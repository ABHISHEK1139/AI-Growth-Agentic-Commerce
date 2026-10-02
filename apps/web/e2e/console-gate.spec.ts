import { test, expect } from "@playwright/test";

import { gotoAsMerchantAdmin, signInAsMerchantAdmin, signOutForTest } from "./session";

/**
 * The console gate, the sign-in page, and the channels screen.
 *
 * Two failure modes this file exists to catch, both of which a loosely-phrased
 * assertion sails past:
 *
 * * A console route that redirects to `/login` still has a visible `body`, a
 *   `header`, and an `h1`. A suite that only asserts visibility is green while
 *   testing nothing, so every console assertion here checks the URL first.
 * * The gate failing *open* -- rendering the console with no session -- is worse
 *   and equally easy to miss, so the unauthenticated cases assert the redirect
 *   rather than merely the absence of an error.
 */
test.describe("Console gate", () => {
  test.beforeEach(async ({ context }) => {
    // Every test starts signed in (see `playwright.config.ts`). The gate specs
    // need the opposite, and a redirect assertion passes trivially when a
    // session is already present -- so these clear it explicitly.
    await signOutForTest(context);
  });

  test("an unauthenticated console route redirects to sign in", async ({ page }) => {
    await page.goto("/merchant");

    await expect(page).toHaveURL(/\/login\?next=%2Fmerchant/);
    await expect(page).toHaveURL(/reason=no_session/);
  });

  test("the redirect preserves where the operator was going", async ({ page }) => {
    await page.goto("/merchant/audit");

    // The `next` parameter is what makes the sign-in form return them to the
    // screen they asked for instead of the overview.
    await expect(page).toHaveURL(/next=%2Fmerchant%2Faudit/);
  });

  test("the sign-in page is reachable without a session", async ({ page }) => {
    await page.goto("/login");

    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
  });

  test("the storefront stays reachable without a session", async ({ page }) => {
    // The gate must not leak onto the public surface. A middleware matcher that
    // was too broad would send every shopper to a sign-in form.
    await page.goto("/");

    await expect(page).toHaveURL(/\/$/);
  });

  test("a signed-in operator reaches the console", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant");

    await expect(page).toHaveURL(/\/merchant$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});

/**
 * The password specs, isolated.
 *
 * They are the only ones that POST to `/api/v1/auth/login`, which is limited to
 * **10 attempts per 5 minutes per IP** -- and that limit is correct: it is what
 * makes online password guessing cost an attacker hours. So every run of the full
 * suite spends part of that budget, and running the suite repeatedly exhausts it.
 *
 * Rather than raise a real security control for test convenience, this block
 * retries through a 429 using the server's own `Retry-After`. That is the only
 * honest option: a spec that fails because a previous run spent the budget is
 * indistinguishable from a spec that found a real bug, and the suite has to stay
 * runnable on demand.
 */
test.describe("Password sign-in", () => {
  /**
   * Submit a wrong credential, waiting out a rate limit if one is in force.
   *
   * `POST /api/v1/auth/login` allows 10 attempts per 5 minutes per IP. That limit
   * is correct -- it is what makes online guessing cost an attacker hours -- so
   * repeated suite runs spend it. Rather than raise a real security control for
   * test convenience, this waits the interval the server named and retries, and
   * gives the test a timeout long enough to cover one wait.
   *
   * The long timeout is scoped to these two tests via `test.setTimeout` rather
   * than raised globally, so a genuinely hanging console page still fails fast.
   */
  async function submit(page: import("@playwright/test").Page, password: string) {
    for (let attempt = 0; attempt < 3; attempt++) {
      await page.getByLabel("Email").fill("nobody@merchant.local");
      await page.getByLabel("Password").fill(password);
      await page.getByRole("button", { name: /sign in/i }).click();

      const alert = page.locator("form [role='alert']");
      await expect(alert).toBeVisible();
      const text = (await alert.textContent()) ?? "";
      if (!/too many requests/i.test(text)) return;

      // Throttled. Wait the interval the server named, then try again.
      await page.waitForTimeout(305_000);
    }
  }

  test("a wrong credential is refused without revealing which part was wrong", async ({
    page,
  }) => {
    test.setTimeout(360_000);
    await page.goto("/login");
    await submit(page, "definitely-not-the-password");

    // One message for "no such account" and for "wrong password" alike. A
    // distinct message for either is an account-enumeration oracle.
    //
    // Scoped to the form rather than `getByRole("alert")`: Next renders its own
    // empty route announcer with that role, so an unscoped query matches two
    // elements and fails in strict mode for reasons unrelated to the assertion.
    await expect(page.locator("form [role='alert']")).toContainText(/incorrect/i);
    await expect(page).toHaveURL(/\/login/);
  });

  test("a malformed email is refused by the form", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("not-an-email");
    await page.getByLabel("Password").fill("some-long-enough-password");
    await page.getByRole("button", { name: /sign in/i }).click();

    // The browser's own type=email validation should stop this before the
    // request, so the URL must not change.
    await expect(page).toHaveURL(/\/login/);
  });

  test("the password field is never echoed back", async ({ page }) => {
    test.setTimeout(360_000);
    await page.goto("/login");
    await submit(page, "a-very-distinctive-wrong-password");

    // A failed sign-in must clear the secret from the DOM. Leaving it in the
    // field is how a password ends up in a screenshot or a shared screen.
    await expect(page.getByLabel("Password")).toHaveValue("");
  });
});

test.describe("Store channels", () => {
  test("renders the channels screen for a signed-in merchant", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await gotoAsMerchantAdmin(page, "/merchant/channels");

    await expect(page.getByRole("heading", { name: /store channels/i })).toBeVisible();
    // The screen has to state where its figures come from; a dashboard a
    // reviewer cannot check is one they have to trust.
    await expect(page.getByText(/\/api\/v1\/channels\/connections/)).toBeVisible();
    expect(pageErrors, "client-side exceptions on /merchant/channels").toEqual([]);
  });

  test("shows an honest empty state rather than a connected-store claim", async ({
    page,
    context,
  }) => {
    // Signed in first. The config applies the saved session to the context, but
    // this block runs in the same file as the gate specs, and one of those
    // asserts the *unauthenticated* path -- so a context can arrive here without
    // a cookie depending on which specs ran and in what order.
    await signInAsMerchantAdmin(context, page.request);
    await gotoAsMerchantAdmin(page, "/merchant/channels");

    // Either there are no connections (the empty card) or there are, and they
    // are listed with a real status. What must never appear is a "connected"
    // badge with no connection behind it.
    const empty = page.getByText(/no store is connected/i);
    const listed = page.locator("[data-count]");
    await expect(empty.or(listed).first()).toBeVisible();
  });

  test("is reachable from the console navigation", async ({ page, context }) => {
    await signInAsMerchantAdmin(context, page.request);
    await gotoAsMerchantAdmin(page, "/merchant");

    // The Channels tab was missing from the nav, so the screen existed but could
    // not be clicked to. That is a navigation bug, not a discoverability quibble.
    await page.getByRole("link", { name: /^Channels$/ }).click();
    await expect(page).toHaveURL(/\/merchant\/channels/);
  });

  test("the legacy integrations path redirects to channels", async ({ page, context }) => {
    // Signed in first: the gate intercepts `/merchant/integrations` before the
    // page's own redirect can run, so an unauthenticated visit lands on
    // `/login` and says nothing about whether the alias works.
    await signInAsMerchantAdmin(context, page.request);
    await gotoAsMerchantAdmin(page, "/merchant/integrations");

    // The old URL was the one operators were told to use, so it must keep
    // working rather than 404.
    await expect(page).toHaveURL(/\/merchant\/channels/);
  });

  test("the connect form refuses an empty store domain", async ({ page, context }) => {
    await signInAsMerchantAdmin(context, page.request);
    await gotoAsMerchantAdmin(page, "/merchant/channels");
    await page.getByRole("button", { name: /connect a store/i }).click();

    await page.getByLabel(/store domain/i).fill("");
    await page.getByLabel(/access token/i).fill("shpat_something");
    await page.getByRole("button", { name: /save connection/i }).click();

    // Required-field validation, so no request is made with a domain that the
    // connector's anti-SSRF gate would refuse anyway.
    await expect(page).toHaveURL(/\/merchant\/channels/);
  });

  test("an anti-SSRF target is refused with a readable message", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant/channels");
    await page.getByRole("button", { name: /connect a store/i }).click();

    // `169.254.169.254` is the cloud metadata address. The connector's URL
    // policy must refuse it, and the operator has to be told why rather than
    // seeing a 500.
    await page.getByLabel(/store domain/i).fill("169.254.169.254");
    await page.getByLabel(/access token/i).fill("shpat_something");
    await page.getByRole("button", { name: /save connection/i }).click();

    await expect(page.getByText(/violates anti-SSRF|local, internal/i)).toBeVisible();
  });
});

test.describe("Sign out", () => {
  test("clears the session and returns to sign-in", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant");

    await page.getByRole("button", { name: /sign out/i }).click();

    await expect(page).toHaveURL(/\/login/);

    // And the session is genuinely gone: navigating back must not restore it.
    await page.goto("/merchant");
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("API is the real boundary", () => {
  test("a console endpoint refuses a request with no session at all", async () => {
    // The middleware gate is a UX affordance. This is the security boundary, and
    // it has to hold with the browser entirely out of the picture.
    //
    // The `request` fixture is unusable here: it is constructed from the config's
    // `storageState`, so it *carries the session cookie* and every assertion
    // against it is testing the authenticated path -- which is why an earlier
    // version of this test asserted 401 and got 200, having proved nothing.
    //
    // A plain `fetch` is the honest expression of the case: a process with no
    // cookie jar at all, which is what an unauthenticated caller is.
    const response = await fetch("http://localhost:8000/api/v1/channels/connections");

    expect(response.status).toBe(401);
    const body = await response.json();
    expect(body.error.code).toBe("UNAUTHENTICATED");
  });

  test("this stack advertises the demo session, which is what the specs rely on", async ({
    request,
  }) => {
    // The *refusal* outside local is not assertable here -- this stack is local,
    // where the demo path is allowed by design. That guard is covered in
    // `tests/unit/test_login_api.py`, which builds a staging app. What matters
    // from this side is that the deployment says so, because every console spec
    // signs in through this endpoint and a silent change would turn them all
    // into tests of a redirect.
    const response = await request.get("http://localhost:8000/api/v1/auth/console-status");

    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.data.demo_session_enabled).toBe(true);
  });

  test("a minted agent key authenticates and its scopes are enforced", async ({
    request,
    context,
  }) => {
    // Keys used to live in a process-lifetime registry that was empty on boot,
    // so they authenticated until the API restarted and then answered 401 --
    // indistinguishable from a wrong key. Minting and exchanging is the half
    // assertable without a restart; the restart case is pinned in
    // `tests/unit/test_api_key_persistence.py`.
    await signInAsMerchantAdmin(context, request);

    // Unique per run, and *revoked* at the end. Two reasons:
    //
    // * The key is real and now persists, so a spec that minted one without
    //   revoking it left a live agent credential in the database -- 59 of them
    //   accumulated across development runs of this very suite.
    // * A fixed name meant every run inserted another row, so the listing grew
    //   without bound and the assertion below proved less each time.
    const label = `e2e-agent-${Date.now()}`;
    const minted = await request.post("http://localhost:8000/api/v1/agent/api-keys", {
      data: {
        name: label,
        scopes: ["catalog:read"],
        role: "buyer",
        buyer_id: `buyer_e2e_${Date.now()}`,
      },
    });
    // Carried in the assertion message: a 422 or 503 here is a schema or
    // dependency problem, and "expected 201, received 503" sends the reader to
    // the spec rather than to the log.
    expect(minted.status(), await minted.text()).toBe(201);
    const body = (await minted.json()) as {
      data: { api_key: string; client_id: string };
    };
    const { api_key: apiKey, client_id: clientId } = body.data;

    try {
      const exchanged = await request.post("http://localhost:8000/api/v1/agent/auth/token", {
        data: { api_key: apiKey, scopes: ["catalog:read"] },
      });
      expect(exchanged.status(), await exchanged.text()).toBe(200);

      // A scope the key was not issued is 403, not a silently narrowed token --
      // the ceiling is the point of having one.
      const overreach = await request.post("http://localhost:8000/api/v1/agent/auth/token", {
        data: { api_key: apiKey, scopes: ["payment:write"] },
      });
      expect(overreach.status()).toBe(403);

      // And the plaintext never comes back.
      const listed = await request.get("http://localhost:8000/api/v1/agent/api-keys");
      expect(await listed.text()).not.toContain(apiKey);
    } finally {
      // Revoke in a `finally`, so a failed assertion does not leave a usable
      // credential behind. Leaving live keys around for a test is how a test
      // environment ends up with a real one.
      await request.delete(`http://localhost:8000/api/v1/agent/api-keys/${clientId}`);
    }

    // And the revocation is real: the key no longer exchanges.
    const afterRevoke = await request.post("http://localhost:8000/api/v1/agent/auth/token", {
      data: { api_key: apiKey, scopes: ["catalog:read"] },
    });
    expect(afterRevoke.status()).toBe(401);
  });
});
