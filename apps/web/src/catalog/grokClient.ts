import type { ProductItem } from "@/data/products";
import type { CustomModelConfig } from "@/catalog/modelConfig";

export type GrokRole = "grok_teardown" | "concierge" | "hardware_specialist" | "merchant_auditor" | "custom";
export type GrokModelTier = "auto" | "grok-2-latest" | "grok-2" | "grok-beta" | "openai/gpt-oss-120b" | "groq-fast";

export interface ChatHistoryItem {
  role: "user" | "model" | "assistant";
  text: string;
}

export interface GrokChatResponse {
  ok: boolean;
  answer?: string;
  modelUsed?: string;
  modelTargeted?: string;
  provider?: string;
  fallbackNotice?: string | null;
  role?: GrokRole;
  matchedProducts?: ProductItem[];
  followUps?: string[];
  durationMs?: number;
  error?: string;
}

export interface SendGrokChatParams {
  message: string;
  history?: ChatHistoryItem[];
  role?: GrokRole;
  customSystemInstruction?: string;
  modelPreference?: GrokModelTier;
  activeProductId?: string;
  customConfig?: CustomModelConfig;
}

/**
 * Sends a multi-turn chat message to the server-side Grok / Custom AI API endpoint (/api/grok/chat).
 */
export async function sendGrokChatMessage(params: SendGrokChatParams): Promise<GrokChatResponse> {
  try {
    const res = await fetch("/api/grok/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(params),
    });

    if (!res.ok) {
      const errJson = await res.json().catch(() => ({}));
      return {
        ok: false,
        error: errJson.error || `Server responded with status ${res.status}`,
      };
    }

    const data: GrokChatResponse = await res.json();
    return data;
  } catch (err: any) {
    return {
      ok: false,
      error: err?.message || "Network error while connecting to AI assistant",
    };
  }
}
