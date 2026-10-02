/**
 * Who is signed in, and what they may reach.
 *
 * The console has no server-side route protection: the API enforces roles per
 * endpoint, and the frontend discovers a missing session from a 401. That is
 * fine for a public storefront and wrong for an admin area, because a merchant
 * admin landing on `/merchant` with no session sees a page of failed panels
 * rather than a sign-in form. This hook is the fix on the client side: it reads
 * the session once and the layout redirects to `/login` when there is none.
 *
 * It is a *convenience*, not a security control. Everything here is readable by
 * the browser and therefore forgeable; the only real gate is
 * `require_roles` on the API, and this must never be described as protecting
 * anything on its own.
 */

import { useCallback, useEffect, useState } from "react";

import { apiGet, logoutSession } from "@/lib/api";

export interface SessionPrincipal {
  subject: string;
  role: "buyer" | "merchant_admin" | "merchant_operator" | "platform_admin";
  merchant_id: string | null;
  buyer_id: string | null;
  scopes: string[];
}

export interface SessionState {
  loading: boolean;
  principal: SessionPrincipal | null;
  /**
   * True when the API could not be asked, as opposed to answering "not signed
   * in". A 429 or a 5xx leaves the question open, and the layout must not treat
   * that as a signed-out operator -- doing so redirected a valid session to the
   * sign-in form the moment the rate limiter engaged.
   */
  unknown: boolean;
  /** Re-read the session from the server. */
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const CONSOLE_ROLES = new Set([
  "merchant_admin",
  "merchant_operator",
  "platform_admin",
]);

export function canUseConsole(principal: SessionPrincipal | null): boolean {
  return principal !== null && CONSOLE_ROLES.has(principal.role);
}

export function useSession(): SessionState {
  const [loading, setLoading] = useState(true);
  const [unknown, setUnknown] = useState(false);
  const [principal, setPrincipal] = useState<SessionPrincipal | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setUnknown(false);
    const result = await apiGet<{ authenticated: boolean; principal: SessionPrincipal | null }>(
      "/api/v1/auth/me"
    );
    if (result.ok) {
      setPrincipal(result.data.authenticated ? result.data.principal : null);
      setLoading(false);
      return;
    }

    // 401/403 is a real answer: not signed in, or not permitted. A 429 or a 5xx
    // is not -- it is the API being unable to say, and treating that as "signed
    // out" turned a throttled read into a sign-in redirect for an operator with a
    // perfectly valid session. That is the same bug the middleware had, and it
    // showed up as a console that "lost" its session under load.
    //
    // `unknown` is what the layout needs in order to tell "definitely signed out"
    // from "cannot tell yet", which are different things and used to be the same
    // state.
    if (result.error.status === 401 || result.error.status === 403) {
      setPrincipal(null);
    } else {
      setUnknown(true);
    }
    setLoading(false);
  }, []);

  const signOut = useCallback(async () => {
    await logoutSession();
    setPrincipal(null);
    setUnknown(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { loading, principal, unknown, refresh, signOut };
}
