import type { APIRequestContext, BrowserContext, Page } from "@playwright/test";

// CommonJS, like the config: `import.meta` is a syntax error in this loader.
declare const __dirname: string;

/**
 * The base URL of the API that is the authority for sessions.
 *
 * The merchant console is gated in middleware, which forwards the session cookie
 * to `GET /api/v1/auth/me` on the API and redirects to `/login?reason=no_session`
 * when the answer says there is no merchant session. A spec that navigates
 * straight to `/merchant/...` therefore lands on the sign-in page, and any
 * assertion phrased loosely enough to match that page - a visible `body`, a
 * `nav, header`, an `h1` - passes without the console ever having rendered. That
 * is how a merchant console test suite can be entirely green while testing
 * nothing.
 *
 * The cookie is set by the API on host `localhost`, and cookies ignore ports, so
 * a session obtained from the API is presented to the web tier on :3000 without
 * any extra plumbing.
 */
const API_BASE_URL = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";

/**
 * Sign in as a merchant admin.
 *
 * A no-op in the normal case -- `playwright.config.ts` already applied the saved
 * session to the context -- so the rate-limited endpoint is not called at all.
 * This exists for specs that need a session *after* having signed out, and it
 * checks before it mints for the same reason.
 *
 * `POST /api/v1/auth/demo-session` issues a session without checking a credential
 * and is refused outside `APP_ENV=local`, which is what these specs run against.
 * It used to be `POST /api/v1/auth/session`; that path is now the
 * credential-checking `POST /api/v1/auth/login`, so the no-credential behaviour
 * moved here deliberately -- a deployment cannot leave "log in as merchant admin"
 * mounted at the URL a human types.
 */
export async function signInAsMerchantAdmin(
  context: BrowserContext,
  api: APIRequestContext
): Promise<void> {
  // The *browser context's* cookies, not the API request context's. Playwright
  // seeds both jars from `storageState` but keeps them separate, and only the
  // browser's is what the middleware sees -- so probing the request context
  // answers a question about the wrong jar. It reported "signed in" for a context
  // whose cookies had just been cleared, and the spec then failed on a redirect
  // with nothing to explain it.
  const hasSession = (await context.cookies()).some(
    (cookie) => cookie.name === "agentpay_session" && cookie.domain.includes("localhost")
  );
  if (hasSession) return;

  // Signed out by a gate spec. Replay the cookie `globalSetup` already obtained
  // rather than asking the rate-limited endpoint for a new one: 20 per 5 minutes
  // is not many, and a suite that mints per test exhausts it partway through.
  if (await restoreSavedSession(context)) return;

  const response = await api.post(`${API_BASE_URL}/api/v1/auth/demo-session`, {
    data: { role: "merchant_admin", subject: "user_merchant_admin" },
  });
  if (!response.ok()) {
    throw new Error(
      `merchant session refused with ${response.status()}: ${await response.text()}`,
    );
  }
  // Minted through `api`, so the cookie landed in *that* jar. Copy it across, or
  // the browser stays signed out and the spec fails confusingly one line later.
  await copySessionCookie(api, context);
}

async function copySessionCookie(
  api: APIRequestContext,
  context: BrowserContext
): Promise<void> {
  for (const cookie of (await api.storageState()).cookies) {
    if (cookie.name !== "agentpay_session") continue;
    await context.addCookies([
      { ...cookie, domain: "localhost", path: "/", sameSite: cookie.sameSite ?? "Lax" },
    ]);
  }
}

/**
 * The saved-state path, derived the same way `playwright.config.ts` derives it.
 *
 * Not read from `process.env`, which is what `globalSetup` sets: global setup
 * runs in the *runner* process, and the specs run in *worker* processes, so a
 * variable exported from there is not reliably visible here. Deriving the path on
 * both sides is the only way they agree.
 */
function savedStatePath(): string {
  return require("node:path").resolve(
    __dirname,
    "..",
    "test-results",
    ".auth",
    "merchant.json",
  );
}

/** Replay the cookie `globalSetup` saved. False when there is nothing to replay. */
async function restoreSavedSession(context: BrowserContext): Promise<boolean> {
  try {
    const fs = await import("node:fs/promises");
    const saved = JSON.parse(await fs.readFile(savedStatePath(), "utf-8")) as {
      cookies?: Parameters<BrowserContext["addCookies"]>[0];
    };
    if (!saved.cookies?.length) return false;
    await context.addCookies(saved.cookies);
    return true;
  } catch {
    return false;
  }
}

/**
 * Drop the session, so the *unauthenticated* path can be tested.
 *
 * The config signs every test in, which is right for the console specs and wrong
 * for the gate specs: a redirect assertion passes trivially when a session is
 * already present, so those tests would be asserting nothing. Clearing the cookie
 * is the explicit opt-out, and it is here rather than inline so the reason it
 * exists is written down next to the code that depends on it.
 */
export async function signOutForTest(context: BrowserContext): Promise<void> {
  await context.clearCookies();
}

/** Sign in and then open a console page, so the page is reached authenticated. */
export async function gotoAsMerchantAdmin(page: Page, path: string): Promise<void> {
  await signInAsMerchantAdmin(page.context(), page.request);
  await page.goto(path);
  // A console route that still redirects is a broken gate, not a broken test, and
  // it must be reported as such rather than as a missing heading further down.
  const url = page.url();
  if (url.includes("/login")) {
    throw new Error(`expected ${path} to render, but it redirected to ${url}`);
  }
}
