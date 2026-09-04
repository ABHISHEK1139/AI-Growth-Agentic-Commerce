import { NextRequest, NextResponse } from "next/server";
import { GoogleGenAI } from "@google/genai";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";

// Server-side initialization of Gemini SDK as required by system guidelines
const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
  httpOptions: {
    headers: {
      "User-Agent": "aistudio-build",
    },
  },
});

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
    if (activeProductId) {
      const activeProd = ALL_PRODUCTS.find((p) => p.id === activeProductId);
      if (activeProd) {
        activeProductContext = `\n\n--- CURRENTLY VIEWED PRODUCT ---\nTitle: ${activeProd.title}\nBrand: ${activeProd.brand}\nPrice: ₹${(activeProd.priceMinor / 100).toLocaleString("en-IN")}\nCategory: ${activeProd.category}\nRating: ${activeProd.rating} / 5 (${activeProd.reviewCount} reviews)\nSummary: ${activeProd.whyFitsYou?.summary || activeProd.shortSpecs}\nSpecs: ${JSON.stringify(activeProd.specsGrouped || activeProd.shortSpecs)}\nStock: ${activeProd.stock} units available\n---------------------------------`;
      }
    }

    const fullSystemInstruction = `${baseInstruction}${activeProductContext}${catalogContext}`;

    const { model: targetModel, reasoning } = detectTaskModel(message, history, modelPreference);

    // Format history for multi-turn chat
    const contents: Array<{ role: "user" | "model"; parts: Array<{ text: string }> }> = [];

    // Add up to previous 10 messages from history to maintain tight context
    const recentHistory = (history as ChatHistoryItem[]).slice(-10);
    for (const item of recentHistory) {
      if (item.text && item.text.trim()) {
        contents.push({
          role: item.role === "user" ? "user" : "model",
          parts: [{ text: item.text.trim() }],
        });
      }
    }

    // Append current user message
    contents.push({
      role: "user",
      parts: [{ text: message.trim() }],
    });

    let activeModelUsed = targetModel;
    let fallbackNotice: string | null = null;
    let responseText = "";

    try {
      const response = await ai.models.generateContent({
        model: targetModel,
        contents,
        config: {
          systemInstruction: fullSystemInstruction,
          temperature: 0.7,
        },
      });

      responseText = response.text || "I processed your request, but received an empty response. How else may I assist you?";
    } catch (modelErr: any) {
      const errMsg = String(modelErr?.message || modelErr);
      // Handle paid model quota gracefully (e.g. if gemini-3.1-pro-preview returns 429 quota on free key)
      if (targetModel === "gemini-3.1-pro-preview" && (errMsg.includes("429") || errMsg.includes("quota") || errMsg.includes("RESOURCE_EXHAUSTED"))) {
        console.warn("gemini-3.1-pro-preview quota reached, gracefully falling back to gemini-3.5-flash");
        activeModelUsed = "gemini-3.5-flash";
        fallbackNotice = "Responded via Gemini 3.5 Flash (Gemini 3.1 Pro Preview requires billing tier in Settings > Secrets).";

        const fallbackResponse = await ai.models.generateContent({
          model: "gemini-3.5-flash",
          contents,
          config: {
            systemInstruction: fullSystemInstruction,
            temperature: 0.7,
          },
        });

        responseText = fallbackResponse.text || "I am here to assist you with our catalog.";
      } else {
        throw modelErr;
      }
    }

    // Match any referenced products from catalog to display interactive quick cards
    const qAndAnswerLower = `${message} ${responseText}`.toLowerCase();
    const matchedProducts: ProductItem[] = ALL_PRODUCTS.filter((p) => {
      const titleSnippet = p.title.toLowerCase().slice(0, 18);
      return qAndAnswerLower.includes(titleSnippet) || (qAndAnswerLower.includes(p.brand.toLowerCase()) && qAndAnswerLower.includes(p.category.toLowerCase()));
    }).slice(0, 3);

    // Generate 3 contextual follow-up prompt chips
    const followUps: string[] = [];
    if (matchedProducts.length > 0) {
      followUps.push(`Compare specs of ${matchedProducts[0].brand} with alternatives`);
      followUps.push(`Check real-world battery and thermal performance`);
      followUps.push(`Add ${matchedProducts[0].title.slice(0, 24)}... to bag`);
    } else {
      followUps.push("Show me top-rated laptops for programming");
      followUps.push("4K color-accurate monitors under ₹40,000");
      followUps.push("What accessories do you recommend?");
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
