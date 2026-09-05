import { NextRequest, NextResponse } from "next/server";
import { GoogleGenAI } from "@google/genai";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";
import {
  buildFullSystemInstruction,
  trimConversationHistory,
  AI_ASSISTANT_BUDGET,
  type AssistantPersonaRole,
} from "@/catalog/assistantConfig";

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

export type GeminiRole = AssistantPersonaRole;
export type ModelTier = "auto" | "gemini-3.5-flash" | "gemini-3.1-flash-lite" | "gemini-3.1-pro-preview";

export interface ChatHistoryItem {
  role: "user" | "model" | "assistant";
  text: string;
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

    const validRoles: GeminiRole[] = ["grok_teardown", "concierge", "hardware_specialist", "merchant_auditor", "custom"];
    const typedRole: GeminiRole = validRoles.includes(role as GeminiRole) ? (role as GeminiRole) : "concierge";
    const activeProd = activeProductId ? ALL_PRODUCTS.find((p) => p.id === activeProductId) : undefined;

    const fullSystemInstruction = buildFullSystemInstruction({
      role: typedRole,
      activeProduct: activeProd,
      customSystemInstruction,
    });
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
      // Format history with smart sliding window within 8K–12K token budget
      const contents: Array<{ role: "user" | "model"; parts: Array<{ text: string }> }> = [];
      const trimmedHistory = trimConversationHistory(
        history,
        AI_ASSISTANT_BUDGET.conversationBudgetTokens
      );
      for (const item of trimmedHistory) {
        contents.push({
          role: item.role === "user" ? "user" : "model",
          parts: [{ text: item.content }],
        });
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
            temperature: 0.5,
            maxOutputTokens: AI_ASSISTANT_BUDGET.maxResponseTokens,
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
        let handledViaFallback = false;

        // 1. If quota or rate limit exceeded on pro model, try flash
        if (targetModel === "gemini-3.1-pro-preview" && (errMsg.includes("429") || errMsg.includes("quota") || errMsg.includes("RESOURCE_EXHAUSTED"))) {
          try {
            activeModelUsed = "gemini-3.5-flash";
            fallbackNotice = "Responded via Gemini 3.5 Flash (Pro Preview quota limit reached).";
            const fallbackResponse = await aiClient.models.generateContent({
              model: "gemini-3.5-flash",
              contents,
              config: {
                systemInstruction: fullSystemInstruction,
                temperature: 0.5,
                maxOutputTokens: AI_ASSISTANT_BUDGET.maxResponseTokens,
              },
            });
            responseText = fallbackResponse.text || "";
            if (responseText.trim()) handledViaFallback = true;
          } catch {}
        }

        // 2. If model name not found (404), try available stable models
        if (!handledViaFallback && (errMsg.includes("404") || errMsg.includes("not found") || errMsg.includes("NOT_FOUND"))) {
          const candidateModels = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"];
          for (const fallbackModel of candidateModels) {
            try {
              activeModelUsed = fallbackModel;
              fallbackNotice = `Responded via ${fallbackModel} (auto model resolution).`;
              const fallbackResponse = await aiClient.models.generateContent({
                model: fallbackModel,
                contents,
                config: {
                  systemInstruction: fullSystemInstruction,
                  temperature: 0.5,
                  maxOutputTokens: AI_ASSISTANT_BUDGET.maxResponseTokens,
                },
              });
              responseText = fallbackResponse.text || "";
              if (responseText.trim()) {
                activeModelUsed = fallbackModel;
                fallbackNotice = `Responded via ${fallbackModel} (stable model).`;
                handledViaFallback = true;
                break;
              }
            } catch {}
          }
        }

        if (!handledViaFallback) {
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
