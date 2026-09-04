import type { ProductItem } from "@/data/products";

export type GeminiRole = "concierge" | "hardware_specialist" | "merchant_auditor" | "custom";
export type ModelTier = "auto" | "gemini-3.5-flash" | "gemini-3.1-flash-lite" | "gemini-3.1-pro-preview";

export interface ChatHistoryItem {
  role: "user" | "model";
  text: string;
}

export interface GeminiChatResponse {
  ok: boolean;
  answer?: string;
  modelUsed?: string;
  modelTargeted?: string;
  modelReasoning?: string;
  fallbackNotice?: string | null;
  role?: GeminiRole;
  matchedProducts?: ProductItem[];
  followUps?: string[];
  durationMs?: number;
  error?: string;
}

export interface SendGeminiChatParams {
  message: string;
  history?: ChatHistoryItem[];
  role?: GeminiRole;
  customSystemInstruction?: string;
  modelPreference?: ModelTier;
  activeProductId?: string;
}

/**
 * Sends a multi-turn chat message to the server-side Gemini API endpoint (/api/gemini/chat).
 */
export async function sendGeminiChatMessage(params: SendGeminiChatParams): Promise<GeminiChatResponse> {
  try {
    const res = await fetch("/api/gemini/chat", {
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

    const data: GeminiChatResponse = await res.json();
    return data;
  } catch (err: any) {
    return {
      ok: false,
      error: err?.message || "Network error while connecting to Gemini Chat",
    };
  }
}
