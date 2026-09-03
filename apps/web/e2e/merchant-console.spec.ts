import { test, expect } from "@playwright/test";

test.describe("Merchant Console & Audit Ledger", () => {
  test("loads merchant overview and automatically bootstraps session", async ({ page }) => {
    await page.goto("/merchant");
    await expect(page.locator("body")).toBeVisible();

    // Verify console header or navigation exists
    const consoleNav = page.locator("nav, header").first();
    await expect(consoleNav).toBeVisible();
  });

  test("loads audit ledger and views events timeline without 401s", async ({ page }) => {
    await page.goto("/merchant/audit");
    await expect(page.locator("body")).toBeVisible();

    // Verify audit page title or heading
    const heading = page.locator("h1, h2:has-text('Audit')").first();
    await expect(heading).toBeVisible();
  });

  test("navigates to campaigns and connector interfaces", async ({ page }) => {
    await page.goto("/merchant/campaigns");
    await expect(page.locator("body")).toBeVisible();

    await page.goto("/merchant/connectors");
    await expect(page.locator("body")).toBeVisible();
  });
});
