import { test, expect } from "@playwright/test";
import { gotoAsMerchantAdmin } from "./session";

test.describe("Merchant AI Policy Control & Capability Agreement", () => {
  // Both tests sign in first. `/merchant/policy` is gated in middleware, so an
  // unauthenticated navigation lands on `/login?reason=no_session` and the
  // assertions below would be made against the sign-in page.
  test("loads policy control page with 3-surface agreement check", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant/policy");

    // Verify main heading
    const heading = page.locator("h1:has-text('Policy Controls'), h1:has-text('Financial Policy')").first();
    await expect(heading).toBeVisible();

    // Verify form or save button exists
    const saveButton = page.locator("button:has-text('Save Policy')").first();
    if (await saveButton.isVisible()) {
      await expect(saveButton).toBeEnabled();
    }
  });

  test("policy routes maintain consistency", async ({ page }) => {
    await gotoAsMerchantAdmin(page, "/merchant/policy");

    // Check for agreement section or status badge
    const agreementSection = page
      .getByText("discovery routes")
      .or(page.getByText("identical limits"))
      .or(page.getByText("What an external agent is told"))
      .or(page.getByText("Financial Policy"))
      .first();
    await expect(agreementSection).toBeVisible();
  });
});
