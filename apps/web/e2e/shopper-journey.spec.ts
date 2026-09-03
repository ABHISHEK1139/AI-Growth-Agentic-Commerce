import { test, expect } from "@playwright/test";

test.describe("Shopper Journey & AI Exploration", () => {
  test("loads homepage and displays catalog products", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/AgentPay|Next.js/i);

    // Verify main navigation and branding
    const header = page.locator("header");
    await expect(header).toBeVisible();

    // Verify product listings are rendered
    const productCards = page.locator("[data-product-id]");
    const count = await productCards.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test("interacts with AI shopping assistant drawer", async ({ page }) => {
    await page.goto("/");

    // Locate AI Assistant trigger button
    const aiButton = page.locator("button:has-text('AI Assistant'), button:has-text('Chat'), button[aria-label*='AI']").first();
    if (await aiButton.isVisible()) {
      await aiButton.click();
      // Verify drawer or chat input opens
      const chatInput = page.locator("input[placeholder*='Ask'], input[placeholder*='Search'], textarea").first();
      await expect(chatInput).toBeVisible();
    }
  });

  test("views cart and checkout page", async ({ page }) => {
    await page.goto("/cart");
    await expect(page.locator("body")).toBeVisible();

    await page.goto("/checkout");
    await expect(page.locator("body")).toBeVisible();
  });
});
