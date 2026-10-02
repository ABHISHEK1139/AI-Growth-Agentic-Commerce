import { defineConfig, devices } from "@playwright/test";

/**
 * One merchant session is minted for the whole run in `globalSetup` and replayed
 * from a cookie file, because `POST /api/v1/auth/demo-session` is rate limited to
 * 20 per 5 minutes per IP and this suite has more console specs than that. The
 * rate limit is a real control and is left intact; the suite simply stops
 * re-asking for the same session. See `e2e/global-setup.ts`.
 *
 * `storageState` is read at config-load time, which happens *before*
 * `globalSetup` runs -- so the path is computed here and duplicated in the setup,
 * rather than exported across that boundary.
 *
 * A `__dirname` shim rather than `import.meta.url`: this file is loaded as
 * CommonJS, and `import.meta` is a syntax error there, not a runtime one. The
 * duplicate source of truth is the price and it is cheap -- both sides resolve
 * against `__dirname`, so they cannot disagree about which root they mean.
 */
declare const __dirname: string;

const AUTH_STATE = require("node:path").resolve(
  __dirname,
  "test-results",
  ".auth",
  "merchant.json",
);

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 30 * 1000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000",
    trace: "on-first-retry",
    // Every test starts already signed in as a merchant admin. Specs that need
    // the *unauthenticated* path must clear it explicitly -- see
    // `signOutForTest` in `e2e/session.ts`.
    storageState: AUTH_STATE,
  },
  webServer: {
    // Build first: `start` serves the last production build, which may not
    // exist (or may predate the specs) on a fresh machine or in CI.
    command: "npm run build && npm run start",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 180 * 1000,
    // No BACKEND_URL here, so these specs exercise the in-process backend in
    // `src/app/api/[...path]/route.ts`. That route refuses to serve unless it is
    // explicitly opted in, precisely so a production deployment that forgot
    // BACKEND_URL cannot quietly serve a seed catalog; the suite states the
    // opt-in rather than working around it. See `localBackendAllowed()`.
    env: {
      ALLOW_LOCAL_BACKEND: "1",
    },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
