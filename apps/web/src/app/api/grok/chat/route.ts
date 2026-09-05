import { NextRequest, NextResponse } from "next/server";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";
import type { CustomModelConfig } from "@/catalog/modelConfig";

export type GrokRole = "grok_teardown" | "concierge" | "hardware_specialist" | "merchant_auditor" | "custom";
export type GrokModelTier = "auto" | "grok-2-latest" | "grok-2" | "grok-beta" | "openai/gpt-oss-120b" | "groq-fast";

export interface ChatHistoryItem {
  role: "user" | "model" | "assistant";
  text: string;
}

const GROK_ROLE_INSTRUCTIONS: Record<GrokRole, string> = {
  grok_teardown: `You are Grok AI Commerce Intelligence: the maximum truth-seeking, direct, technically rigorous hardware evaluator and shopping co-pilot.
Your mission is to give buyers the unfiltered truth about hardware architecture, price-to-performance tradeoffs, thermal envelopes, and value.
- First-Principles Reasoning: Analyze silicon, cooling, RAM bandwidth, display color accuracy, and port IO from physical reality rather than marketing claims.
- Zero Fluff: Be direct, witty, and concise. No generic corporate disclaimers.
- Honest Pricing: Always quote prices in Indian Rupees (₹) with proper comma separation. If a product is overpriced or bottlenecked, state it frankly.
- Real Catalog Grounding: Reference only products that exist in our verified store catalog. When recommending an item, cite its exact model title.`,

  concierge: `You are Grok Shopping Concierge: an intelligent, insightful shopping guide for our premium electronics store.
Provide honest pros and cons, explain price-to-performance tradeoffs, and recommend accessories that make engineering sense.
Always format currency in Indian Rupees (₹) with proper comma separation.
When referencing catalog items, use exact model titles so the user can easily locate or purchase them.`,

  hardware_specialist: `You are Grok Senior Hardware Architect and Benchmarking Specialist.
Perform deep technical evaluations of laptop architectures, monitors, thermal dynamics, ports, and peripheral gear.
Analyze developer workloads (Docker containers, compilation times, memory pressure with 16GB vs 32GB RAM), color spaces (100% sRGB vs 95% DCI-P3), and interface standards (Thunderbolt 4 vs USB 3.2 Gen 2, HDMI 2.0 vs 2.1).
Provide structured comparisons with precise, technically rigorous assessments.`,

  merchant_auditor: `You are Grok Autonomous Commerce Risk Officer and Merchant Policy Auditor.
Advise store administrators on transaction guardrails, automated spending thresholds, checkout approval policies, and audit ledger compliance.
Provide clear risk assessment scoring, mitigation steps, and policy configuration advice.`,

  custom: `You are Grok AI, an adaptable, truth-seeking shopping and technical assistant. Follow user instructions precisely with maximum honesty and clarity.`,
};

function buildCatalogSummary(): string {
  return ALL_PRODUCTS.slice(0, 35)
    .map(
      (p) =>
        `- [ID: ${p.id}] ${p.title} | Brand: ${p.brand} | Category: ${p.category} | Price: ₹${(p.priceMinor / 100).toLocaleString("en-IN")} | Rating: ${p.rating}/5 | Key Specs: ${p.shortSpecs || "Standard specifications"}`
    )
    .join("\n");
}

function resolveGrokEndpointAndKey(
  preference?: GrokModelTier,
  customConfig?: CustomModelConfig
): {
  endpoint: string;
  key: string;
  targetModel: string;
  provider: string;
  isLoopback: boolean;
} {
  // 1. If user provided a custom config (e.g. Ollama, LM Studio, custom OpenAI endpoint)
  if (customConfig && customConfig.baseUrl) {
    const rawBase = customConfig.baseUrl.trim().replace(/\/$/, "");
    const endpoint = rawBase.endsWith("/chat/completions") ? rawBase : `${rawBase}/chat/completions`;
    const isLoopback =
      rawBase.includes("localhost") || rawBase.includes("127.0.0.1") || rawBase.includes("0.0.0.0");
    return {
      endpoint,
      key: customConfig.apiKey ? customConfig.apiKey.trim() : "",
      targetModel: customConfig.modelName || "default",
      provider: customConfig.providerId || "custom",
      isLoopback,
    };
  }

  const grokApiKey = (process.env.GROK_API_KEY || process.env.XAI_API_KEY || "").trim();
  const groqApiKey = (
    process.env.GROQ_API_KEY ||
    (process.env.MODEL_API_KEY?.startsWith("gsk_") ? process.env.MODEL_API_KEY : "")
  ).trim();
  const genericKey = (process.env.MODEL_API_KEY || "").trim();

  // 2. If explicit xAI Grok key is present in env
  if (grokApiKey) {
    const baseUrl = (process.env.GROK_BASE_URL || "https://api.x.ai/v1").replace(/\/$/, "");
    const targetModel =
      preference && preference !== "auto" && !preference.includes("groq") && !preference.includes("oss")
        ? preference
        : (process.env.GROK_MODEL || "grok-2-latest");
    return {
      endpoint: `${baseUrl}/chat/completions`,
      key: grokApiKey,
      targetModel,
      provider: "xai",
      isLoopback: false,
    };
  }

  // 3. If Groq OpenAI-compatible key is present
  if (groqApiKey) {
    const baseUrl = (process.env.MODEL_BASE_URL || "https://api.groq.com/openai/v1").replace(/\/$/, "");
    const targetModel =
      preference && (preference.includes("oss") || preference.includes("groq"))
        ? (preference === "groq-fast" ? "openai/gpt-oss-120b" : preference)
        : (process.env.MODEL_NAME || "openai/gpt-oss-120b");
    return {
      endpoint: `${baseUrl}/chat/completions`,
      key: groqApiKey,
      targetModel,
      provider: "groq",
      isLoopback: false,
    };
  }

  // 4. Local model (Ollama / LM Studio) configured in environment
  const localProvider = (process.env.MODEL_PROVIDER || "").toLowerCase();
  const ollamaBaseUrl =
    process.env.OLLAMA_BASE_URL ||
    (localProvider === "ollama" ? (process.env.MODEL_BASE_URL || "http://localhost:11434/v1") : "");
  if (ollamaBaseUrl) {
    const cleanBase = ollamaBaseUrl.trim().replace(/\/$/, "");
    return {
      endpoint: cleanBase.endsWith("/chat/completions") ? cleanBase : `${cleanBase}/chat/completions`,
      key: process.env.MODEL_API_KEY || "ollama",
      targetModel: process.env.MODEL_NAME || process.env.OLLAMA_MODEL || "llama3.2",
      provider: "ollama",
      isLoopback: true,
    };
  }

  const lmstudioBaseUrl =
    process.env.LMSTUDIO_BASE_URL ||
    (localProvider === "lmstudio" || localProvider === "local" ? (process.env.MODEL_BASE_URL || "http://localhost:1234/v1") : "");
  if (lmstudioBaseUrl) {
    const cleanBase = lmstudioBaseUrl.trim().replace(/\/$/, "");
    return {
      endpoint: cleanBase.endsWith("/chat/completions") ? cleanBase : `${cleanBase}/chat/completions`,
      key: process.env.MODEL_API_KEY || "lmstudio",
      targetModel: process.env.MODEL_NAME || "local-model",
      provider: "lmstudio",
      isLoopback: true,
    };
  }

  // 5. Generic OpenAI compatible key
  if (genericKey) {
    const baseUrl = (process.env.MODEL_BASE_URL || "https://api.openai.com/v1").replace(/\/$/, "");
    return {
      endpoint: `${baseUrl}/chat/completions`,
      key: genericKey,
      targetModel: process.env.MODEL_NAME || "grok-2-latest",
      provider: "generic",
      isLoopback: false,
    };
  }

  return {
    endpoint: "",
    key: "",
    targetModel: "grok-catalog-intelligence",
    provider: "none",
    isLoopback: false,
  };
}

function generateGrokCatalogFallback(
  query: string,
  role: GrokRole,
  activeProduct?: ProductItem
): {
  answer: string;
  matchedProducts: ProductItem[];
  followUps: string[];
} {
  const qLower = query.toLowerCase();

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
    answer = `Here is the first-principles breakdown on the **${activeProduct.title}**:\n\n` +
      `• **Price**: ₹${(activeProduct.priceMinor / 100).toLocaleString("en-IN")} (Verified In Stock)\n` +
      `• **Teardown Verdict**: ${activeProduct.whyFitsYou?.summary || activeProduct.shortSpecs || "Solid build with premium thermal envelope and clean IO layout."}\n` +
      `• **Hardware Overview**: ${activeProduct.shortSpecs || "High-performance architecture."}\n` +
      `• **Rating**: ★ ${activeProduct.rating} / 5 based on ${activeProduct.reviewCount} customer reviews.\n\n` +
      (activeProduct.whyFitsYou?.pros?.length ? `**Key Highlights:**\n` + activeProduct.whyFitsYou.pros.slice(0, 3).map((s) => `• ${s}`).join("\n") + "\n\n" : "") +
      `**Grok Recommendation:** If this fits your workflow, you can add it directly to bag or initiate instant in-app checkout.`;
  } else if (top) {
    answer = `Based on first-principles analysis of our current catalog, here are the top options matching your query:\n\n` +
      selectedProducts
        .map(
          (p, i) =>
            `${i + 1}. **${p.title}** — ₹${(p.priceMinor / 100).toLocaleString("en-IN")}\n` +
            `   • *Specs*: ${p.shortSpecs || p.brand}\n` +
            `   • *Rating*: ★ ${p.rating} / 5 (${p.reviewCount} reviews)\n` +
            `   • *Verdict*: ${p.whyFitsYou?.summary || p.shortSpecs || "Exceptional price-to-performance ratio in its tier."}`
        )
        .join("\n\n") +
      `\n\nWould you like a deep architectural comparison between any of these, or to proceed with checkout?`;
  } else {
    answer = `I scanned our verified hardware catalog for "${query}". You can explore our top-tier laptops, 4K monitors, or audio peripherals directly, or refine your search with specific budget and performance criteria.`;
  }

  const followUps = selectedProducts.map((p) => `Deep breakdown of ${p.title.slice(0, 25)}...`);
  followUps.push("Compare price-to-performance ratio");
  followUps.push("Check real-world thermal and battery endurance");

  return { answer, matchedProducts: selectedProducts, followUps: followUps.slice(0, 4) };
}

function cleanThinkingContent(rawContent: string): string {
  if (!rawContent) return "";
  let cleaned = rawContent.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
  if (!cleaned) {
    cleaned = rawContent.replace(/<\/?think>/gi, "").trim();
  }
  return cleaned;
}

export async function POST(req: NextRequest) {
  const startTime = Date.now();

  try {
    const body = await req.json();
    const {
      message,
      history = [],
      role = "grok_teardown",
      customSystemInstruction,
      modelPreference = "auto",
      activeProductId,
      customConfig,
    } = body;

    if (!message || typeof message !== "string" || !message.trim()) {
      return NextResponse.json({ ok: false, error: "Missing or empty message parameter" }, { status: 400 });
    }

    const typedRole: GrokRole = GROK_ROLE_INSTRUCTIONS[role as GrokRole] ? (role as GrokRole) : "grok_teardown";
    const activeProd = activeProductId ? ALL_PRODUCTS.find((p) => p.id === activeProductId) : undefined;

    const baseSystemPrompt = GROK_ROLE_INSTRUCTIONS[typedRole];
    const catalogData = buildCatalogSummary();

    const activeProdContext = activeProd
      ? `\n\nCurrently Active Product Under Review:\n- Title: ${activeProd.title}\n- Brand: ${activeProd.brand}\n- Price: ₹${(activeProd.priceMinor / 100).toLocaleString("en-IN")}\n- Specs: ${activeProd.shortSpecs}\n- Rating: ${activeProd.rating} / 5 (${activeProd.reviewCount} reviews)\n- Stock: ${activeProd.stock > 0 ? "In Stock" : "Pre-order"}`
      : "";

    const fullSystemInstruction = `${baseSystemPrompt}

${customSystemInstruction ? `Custom Guidelines:\n${customSystemInstruction}\n` : ""}
Store Catalog Snapshot:
${catalogData}${activeProdContext}

Important Rules:
1. Always deliver sharp, first-principles, accurate engineering truth.
2. Prices must be in ₹ (Indian Rupees) with standard formatting.
3. If recommending products, use exact names from the catalog.
4. Keep responses structured, highly readable, and free of marketing fluff.`;

    const { endpoint, key, targetModel, provider, isLoopback } = resolveGrokEndpointAndKey(
      modelPreference,
      customConfig
    );

    let responseText = "";
    let activeModelUsed = targetModel;
    let fallbackNotice: string | null = null;
    let matchedProducts: ProductItem[] = [];
    let followUps: string[] = [];

    // If no endpoint configured (and no loopback) and no key
    if ((provider === "none" || !endpoint) && !isLoopback) {
      const fb = generateGrokCatalogFallback(message, typedRole, activeProd);
      responseText = fb.answer;
      matchedProducts = fb.matchedProducts;
      followUps = fb.followUps;
      activeModelUsed = "grok-catalog-engine";
      fallbackNotice = "Responded via Grok verified catalog intelligence (offline mode).";
    } else {
      const messages: Array<{ role: "system" | "user" | "assistant"; content: string }> = [
        { role: "system", content: fullSystemInstruction },
      ];

      for (const item of history.slice(-8)) {
        if (item.text && item.text.trim()) {
          messages.push({
            role: item.role === "user" ? "user" : "assistant",
            content: item.text.trim(),
          });
        }
      }

      messages.push({
        role: "user",
        content: message.trim(),
      });

      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (key) {
        headers["Authorization"] = `Bearer ${key}`;
      }

      try {
        const grokRes = await fetch(endpoint, {
          method: "POST",
          headers,
          body: JSON.stringify({
            model: targetModel,
            messages,
            temperature: 0.6,
            max_tokens: 1024,
          }),
        });

        if (!grokRes.ok) {
          const errBody = await grokRes.text();
          console.warn(`AI API returned ${grokRes.status}: ${errBody}`);
          throw new Error(`Provider HTTP ${grokRes.status}`);
        }

        const data = await grokRes.json();
        const choice = data.choices?.[0];
        const rawContent = choice?.message?.content || "";
        const reasoningContent = choice?.message?.reasoning || "";

        responseText = cleanThinkingContent(rawContent);
        if (!responseText && reasoningContent) {
          responseText = cleanThinkingContent(reasoningContent);
        }

        if (!responseText.trim()) {
          const fb = generateGrokCatalogFallback(message, typedRole, activeProd);
          responseText = fb.answer;
          matchedProducts = fb.matchedProducts;
          followUps = fb.followUps;
          fallbackNotice = "Catalog fallback used due to empty model response.";
        }
      } catch (callErr: any) {
        console.warn("AI endpoint call failed, falling back to catalog intelligence:", callErr?.message);
        const fb = generateGrokCatalogFallback(message, typedRole, activeProd);
        responseText = fb.answer;
        matchedProducts = fb.matchedProducts;
        followUps = fb.followUps;
        activeModelUsed = "grok-catalog-engine";
        fallbackNotice = `Responded via Grok catalog intelligence engine (${callErr?.message || "Network failure"}).`;
      }
    }

    if (matchedProducts.length === 0) {
      const qAndAnswerLower = `${message} ${responseText}`.toLowerCase();
      matchedProducts = ALL_PRODUCTS.filter((p) => {
        const titleSnippet = p.title.toLowerCase().slice(0, 18);
        return (
          qAndAnswerLower.includes(titleSnippet) ||
          (qAndAnswerLower.includes(p.brand.toLowerCase()) && qAndAnswerLower.includes(p.category.toLowerCase()))
        );
      }).slice(0, 3);
    }

    if (followUps.length === 0) {
      if (matchedProducts.length > 0) {
        followUps.push(`Deep architectural breakdown of ${matchedProducts[0].title.slice(0, 24)}...`);
        followUps.push("Compare real-world thermal & battery performance");
        followUps.push(`Add ${matchedProducts[0].title.slice(0, 22)}... to bag`);
      } else {
        followUps.push("First-principles teardown of top coding laptops");
        followUps.push("4K color-accurate monitors under ₹40,000");
        followUps.push("Explain RAM and thermal bottlenecks");
      }
    }

    const durationMs = Date.now() - startTime;

    return NextResponse.json({
      ok: true,
      answer: responseText,
      modelUsed: activeModelUsed,
      modelTargeted: targetModel,
      provider,
      fallbackNotice,
      role: typedRole,
      matchedProducts,
      followUps,
      durationMs,
    });
  } catch (error: any) {
    console.error("Chat API Error:", error);
    return NextResponse.json(
      {
        ok: false,
        error: error?.message || "Failed to process chat with AI",
      },
      { status: 500 }
    );
  }
}
