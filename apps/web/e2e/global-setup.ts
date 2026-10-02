import fs from "node:fs/promises";
import path from "node:path";

import type { FullConfig } from "@playwright/test";

/**
 * Mint one merchant session for the whole suite and save it as `storageState`.
 *
 * Why this exists
 * ---------------
 * `POST /api/v1/auth/demo-session` is rate limited to 20 requests per 5 minutes
 * per IP, and the merchant console is gated on it. A suite of ~30 specs that
 * signs in per test therefore exhausts the limit partway through, and every
 * remaining spec fails on a redirect to `/login` -- a dozen failures that all
 * look like broken route gating and none of which are.
 *
 * The rate limit is correct for the endpoint it guards: a no-credential session
 * issuer that can be hammered is a way to mint unlimited sessions. So the fix
 * belongs here rather than in the API. Weakening a production limit to make a
 * test suite pass is how a real limit disappears.
 *
 * What this does instead: one session, taken once, replayed into every browser
 * context from a cookie file. The limit is respected exactly, and the suite stops
 * measuring it.
 *
 * The saved state is written under `test-results/` (git-ignored) rather than into
 * `e2e/`, so a session cookie is never committed.
 *
 * On paths
 * --------
 * `AUTH_STATE` here must equal `AUTH_STATE` in `playwright.config.ts`. The
 * relative path is a trap: `config.rootDir` is the *test directory*, so
 * `path.resolve(config.rootDir, ...)` writes to `e2e/test-results/` while the
 * config reads from `test-results/` -- and every test then fails with ENOENT on
 * a file that demonstrably exists. Resolving from this file's own directory is
 * the only thing that makes the two agree.
 */

const API_BASE_URL = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";

// CommonJS, like the config: `import.meta` is a syntax error in this loader, and
// `__dirname` is what both files already have available.
declare const __dirname: string;

const AUTH_STATE = path.resolve(__dirname, "..", "test-results", ".auth", "merchant.json");

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const { request } = await import("@playwright/test");

  const api = await request.newContext({ baseURL: API_BASE_URL });

  try {
    let response = await api.post("/api/v1/auth/demo-session", {
      data: { role: "merchant_admin", subject: "user_merchant_admin" },
    });

    if (response.status() === 429) {
      // The rate limit is real and stays in place, but a developer re-running the
      // suite repeatedly hits it, and the resulting failure -- "could not mint a
      // merchant session", with no mention of rate limiting -- sends them looking
      // at the wrong thing. `Retry-After` is the server's own statement of how
      // long, so wait exactly that long and try once more.
      const retryAfter = Number(
        (await response.json().catch(() => ({}))).details?.retry_after_seconds ?? 0,
      );
      const waitMs = Math.min(Math.max(retryAfter + 1, 0), 300) * 1000;
      console.log(
        `[e2e] demo session rate limited; waiting ${Math.round(waitMs / 1000)}s before retrying`,
      );
      await new Promise((resolve) => setTimeout(resolve, waitMs));
      response = await api.post("/api/v1/auth/demo-session", {
        data: { role: "merchant_admin", subject: "user_merchant_admin" },
      });
    }

    if (!response.ok()) {
      throw new Error(
        `could not mint a merchant session for the suite: ${response.status()} ${await response.text()}`,
      );
    }

    // Read the cookie the API set. Named explicitly rather than dumping the whole
    // jar, so a future cookie on localhost:8000 does not silently start being
    // replayed into the web tier.
    const cookies = await api.storageState();
    const session = cookies.cookies.find((c) => c.name === "agentpay_session");
    if (!session) {
      throw new Error("the demo session returned no agentpay_session cookie");
    }

    await fs.mkdir(path.dirname(AUTH_STATE), { recursive: true });
    await fs.writeFile(
      AUTH_STATE,
      JSON.stringify({
        cookies: [
          {
            ...session,
            // Cookies ignore ports, so this is belt-and-braces, but it makes the
            // intent explicit and survives a future change that scopes cookies
            // by port.
            domain: "localhost",
            path: "/",
            sameSite: session.sameSite ?? "Lax",
          },
        ],
        origins: [],
      }),
      "utf-8",
    );

    // Exported so a spec that signs itself out can replay the cookie rather than
    // mint a new session. `playwright.config.ts` hardcodes the same path for
    // `storageState`, because config is read before this runs and cannot see a
    // variable exported from here.
    process.env.PLAYWRIGHT_AUTH_STATE = AUTH_STATE;
  } finally {
    await api.dispose();
  }
}
