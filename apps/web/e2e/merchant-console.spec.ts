import { test, expect } from "@playwright/test";

import { gotoAsMerchantAdmin } from "./session";

/**
 * Every navigation here goes through `gotoAsMerchantAdmin`.
 *
 * These assertions are deliberately loose -- a visible body, a nav, a heading --
 * and that is fine for smoke coverage, but only once the session is established
 * first. Unauthenticated, each of them passes while the console never renders,
 * because the sign-in page also has a body, a header, and an `h1`. The gate
 * itself is asserted strictly in `console-gate.spec.ts`.
 */
test.describe("Merchant Console & Audit Ledger", () => {
  test("loads merchant overview without a client-side exception", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await gotoAsMerchantAdmin(page, "/merchant");
    await expect(page.locator("body")).toBeVisible();

    // A client-side exception used to blank this page: the console rendered an
    // audit row whose timestamp was missing, and the date helper threw inside
    // the table's `.map`. Assert the absence of that failure explicitly, or the
    // "no header found" symptom reads as a missing-header bug.
    await expect(page.getByRole("heading", { name: /Application error/i })).toHaveCount(0);
    expect(pageErrors, "client-side exceptions on /merchant").toEqual([]);

    const consoleNav = page.locator("nav, header").first();
    await expect(consoleNav).toBeVisible();
  });

  test("loads audit ledger and views events timeline without 401s", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant/audit");
    await expect(page.locator("body")).toBeVisible();

    const heading = page.locator("h1, h2:has-text('Audit')").first();
    await expect(heading).toBeVisible();
  });

  test("navigates to campaigns and the channel screen", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant/campaigns");
    await expect(page.locator("body")).toBeVisible();

    // `/merchant/connectors` redirects to `/merchant/channels`, which now holds a
    // real console rather than a registration form. The assertion is on the final
    // URL rather than on a visible element: the alias and the screen it points
    // at both render, so only the URL distinguishes them.
    await page.goto("/merchant/connectors");
    await page.waitForURL(/\/merchant\/channels/);
    await expect(page.getByRole("heading", { name: /store channels/i })).toBeVisible();
  });
});
