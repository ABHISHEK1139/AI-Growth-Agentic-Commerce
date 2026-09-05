export type AIProviderId =
  | "grok"
  | "ollama"
  | "lmstudio"
  | "groq"
  | "openai"
  | "custom"
  | "gemini";

export interface CustomModelConfig {
  providerId: AIProviderId;
  baseUrl: string;
  apiKey: string;
  modelName: string;
  displayName?: string;
  customHeaders?: Record<string, string>;
}

export interface ProviderPreset {
  id: AIProviderId;
  name: string;
  badge: string;
  description: string;
  defaultBaseUrl: string;
  defaultModel: string;
  requiresKey: boolean;
  keyPlaceholder: string;
  isLocal: boolean;
}

export const PROVIDER_PRESETS: Record<AIProviderId, ProviderPreset> = {
  grok: {
    id: "grok",
    name: "xAI Grok",
    badge: "⚡ xAI",
    description: "Maximum truth-seeking reasoning via xAI Grok API",
    defaultBaseUrl: "https://api.x.ai/v1",
    defaultModel: "grok-2-latest",
    requiresKey: true,
    keyPlaceholder: "xai-...",
    isLocal: false,
  },
  ollama: {
    id: "ollama",
    name: "Local Ollama",
    badge: "🦙 Local",
    description: "Self-hosted local models running on your machine (No API key needed)",
    defaultBaseUrl: "http://localhost:11434/v1",
    defaultModel: "llama3.2",
    requiresKey: false,
    keyPlaceholder: "None needed (local inference)",
    isLocal: true,
  },
  lmstudio: {
    id: "lmstudio",
    name: "LM Studio / vLLM",
    badge: "💻 Local",
    description: "Local desktop inference via LM Studio or vLLM server",
    defaultBaseUrl: "http://localhost:1234/v1",
    defaultModel: "local-model",
    requiresKey: false,
    keyPlaceholder: "None needed (local inference)",
    isLocal: true,
  },
  groq: {
    id: "groq",
    name: "Groq Ultra-Fast",
    badge: "🚀 Groq",
    description: "Ultra-low-latency LPU inference for Llama 3 & GPT-OSS",
    defaultBaseUrl: "https://api.groq.com/openai/v1",
    defaultModel: "openai/gpt-oss-120b",
    requiresKey: true,
    keyPlaceholder: "gsk_...",
    isLocal: false,
  },
  openai: {
    id: "openai",
    name: "OpenAI Direct",
    badge: "🌐 OpenAI",
    description: "Official OpenAI chat completions endpoint",
    defaultBaseUrl: "https://api.openai.com/v1",
    defaultModel: "gpt-4o-mini",
    requiresKey: true,
    keyPlaceholder: "sk-...",
    isLocal: false,
  },
  custom: {
    id: "custom",
    name: "Custom Provider",
    badge: "⚙️ Custom",
    description: "DeepSeek, OpenRouter, Together, Mistral, or custom server",
    defaultBaseUrl: "https://api.deepseek.com/v1",
    defaultModel: "deepseek-chat",
    requiresKey: true,
    keyPlaceholder: "Provider API key",
    isLocal: false,
  },
  gemini: {
    id: "gemini",
    name: "Google Gemini",
    badge: "✨ Gemini",
    description: "Google Gemini SDK with automated model fallback",
    defaultBaseUrl: "https://generativelanguage.googleapis.com",
    defaultModel: "gemini-3.5-flash",
    requiresKey: false,
    keyPlaceholder: "Configured via GEMINI_API_KEY",
    isLocal: false,
  },
};

const STORAGE_KEY = "agentpay_custom_ai_config";

export function getStoredModelConfig(): CustomModelConfig | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.baseUrl === "string" && typeof parsed.modelName === "string") {
      return parsed;
    }
  } catch {}
  return null;
}

export function saveStoredModelConfig(config: CustomModelConfig): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {}
}

export function clearStoredModelConfig(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {}
}
