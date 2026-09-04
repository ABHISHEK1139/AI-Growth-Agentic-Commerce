import { NextRequest, NextResponse } from "next/server";
import { GoogleGenAI } from "@google/genai";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";

function getAiClient(): GoogleGenAI | null {
  const apiKey = (process.env.GEMINI_API_KEY || "").trim();
  if (!apiKey) return null;
  return new GoogleGenAI({
    apiKey,
    httpOptions: {
      headers: {
        "User-Agent": "aistudio-build",
      },
    },
  });
}

export type GeminiRole = "concierge" | "hardware_specialist" | "merchant_auditor" | "custom";
export type ModelTier = "auto" | "gemini-3.5-flash" | "gemini-3.1-flash-lite" | "gemini-3.1-pro-preview";

export interface ChatHistoryItem {
  role: "user" | "model";
  text: string;
}

const ROLE_SYSTEM_INSTRUCTIONS: Record<GeminiRole, string> = {
  concierge: `You are the Official Personal Shopping Concierge for our premium electronics and gadgets catalog.
Your mission is to help shoppers discover the exact right product for their needs and budget.
Maintain a warm, refined, highly knowledgeable tone.
Provide clear, honest pros and cons, explain price-to-performance tradeoffs, and recommend matching accessories where helpful.
Always format currency in Indian Rupees (₹) with proper comma separation.
When referencing products from our catalog, use their exact model name so the user can easily locate or add them to cart.`,

  hardware_specialist: `You are a Senior Hardware Architect and Benchmarking Specialist.
Your mission is to perform deep technical evaluations of laptop architectures, monitors, thermal envelopes, ports, and peripheral gear.
Analyze real-world developer workloads (Docker containers, compilation times, memory pressure with 16GB vs 32GB RAM), color space fidelity (100% sRGB vs 95% DCI-P3), and interface standards (Thunderbolt 4 vs USB 3.2 Gen 2, HDMI 2.0 vs 2.1).
Deliver precise, technically rigorous, objective assessments with structured tables when comparing multiple models.`,

  merchant_auditor: `You are an Autonomous Commerce Risk Officer and Merchant Policy Auditor.
Your mission is to advise merchant store administrators on transaction guardrails, automated spending thresholds, checkout approval rules, campaign return-on-ad-spend (RoAS), and audit ledger compliance.
Provide clear risk assessment scoring, mitigation steps, and policy configuration advice.`,

  custom: `You are an adaptable AI shopping and technical commerce assistant. Follow the shopper's instructions precisely while remaining objective, accurate, and helpful.`,
};

function buildCatalogSummary(): string {
  return ALL_PRODUCTS.slice(0, 30)
    .map(
      (p) =>
        `- [ID: ${p.id}] ${p.title} | Brand: ${p.brand} | Category: ${p.category} | Price: ₹${(p.priceMinor / 100).toLocaleString("en-IN")} | Rating: ${p.rating}/5 | Key Specs: ${p.shortSpecs || "Standard specifications"}`
    )
    .join("\n");
}

function detectTaskModel(
  query: string,
  history: ChatHistoryItem[],
  preference?: ModelTier
): { model: "gemini-3.5-flash" | "gemini-3.1-flash-lite" | "gemini-3.1-pro-preview"; reasoning: string } {
  if (preference && preference !== "auto") {
    return {
      model: preference,
      reasoning: `User explicitly selected ${preference}`,
    };
  }

  const qLower = query.toLowerCase();

  // 1. FAST TASKS -> gemini-3.1-flash-lite
  // Short queries, spec lookups, fast checks, greetings
  const isFastTask =
    query.trim().split(/\s+/).length <= 5 ||
    qLower.startsWith("price of") ||
    qLower.startsWith("is it in stock") ||
    qLower.startsWith("how much is") ||
    qLower.startsWith("hi") ||
    qLower.startsWith("hello") ||
    qLower.includes("quick question") ||
    qLower.includes("dimensions") ||
    qLower.includes("weight") ||
    qLower.includes("warranty period");

  if (isFastTask) {
    return {
      model: "gemini-3.1-flash-lite",
      reasoning: "Categorized as fast task (quick spec/price query) -> gemini-3.1-flash-lite",
    };
  }

  // 2. COMPLEX TASKS -> gemini-3.1-pro-preview
  // Deep architectural breakdown, multi-system benchmarks, developer workloads, procurement math
  const isComplexTask =
    qLower.includes("architecture") ||
    qLower.includes("benchmark") ||
    qLower.includes("deep comparison") ||
    qLower.includes("docker") ||
    qLower.includes("virtualization") ||
    qLower.includes("thermal throttling") ||
    qLower.includes("vrm") ||
    qLower.includes("compile time") ||
    qLower.includes("risk assessment") ||
    qLower.includes("procurement strategy") ||
    (qLower.includes("vs") && qLower.includes("detailed breakdown")) ||
    history.length >= 6; // Long multi-turn context

  if (isComplexTask) {
    return {
      model: "gemini-3.1-pro-preview",
      reasoning: "Categorized as complex task (deep specs/benchmarks/architecture) -> gemini-3.1-pro-preview",
    };
  }

  // 3. GENERAL TASKS -> gemini-3.5-flash
  return {
    model: "gemini-3.5-flash",
    reasoning: "Categorized as general conversational shopping & recommendations -> gemini-3.5-flash",
  };
}

function generateCatalogFallback(
  query: string,
  role: GeminiRole,
  activeProduct?: ProductItem
): {
  answer: string;
  matchedProducts: ProductItem[];
  followUps: string[];
} {
  const qLower = query.toLowerCase();

  // Find products matching keywords
  const matched = ALL_PRODUCTS.filter((p) => {
    const title = p.title.toLowerCase();
    const brand = p.brand.toLowerCase();
    const cat = p.category.toLowerCase();
    const specs = (p.shortSpecs || "").toLowerCase();
    return (
      qLower.includes(brand) ||
      qLower.includes(cat) ||
      (qLower.includes("laptop") && cat.includes("laptop")) ||
      (qLower.includes("phone") && cat.includes("phone")) ||
      (qLower.includes("headphone") && cat.includes("audio")) ||
      (qLower.includes("earbuds") && cat.includes("audio")) ||
      (qLower.includes("monitor") && cat.includes("monitor")) ||
      (qLower.includes("keyboard") && (cat.includes("keyboard") || cat.includes("accessory"))) ||
      (qLower.includes("oled") && (title.includes("oled") || specs.includes("oled"))) ||
      (qLower.includes("4k") && (title.includes("4k") || specs.includes("4k"))) ||
      (qLower.includes("gaming") && (title.includes("rtx") || specs.includes("gaming"))) ||
      title.split(" ").some((w) => w.length > 3 && qLower.includes(w))
    );
  });

  const selectedProducts = matched.length > 0 ? matched.slice(0, 3) : ALL_PRODUCTS.slice(0, 3);
  const top = selectedProducts[0];

  let answer = "";
  if (activeProduct) {
    answer = `Here is what you should know about the **${activeProduct.title}**:\n\n` +
      `• **Current Price:** ₹${(activeProduct.priceMinor / 100).toLocaleString("en-IN")}\n` +
      `• **Rating:** ★ ${activeProduct.rating} / 5 (${activeProduct.reviewCount} customer reviews)\n` +
      `• **Hardware Overview:** ${activeProduct.shortSpecs || activeProduct.whyFitsYou?.summary || "Engineered for high performance and durability."}\n` +
      `• **Delivery & Returns:** ${activeProduct.deliveryDays}-day delivery guaranteed with ${activeProduct.returnDays}-day hassle-free return window.\n\n` +
      `Would you like to compare this with similar models or add it to your bag?`;
  } else if (qLower.includes("under") || qLower.includes("budget") || qLower.includes("cheap")) {
    const sortedByPrice = [...selectedProducts].sort((a, b) => a.priceMinor - b.priceMinor);
    const bestBudget = sortedByPrice[0];
    answer = `Based on your budget criteria, here are verified options from our catalog:\n\n` +
      `• **Top Value Match:** **${bestBudget.title}** at **₹${(bestBudget.priceMinor / 100).toLocaleString("en-IN")}**\n` +
      `  - Key features: ${bestBudget.shortSpecs}\n` +
      `  - Customer rating: ★ ${bestBudget.rating} (${bestBudget.reviewCount} reviews)\n` +
      `  - Stock: ${bestBudget.stock} units ready for express dispatch\n\n` +
      `Would you like to compare this with higher-tier models or proceed to secure checkout?`;
  } else {
    answer = `I checked our verified store catalog for **"${query}"**. Here are the top matching products:\n\n` +
      selectedProducts.map((p, idx) =>
        `**${idx + 1}. ${p.title}**\n` +
        `• Price: **₹${(p.priceMinor / 100).toLocaleString("en-IN")}** | Rating: ★ ${p.rating}/5 (${p.reviewCount} reviews)\n` +
        `• Key specs: ${p.shortSpecs || p.brand}\n` +
        `• Delivery: Within ${p.deliveryDays} days with verified merchant guarantee`
      ).join("\n\n") +
      `\n\nYou can click any product card below to view full specifications, compare side-by-side, or add to your shopping bag.`;
  }

  const followUps = [
    `Compare ${top.brand} with alternatives`,
    `Check ports and battery life for ${top.title.slice(0, 24)}...`,
    `Add ${top.title.slice(0, 20)}... to bag`,
  ];

  return { answer, matchedProducts: selectedProducts, followUps };
}

export async function POST(req: NextRequest) {
  const startTime = Date.now();
  try {
    const body = await req.json();
    const {
      message,
      history = [],
      role = "concierge",
      customSystemInstruction,
      modelPreference = "auto",
      activeProductId,
    } = body;

    if (!message || typeof message !== "string" || !message.trim()) {
      return NextResponse.json({ ok: false, error: "Message is required" }, { status: 400 });
    }

    const typedRole = (["concierge", "hardware_specialist", "merchant_auditor", "custom"].includes(role)
      ? role
      : "concierge") as GeminiRole;

    const baseInstruction =
      typedRole === "custom" && customSystemInstruction
        ? customSystemInstruction
        : ROLE_SYSTEM_INSTRUCTIONS[typedRole];

    const catalogContext = `\n\n--- CURRENT VERIFIED STORE CATALOG ---\n${buildCatalogSummary()}\n---------------------------------------\nIf the shopper asks for recommendations or specific items, prioritize matching these verified catalog products. Mention their exact titles and current prices in ₹.`;

    let activeProductContext = "";
    const activeProd = activeProductId ? ALL_PRODUCTS.find((p) => p.id === activeProductId) : undefined;
    if (activeProd) {
      activeProductContext = `\n\n--- CURRENTLY VIEWED PRODUCT ---\nTitle: ${activeProd.title}\nBrand: ${activeProd.brand}\nPrice: ₹${(activeProd.priceMinor / 100).toLocaleString("en-IN")}\nCategory: ${activeProd.category}\nRating: ${activeProd.rating} / 5 (${activeProd.reviewCount} reviews)\nSummary: ${activeProd.whyFitsYou?.summary || activeProd.shortSpecs}\nSpecs: ${JSON.stringify(activeProd.specsGrouped || activeProd.shortSpecs)}\nStock: ${activeProd.stock} units available\n---------------------------------`;
    }

    const fullSystemInstruction = `${baseInstruction}${activeProductContext}${catalogContext}`;
    const { model: targetModel, reasoning } = detectTaskModel(message, history, modelPreference);

    const aiClient = getAiClient();
    let activeModelUsed: string = targetModel;
    let fallbackNotice: string | null = null;
    let responseText = "";
    let matchedProducts: ProductItem[] = [];
    let followUps: string[] = [];

    if (!aiClient) {
      // Offline / Keyless Catalog Intelligence Fallback
      const fb = generateCatalogFallback(message, typedRole, activeProd);
      responseText = fb.answer;
      matchedProducts = fb.matchedProducts;
      followUps = fb.followUps;
      activeModelUsed = "catalog-intelligence-engine";
      fallbackNotice = "Responded via verified store catalog engine.";
    } else {
      // Format history for multi-turn chat
      const contents: Array<{ role: "user" | "model"; parts: Array<{ text: string }> }> = [];
      const recentHistory = (history as ChatHistoryItem[]).slice(-10);
      for (const item of recentHistory) {
        if (item.text && item.text.trim()) {
          contents.push({
            role: item.role === "user" ? "user" : "model",
            parts: [{ text: item.text.trim() }],
          });
        }
      }
      contents.push({
        role: "user",
        parts: [{ text: message.trim() }],
      });

      try {
        const response = await aiClient.models.generateContent({
          model: targetModel,
          contents,
          config: {
            systemInstruction: fullSystemInstruction,
            temperature: 0.7,
          },
        });

        responseText = response.text || "";
        if (!responseText.trim()) {
          const fb = generateCatalogFallback(message, typedRole, activeProd);
          responseText = fb.answer;
          matchedProducts = fb.matchedProducts;
          followUps = fb.followUps;
        }
      } catch (modelErr: any) {
        const errMsg = String(modelErr?.message || modelErr);
        if (targetModel === "gemini-3.1-pro-preview" && (errMsg.includes("429") || errMsg.includes("quota") || errMsg.includes("RESOURCE_EXHAUSTED"))) {
          try {
            activeModelUsed = "gemini-3.5-flash";
            fallbackNotice = "Responded via Gemini 3.5 Flash (Pro Preview quota limit reached).";
            const fallbackResponse = await aiClient.models.generateContent({
              model: "gemini-3.5-flash",
              contents,
              config: {
                systemInstruction: fullSystemInstruction,
                temperature: 0.7,
              },
            });
            responseText = fallbackResponse.text || "";
          } catch {
            const fb = generateCatalogFallback(message, typedRole, activeProd);
            responseText = fb.answer;
            matchedProducts = fb.matchedProducts;
            followUps = fb.followUps;
            activeModelUsed = "catalog-intelligence-engine";
            fallbackNotice = "Responded via verified store catalog engine.";
          }
        } else {
          console.warn("Gemini API call failed, gracefully using catalog intelligence:", errMsg);
          const fb = generateCatalogFallback(message, typedRole, activeProd);
          responseText = fb.answer;
          matchedProducts = fb.matchedProducts;
          followUps = fb.followUps;
          activeModelUsed = "catalog-intelligence-engine";
          fallbackNotice = "Responded via verified store catalog engine.";
        }
      }
    }

    // Match any referenced products if not already populated
    if (matchedProducts.length === 0) {
      const qAndAnswerLower = `${message} ${responseText}`.toLowerCase();
      matchedProducts = ALL_PRODUCTS.filter((p) => {
        const titleSnippet = p.title.toLowerCase().slice(0, 18);
        return qAndAnswerLower.includes(titleSnippet) || (qAndAnswerLower.includes(p.brand.toLowerCase()) && qAndAnswerLower.includes(p.category.toLowerCase()));
      }).slice(0, 3);
    }

    if (followUps.length === 0) {
      if (matchedProducts.length > 0) {
        followUps.push(`Compare specs of ${matchedProducts[0].brand} with alternatives`);
        followUps.push(`Check real-world battery and thermal performance`);
        followUps.push(`Add ${matchedProducts[0].title.slice(0, 24)}... to bag`);
      } else {
        followUps.push("Show me top-rated laptops for programming");
        followUps.push("4K color-accurate monitors under ₹40,000");
        followUps.push("What accessories do you recommend?");
      }
    }

    const durationMs = Date.now() - startTime;

    return NextResponse.json({
      ok: true,
      answer: responseText,
      modelUsed: activeModelUsed,
      modelTargeted: targetModel,
      modelReasoning: reasoning,
      fallbackNotice,
      role: typedRole,
      matchedProducts,
      followUps,
      durationMs,
    });
  } catch (error: any) {
    console.error("Gemini Chat API Error:", error);
    return NextResponse.json(
      {
        ok: false,
        error: error?.message || "Failed to process chat with Gemini",
      },
      { status: 500 }
    );
  }
}
