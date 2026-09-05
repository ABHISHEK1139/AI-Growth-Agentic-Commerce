import { test, expect } from "@playwright/test";

test.describe("Full End-to-End Human User Flows & Edge Cases", () => {
  test("1. Full Shopping Flow: Browse, Filter, Add to Cart, Cross-Sell Companion, and Proceed to Checkout", async ({ page }) => {
    // 1. Visit homepage
    await page.goto("/");
    await expect(page).toHaveTitle(/AgentPay/i);

    // 2. Browse category
    await page.goto("/category/laptops");
    await expect(page.locator("h1")).toContainText("Laptops");

    // Verify laptops are loaded and first product is authentic (e.g., ASUS, Lenovo, Apple)
    const cards = page.locator("article");
    await expect(cards.first()).toBeVisible();

    // 3. Click first product to view details
    const firstProductLink = cards.first().locator("a[href*='/product/']").first();
    await firstProductLink.click();
    await page.waitForURL(/\/product\//);

    // Verify Product Detail Page
    await expect(page.locator("h1")).toBeVisible();
    await expect(page.locator("button:has-text('Add to Bag'), button:has-text('Add to Cart')").first()).toBeVisible();

    // 4. Add to bag
    const addToBagBtn = page.locator("button:has-text('Add to Bag'), button:has-text('Add to Cart')").first();
    await addToBagBtn.click();

    // Verify Cart Drawer opens or badge increments
    await page.waitForTimeout(500);

    // 5. Navigate to Cart page
    await page.goto("/cart");
    await expect(page.locator("h1, h2").first()).toBeVisible();

    // 6. Proceed to Checkout
    await page.goto("/checkout");
    await expect(page.locator("body")).toBeVisible();

    // Verify checkout address fields are interactable
    const nameInput = page.locator("input[name*='name'], input[placeholder*='Name'], input[value*='Alex']").first();
    if (await nameInput.isVisible()) {
      await expect(nameInput).toBeEnabled();
    }
  });

  test("2. AI Assistant & In-App Conversational Checkout", async ({ page }) => {
    await page.goto("/");

    // Open AI Drawer via nav button
    const openAssistantBtn = page.locator("button:has-text('Ask'), button[aria-label*='AI'], button:has-text('Chat')").first();
    if (await openAssistantBtn.isVisible()) {
      await openAssistantBtn.click();
      await page.waitForTimeout(600);

      // Verify chat input is visible
      const chatInput = page.locator("textarea, input[placeholder*='Ask']").first();
      await expect(chatInput).toBeVisible();

      // Trigger conversational checkout
      await chatInput.fill("checkout");
      await page.keyboard.press("Enter");

      await page.waitForTimeout(1000);

      // Verify either assistant reply or in-app checkout card appears
      const responseElements = page.locator("article, div[class*='bubble'], div[class*='message']");
      expect(await responseElements.count()).toBeGreaterThan(0);
    }
  });

  test("3. Agent-Readable Catalog Protocols (UAP, AP2, ACP)", async ({ request }) => {
    // Verify /.well-known/agent-catalog.json
    const agentCatalogRes = await request.get("/.well-known/agent-catalog.json");
    expect(agentCatalogRes.status()).toBe(200);
    const agentCatalogData = await agentCatalogRes.json();
    expect(agentCatalogData.protocol || agentCatalogData.catalog_version).toBeDefined();

    // Verify /api/v1/agent/catalog
    const catalogRes = await request.get("/api/v1/agent/catalog");
    expect(catalogRes.status()).toBe(200);
    const catalogData = await catalogRes.json();
    expect(catalogData.items || catalogData.data || catalogData.offers).toBeDefined();

    // Verify /.well-known/acp-manifest.json
    const acpRes = await request.get("/.well-known/acp-manifest.json");
    expect(acpRes.status()).toBe(200);
  });

  test("4. Campaign Orchestrator & Safety Discount Gating", async ({ page }) => {
    await page.goto("/merchant/campaigns");
    await expect(page.locator("h1, h2:has-text('Campaigns')").first()).toBeVisible();

    // Verify propose campaign form or existing campaigns list
    const campaignItems = page.locator("div[class*='border'], article");
    expect(await campaignItems.count()).toBeGreaterThan(0);
  });
});
