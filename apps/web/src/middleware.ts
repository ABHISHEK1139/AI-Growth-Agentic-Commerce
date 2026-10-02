/**
 * Server-side route gate for the merchant console.
 *
 * Why this exists
 * ---------------
 * `/merchant/**` holds the administrative surface: policy controls, the audit
 * ledger, catalog import, channel credentials. Before this file, every one of
 * those routes was reachable by URL, and the only enforcement was per-endpoint
 * `require_roles` in the API. That is sound as a security boundary -- the API
 * still refuses everything without a valid session -- but it produced a bad
 * experience and a bad audit story:
 *
 * * a merchant admin landing on `/merchant` with no session got a page of failed
 *   panels instead of a sign-in form, and
 * * a request for merchant data was served (as 401) to anyone who asked for it.
 *
 * The client-side check in `app/merchant/layout.tsx` fixes the first problem and
 * does nothing for the second, because it runs after the HTML and the requests
 * have already been made.
 *
 * How it works
 * ------------
 * The session token is a compact JWS signed with `SESSION_SECRET`, so the
 * signature, the token type, and the expiry are all verified **here**, in the
 * Edge runtime, with WebCrypto. No network call.
 *
 * That is not an optimisation. The first version of this file asked
 * `GET /api/v1/auth/me` on every navigation, and because that endpoint is rate
 * limited and a console page makes several API calls per navigation, a burst
 * earned a 429 -- which the gate read as "signed out" and turned into a
 * signed-in operator being logged out of their own console. Asking the gateway
 * about a credential it can answer locally was the source of the bug.
 *
 * What this is NOT
 * ----------------
 * This is defence in depth, not the security boundary. Every API endpoint calls
 * `require_roles` regardless of what this file decides, so a wrong decision here
 * can at worst render the wrong shell, never grant an action. The reverse is not
 * true: bypassing this file entirely still meets `require_roles` everywhere.
 *
 * Two consequences of verifying locally are worth stating plainly, because they
 * are the trade:
 *
 * * **Revocation is not consulted.** A token issued before an operator was
 *   disabled keeps opening the console until it expires. The session TTL bounds
 *   that window, and revoking the *API* access is immediate regardless, because
 *   that is checked per request.
 * * **The web tier needs `SESSION_SECRET`.** A mismatch makes every signature
 *   check fail and the console unreachable, which is why compose requires the
 *   variable rather than defaulting it: a silently wrong value produces a
 *   redirect loop, not an error.
 *
 * On failure
 * ----------
 * If the token cannot be verified -- no secret configured, a malformed or expired
 * token, a bad signature -- this **fails closed** and redirects to sign-in.
 * Failing open would mean a configuration mistake silently opening the console
 * shell to anyone.
 */

import { NextResponse, type NextRequest } from "next/server";

import { CONSOLE_ROLES } from "@/lib/middleware-roles";
import { verifySessionToken } from "@/lib/sessionToken";

/** Prefixes behind the console gate. */
const PROTECTED_PREFIXES = ["/merchant", "/scenarios", "/agent/playground"];

/** Never gated: the sign-in page and the auth endpoints it calls. */
const PUBLIC_PATHS = new Set([
  "/login",
  "/logout",
  "/auth/session",
  "/auth/refresh",
]);

/** Session cookie name. Mirrors `SESSION_COOKIE_NAME` in `apps/api/auth.py`. */
const SESSION_COOKIE = "agentpay_session";


function isProtected(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return false;
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
}

type SessionProbe =
  | { kind: "authenticated"; role: string }
  | { kind: "anonymous" }
  | { kind: "indeterminate" };

async function probeSession(request: NextRequest): Promise<SessionProbe> {
  const cookie = request.cookies.get(SESSION_COOKIE)?.value;
  if (!cookie) {
    // No cookie at all is a definite answer, not an indeterminate one. Short of
    // it so an unauthenticated visitor does not pay for a signature check.
    return { kind: "anonymous" };
  }

  // The session token is a compact JWS signed with SESSION_SECRET, so the
  // signature, the token type, and the expiry are all checkable here with
  // WebCrypto. No network call, which is the entire point: an earlier version
  // asked `GET /api/v1/auth/me` on every navigation, and since that endpoint is
  // rate limited and a console page makes several calls per navigation, a burst
  // earned a 429 -- which the gate read as "signed out" and turned into a signed-in
  // operator being logged out of their own console.
  const verdict = await verifySessionToken(cookie, process.env.SESSION_SECRET);

  if (verdict.kind === "invalid") return { kind: "anonymous" };
  if (verdict.kind === "unknown") {
    // No secret configured, so nothing can be verified. Fails closed: a
    // configuration mistake must not silently open the console to anyone.
    return { kind: "indeterminate" };
  }
  if (!CONSOLE_ROLES.has(verdict.role)) {
    // A valid session with the wrong role. The caller sends them to their account
    // rather than a sign-in form they cannot satisfy.
    return { kind: "authenticated", role: verdict.role };
  }
  return { kind: "authenticated", role: verdict.role };
}

function signInRedirect(request: NextRequest, reason: string): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  // `next` so the operator lands where they were going, rather than having to
  // navigate back. Validated on read in the login page: an attacker-supplied
  // absolute URL here would be an open redirect.
  url.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url);
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  const { pathname } = request.nextUrl;

  if (!isProtected(pathname)) {
    return NextResponse.next();
  }

  const probe = await probeSession(request);

  if (probe.kind === "authenticated") {
    if (CONSOLE_ROLES.has(probe.role)) {
      return NextResponse.next();
    }
    // A valid session, wrong role. A buyer is not a broken admin: they get the
    // storefront, not a sign-in form they cannot satisfy.
    const url = request.nextUrl.clone();
    url.pathname = "/account";
    url.search = "";
    url.searchParams.set("reason", "insufficient_role");
    return NextResponse.redirect(url);
  }

  return signInRedirect(request, probe.kind === "indeterminate" ? "gateway_unavailable" : "no_session");
}

export const config = {
  /**
   * Only the console paths. Matching everything would add a request to every
   * storefront navigation, which is the one surface that must stay fast and must
   * keep working when the gateway is down.
   */
  matcher: ["/merchant/:path*", "/scenarios", "/agent/playground"],
};
