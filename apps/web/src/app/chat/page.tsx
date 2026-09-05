"use client";

import React, { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Sparkles,
  Send,
  Trash2,
  SlidersHorizontal,
  ShoppingBag,
  ExternalLink,
  Bot,
  User,
  Zap,
  Cpu,
  ShieldCheck,
  CheckCircle2,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Settings,
  HelpCircle,
  AlertTriangle,
  Search,
} from "lucide-react";
import { useStore } from "@/context/StoreContext";
import { AIModelConfigModal } from "@/components/AIModelConfigModal";
import { getStoredModelConfig, type CustomModelConfig } from "@/catalog/modelConfig";
import { Sliders } from "lucide-react";
import { formatMinorToMajor } from "@/lib/money";
import type { ProductItem } from "@/data/products";
import {
  sendGeminiChatMessage,
  type GeminiRole,
  type ModelTier,
  type ChatHistoryItem,
} from "@/catalog/geminiClient";
import {
  sendGrokChatMessage,
  type GrokRole,
  type GrokModelTier,
} from "@/catalog/grokClient";

interface ChatMessage {
  id: string;
  role: "user" | "model";
  text: string;
  modelUsed?: string;
  fallbackNotice?: string | null;
  durationMs?: number;
  matchedProducts?: ProductItem[];
  followUps?: string[];
  timestamp: string;
  isError?: boolean;
  errorHeading?: string;
  queryAttempted?: string;
}

type AIEngine = "grok" | "gemini";

const GROK_ROLES: Record<
  GrokRole,
  {
    name: string;
    icon: React.ReactNode;
    tagline: string;
    recommendedModel: GrokModelTier;
    starters: string[];
    defaultPrompt: string;
  }
> = {
  grok_teardown: {
    name: "Grok Teardown Specialist",
    icon: <Zap className="h-4 w-4 text-amber-500" />,
    tagline: "First-principles hardware teardown, thermal bottlenecks & unfiltered value truth",
    recommendedModel: "grok-2-latest",
    starters: [
      "First-principles teardown of top coding laptops under ₹80,000",
      "Does 16GB RAM throttle local Docker development on modern ultrabooks?",
      "Which 4K monitor has actual 10-bit color without fake FRC dithering?",
      "Expose the real price-to-performance tradeoff between M-series and Intel Core Ultra",
    ],
    defaultPrompt: `You are Grok AI Commerce Intelligence: the maximum truth-seeking, direct, technically rigorous hardware evaluator and shopping co-pilot.
Your mission is to give buyers the unfiltered truth about hardware architecture, price-to-performance tradeoffs, thermal envelopes, and value.
- First-Principles Reasoning: Analyze silicon, cooling, RAM bandwidth, display color accuracy, and port IO from physical reality rather than marketing claims.
- Zero Fluff: Be direct, witty, and concise. No generic corporate disclaimers.
- Honest Pricing: Always quote prices in Indian Rupees (₹) with proper comma separation. If a product is overpriced or bottlenecked, state it frankly.
- Real Catalog Grounding: Reference only products that exist in our verified store catalog. When recommending an item, cite its exact model title.`,
  },
  concierge: {
    name: "Shopping Concierge",
    icon: <ShoppingBag className="h-4 w-4 text-emerald-600" />,
    tagline: "Personal buying advisor for verified electronics & peripherals",
    recommendedModel: "grok-2-latest",
    starters: [
      "Find me a reliable laptop for programming under ₹75,000",
      "What is the best 4K monitor in your catalog under ₹40,000?",
      "Noise cancelling headphones for long flights with multipoint pairing",
      "Which gaming laptop offers the best thermal cooling for long sessions?",
    ],
    defaultPrompt: `You are Grok Shopping Concierge: an intelligent, insightful shopping guide for our premium electronics store.
Provide honest pros and cons, explain price-to-performance tradeoffs, and recommend accessories that make engineering sense.
Always format currency in Indian Rupees (₹) with proper comma separation.
When referencing catalog items, use exact model titles so the user can easily locate or purchase them.`,
  },
  hardware_specialist: {
    name: "Hardware Architect",
    icon: <Cpu className="h-4 w-4 text-indigo-600" />,
    tagline: "Deep technical comparisons, silicon benchmarks & developer workloads",
    recommendedModel: "grok-2-latest",
    starters: [
      "Deep comparison of CPU architecture and thermal throttling between Intel Core i7 and AMD Ryzen 7",
      "100% sRGB vs 95% DCI-P3: Which monitor panel is optimal for color grading?",
      "Docker & Kubernetes local workload suitability: 16GB vs 32GB RAM memory pressure",
      "Thunderbolt 4 vs USB 3.2 Gen 2 bandwidth limits for dual 4K external displays",
    ],
    defaultPrompt: `You are Grok Senior Hardware Architect and Benchmarking Specialist.
Perform deep technical evaluations of laptop architectures, monitors, thermal dynamics, ports, and peripheral gear.
Analyze developer workloads (Docker containers, compilation times, memory pressure with 16GB vs 32GB RAM), color spaces (100% sRGB vs 95% DCI-P3), and interface standards (Thunderbolt 4 vs USB 3.2 Gen 2, HDMI 2.0 vs 2.1).
Provide structured comparisons with precise, technically rigorous assessments.`,
  },
  merchant_auditor: {
    name: "Merchant Risk Officer",
    icon: <ShieldCheck className="h-4 w-4 text-amber-600" />,
    tagline: "Autonomous spending caps, auto-approval thresholds & risk compliance",
    recommendedModel: "openai/gpt-oss-120b",
    starters: [
      "What auto-approval limit should I configure to balance conversion vs fraud risk?",
      "How do autonomous agent transaction caps protect merchant operating capital?",
      "Analyze audit ledger compliance requirements for agent-driven checkouts",
      "How does campaign budget pacing interact with dynamic price discounting?",
    ],
    defaultPrompt: `You are Grok Autonomous Commerce Risk Officer and Merchant Policy Auditor.
Advise store administrators on transaction guardrails, automated spending thresholds, checkout approval policies, and audit ledger compliance.
Provide clear risk assessment scoring, mitigation steps, and policy configuration advice.`,
  },
  custom: {
    name: "Custom Persona",
    icon: <SlidersHorizontal className="h-4 w-4 text-slate-600" />,
    tagline: "Define your own system instructions and operational scope",
    recommendedModel: "auto",
    starters: [
      "Explain the key factors to consider when buying a laptop in 3 concise bullet points",
      "Compare Dell vs HP warranties and after-sales service reliability in India",
      "What is the best mechanical keyboard switch type for quiet office programming?",
    ],
    defaultPrompt: `You are Grok AI, an adaptable, truth-seeking shopping and technical assistant. Follow user instructions precisely with maximum honesty and clarity.`,
  },
};

const GEMINI_ROLES: Record<
  GeminiRole,
  {
    name: string;
    icon: React.ReactNode;
    tagline: string;
    recommendedModel: ModelTier;
    starters: string[];
    defaultPrompt: string;
  }
> = {
  concierge: {
    name: "Shopping Concierge",
    icon: <ShoppingBag className="h-4 w-4 text-emerald-600" />,
    tagline: "Personal buying advisor for verified electronics & peripherals",
    recommendedModel: "gemini-3.5-flash",
    starters: [
      "Find me a reliable laptop for programming under ₹75,000",
      "What is the best 4K monitor in your catalog under ₹40,000?",
      "Noise cancelling headphones for long flights with multipoint pairing",
      "Which gaming laptop offers the best thermal cooling for long sessions?",
    ],
    defaultPrompt: `You are the Official Personal Shopping Concierge for our premium electronics and gadgets catalog.
Your mission is to help shoppers discover the exact right product for their needs and budget.
Maintain a warm, refined, highly knowledgeable tone.
Provide clear, honest pros and cons, explain price-to-performance tradeoffs, and recommend matching accessories where helpful.
Always format currency in Indian Rupees (₹) with proper comma separation.
When referencing products from our catalog, use their exact model name so the user can easily locate or add them to cart.`,
  },
  hardware_specialist: {
    name: "Hardware Architect",
    icon: <Cpu className="h-4 w-4 text-indigo-600" />,
    tagline: "Deep technical comparisons, silicon benchmarks & developer workloads",
    recommendedModel: "gemini-3.1-pro-preview",
    starters: [
      "Deep comparison of CPU architecture and thermal throttling between Intel Core i7 and AMD Ryzen 7",
      "100% sRGB vs 95% DCI-P3: Which monitor panel is optimal for color grading?",
      "Docker & Kubernetes local workload suitability: 16GB vs 32GB RAM memory pressure",
      "Thunderbolt 4 vs USB 3.2 Gen 2 bandwidth limits for dual 4K external displays",
    ],
    defaultPrompt: `You are a Senior Hardware Architect and Benchmarking Specialist.
Your mission is to perform deep technical evaluations of laptop architectures, monitors, thermal envelopes, ports, and peripheral gear.
Analyze real-world developer workloads (Docker containers, compilation times, memory pressure with 16GB vs 32GB RAM), color space fidelity (100% sRGB vs 95% DCI-P3), and interface standards (Thunderbolt 4 vs USB 3.2 Gen 2, HDMI 2.0 vs 2.1).
Deliver precise, technically rigorous, objective assessments with structured tables when comparing multiple models.`,
  },
  merchant_auditor: {
    name: "Merchant Risk Officer",
    icon: <ShieldCheck className="h-4 w-4 text-amber-600" />,
    tagline: "Autonomous spending caps, auto-approval thresholds & risk compliance",
    recommendedModel: "gemini-3.5-flash",
    starters: [
      "What auto-approval limit should I configure to balance conversion vs fraud risk?",
      "How do autonomous agent transaction caps protect merchant operating capital?",
      "Analyze audit ledger compliance requirements for agent-driven checkouts",
      "How does campaign budget pacing interact with dynamic price discounting?",
    ],
    defaultPrompt: `You are an Autonomous Commerce Risk Officer and Merchant Policy Auditor.
Your mission is to advise merchant store administrators on transaction guardrails, automated spending thresholds, checkout approval rules, campaign return-on-ad-spend (RoAS), and audit ledger compliance.
Provide clear risk assessment scoring, mitigation steps, and policy configuration advice.`,
  },
  custom: {
    name: "Custom Persona",
    icon: <SlidersHorizontal className="h-4 w-4 text-slate-600" />,
    tagline: "Define your own system instructions and operational scope",
    recommendedModel: "auto",
    starters: [
      "Explain the key factors to consider when buying a laptop in 3 concise bullet points",
      "Compare Dell vs HP warranties and after-sales service reliability in India",
      "What is the best mechanical keyboard switch type for quiet office programming?",
    ],
    defaultPrompt: `You are an adaptable AI shopping and technical commerce assistant. Follow the shopper's instructions precisely while remaining objective, accurate, and helpful.`,
  },
};

export default function MultiModelChatPage() {
  const router = useRouter();
  const { addToCart } = useStore();

  const [isConfigModalOpen, setIsConfigModalOpen] = useState(false);
  const [activeCustomConfig, setActiveCustomConfig] = useState<CustomModelConfig | null>(null);

  useEffect(() => {
    const stored = getStoredModelConfig();
    if (stored) {
      setActiveCustomConfig(stored);
    }
  }, []);

  const [aiEngine, setAiEngine] = useState<AIEngine>("grok");

  // Grok settings
  const [grokRole, setGrokRole] = useState<GrokRole>("grok_teardown");
  const [grokModelTier, setGrokModelTier] = useState<GrokModelTier>("auto");

  // Gemini settings
  const [geminiRole, setGeminiRole] = useState<GeminiRole>("concierge");
  const [geminiModelTier, setGeminiModelTier] = useState<ModelTier>("auto");

  const [customSystemInstruction, setCustomSystemInstruction] = useState("");
  const [isSystemInstructionOpen, setIsSystemInstructionOpen] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "msg_init",
      role: "model",
      text: "Welcome! I am **Grok AI Commerce Intelligence**.\n\nI provide maximum truth-seeking hardware teardowns, first-principles silicon comparisons, zero-BS pricing breakdowns, and autonomous checkout assistance. What would you like to evaluate?",
      modelUsed: "grok-2-latest",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      followUps: [
        "First-principles teardown of top coding laptops under ₹80,000",
        "Does 16GB RAM throttle local Docker development?",
        "4K monitor with authentic 10-bit color under ₹40,000",
      ],
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [addedItemIds, setAddedItemIds] = useState<Record<string, boolean>>({});

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleEngineSwitch = (engine: AIEngine) => {
    setAiEngine(engine);
    if (engine === "grok") {
      setMessages([
        {
          id: `msg_${Date.now()}`,
          role: "model",
          text: "Switched to **Grok AI Intelligence**. Maximum truth-seeking hardware evaluation, first-principles benchmarks, and direct value analysis active.",
          modelUsed: "grok-2-latest",
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          followUps: GROK_ROLES[grokRole].starters.slice(0, 3),
        },
      ]);
    } else {
      setMessages([
        {
          id: `msg_${Date.now()}`,
          role: "model",
          text: "Switched to **Google Gemini Assistant**. Multi-turn concierge shopping and hardware analysis active.",
          modelUsed: "gemini-3.5-flash",
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          followUps: GEMINI_ROLES[geminiRole].starters.slice(0, 3),
        },
      ]);
    }
  };

  const handleRoleChange = (roleKey: string) => {
    if (aiEngine === "grok") {
      const r = roleKey as GrokRole;
      setGrokRole(r);
      if (r === "custom" && !customSystemInstruction) {
        setCustomSystemInstruction(GROK_ROLES.custom.defaultPrompt);
      }
    } else {
      const r = roleKey as GeminiRole;
      setGeminiRole(r);
      if (r === "custom" && !customSystemInstruction) {
        setCustomSystemInstruction(GEMINI_ROLES.custom.defaultPrompt);
      }
    }
  };

  const handleClearHistory = () => {
    const roleName = aiEngine === "grok" ? GROK_ROLES[grokRole].name : GEMINI_ROLES[geminiRole].name;
    const starters = aiEngine === "grok" ? GROK_ROLES[grokRole].starters : GEMINI_ROLES[geminiRole].starters;

    setMessages([
      {
        id: `msg_${Date.now()}`,
        role: "model",
        text: `Reset conversation context to **${roleName}** (${aiEngine === "grok" ? "Grok AI" : "Gemini"}). How can I assist you?`,
        modelUsed: aiEngine === "grok" ? "grok-2-latest" : "gemini-3.5-flash",
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        followUps: starters.slice(0, 3),
      },
    ]);
  };

  const handleSendMessage = async (textToSend?: string) => {
    const query = (textToSend || input).trim();
    if (!query || loading) return;

    const userMessage: ChatMessage = {
      id: `usr_${Date.now()}`,
      role: "user",
      text: query,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setMessages((prev) => [...prev, userMessage]);
    if (!textToSend) setInput("");
    setLoading(true);

    try {
      if (aiEngine === "grok") {
        const historyPayload: any[] = messages.map((m) => ({
          role: m.role === "user" ? "user" : "assistant",
          text: m.text,
        }));

        const response = await sendGrokChatMessage({
          message: query,
          history: historyPayload,
          role: grokRole,
          customSystemInstruction: grokRole === "custom" ? customSystemInstruction : undefined,
          modelPreference: grokModelTier,
          customConfig: activeCustomConfig || getStoredModelConfig() || undefined,
        });

        if (response.ok && response.answer) {
          const modelMessage: ChatMessage = {
            id: `mod_${Date.now()}`,
            role: "model",
            text: response.answer,
            modelUsed: response.modelUsed,
            fallbackNotice: response.fallbackNotice,
            durationMs: response.durationMs,
            matchedProducts: response.matchedProducts,
            followUps: response.followUps,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          };
          setMessages((prev) => [...prev, modelMessage]);
        } else {
          const errorMessage: ChatMessage = {
            id: `err_${Date.now()}`,
            role: "model",
            text: response.error || "Unable to reach Grok AI. Verified catalog fallback is active.",
            isError: true,
            errorHeading: "Grok AI Temporarily Unavailable",
            queryAttempted: query,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          };
          setMessages((prev) => [...prev, errorMessage]);
        }
      } else {
        const historyPayload: ChatHistoryItem[] = messages.map((m) => ({
          role: m.role === "user" ? "user" : "model",
          text: m.text,
        }));

        const response = await sendGeminiChatMessage({
          message: query,
          history: historyPayload,
          role: geminiRole,
          customSystemInstruction: geminiRole === "custom" ? customSystemInstruction : undefined,
          modelPreference: geminiModelTier,
        });

        if (response.ok && response.answer) {
          const modelMessage: ChatMessage = {
            id: `mod_${Date.now()}`,
            role: "model",
            text: response.answer,
            modelUsed: response.modelUsed,
            fallbackNotice: response.fallbackNotice,
            durationMs: response.durationMs,
            matchedProducts: response.matchedProducts,
            followUps: response.followUps,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          };
          setMessages((prev) => [...prev, modelMessage]);
        } else {
          const errorMessage: ChatMessage = {
            id: `err_${Date.now()}`,
            role: "model",
            text: response.error || "Unable to reach Gemini AI. Verified catalog fallback is active.",
            isError: true,
            errorHeading: "Gemini AI Temporarily Unavailable",
            queryAttempted: query,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          };
          setMessages((prev) => [...prev, errorMessage]);
        }
      }
    } catch (err: any) {
      const errorMessage: ChatMessage = {
        id: `err_${Date.now()}`,
        role: "model",
        text: err?.message || "An unexpected error occurred while communicating with the shopping assistant.",
        isError: true,
        errorHeading: "Connection Interrupted",
        queryAttempted: query,
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setLoading(false);
    }
  };

  const handleAddToCart = (product: ProductItem) => {
    addToCart(product, 1);
    setAddedItemIds((prev) => ({ ...prev, [product.id]: true }));
    setTimeout(() => {
      setAddedItemIds((prev) => ({ ...prev, [product.id]: false }));
    }, 2500);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const activeRoleConfig =
    aiEngine === "grok" ? GROK_ROLES[grokRole] : GEMINI_ROLES[geminiRole];

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#f7f7f2] text-slate-900 flex flex-col">
      {/* Header Banner */}
      <header className="border-b border-[#e6e8df] bg-white px-4 py-3 sm:px-6">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className={`h-10 w-10 rounded-xl flex items-center justify-center shadow-sm transition-colors ${
              aiEngine === "grok" ? "bg-black text-amber-400" : "bg-gradient-to-br from-emerald-600 to-teal-700 text-white"
            }`}>
              {aiEngine === "grok" ? <Zap className="h-5 w-5" /> : <Sparkles className="h-5 w-5" />}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-slate-900 tracking-tight">
                  {aiEngine === "grok" ? "Grok AI Assistant" : "Gemini AI Assistant"}
                </h1>
                <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold border ${
                  aiEngine === "grok"
                    ? "bg-amber-50 text-amber-900 border-amber-300"
                    : "bg-emerald-50 text-emerald-800 border-emerald-200/60"
                }`}>
                  <Bot className="h-3 w-3" />
                  {aiEngine === "grok" ? "xAI Grok & Groq" : "Google Gemini"}
                </span>
              </div>
              <p className="text-xs text-slate-500">
                {aiEngine === "grok"
                  ? "Maximum truth-seeking, first-principles hardware breakdown & commerce copilot"
                  : "Powered by official @google/genai SDK with multi-role system instructions"}
              </p>
            </div>
          </div>

          {/* Controls: Engine Selector & Model Tier */}
          <div className="flex items-center flex-wrap gap-2.5">
            {/* Engine Selector */}
            <div className="flex items-center bg-slate-100 p-1 rounded-lg border border-slate-200">
              <button
                type="button"
                onClick={() => handleEngineSwitch("grok")}
                className={`px-3 py-1 text-xs font-bold rounded-md transition-all ${
                  aiEngine === "grok" ? "bg-black text-amber-400 shadow-xs" : "text-slate-600 hover:text-slate-900"
                }`}
              >
                ⚡ Grok AI
              </button>
              <button
                type="button"
                onClick={() => handleEngineSwitch("gemini")}
                className={`px-3 py-1 text-xs font-bold rounded-md transition-all ${
                  aiEngine === "gemini" ? "bg-[#174c3c] text-white shadow-xs" : "text-slate-600 hover:text-slate-900"
                }`}
              >
                ✨ Gemini
              </button>
            </div>

            {/* Model Tier Selector */}
            {aiEngine === "grok" ? (
              <select
                value={grokModelTier}
                onChange={(e) => setGrokModelTier(e.target.value as GrokModelTier)}
                className="text-xs font-semibold bg-slate-50 border border-slate-300 rounded-lg px-2.5 py-1.5 text-slate-800 focus:outline-none focus:ring-2 focus:ring-amber-500"
              >
                <option value="auto">🤖 Auto-Detect (Task Based)</option>
                <option value="grok-2-latest">⚡ Grok 2 Latest (xAI)</option>
                <option value="openai/gpt-oss-120b">🧠 OpenAI GPT-OSS (Reasoning)</option>
                <option value="groq-fast">🚀 Groq Ultra-Fast</option>
              </select>
            ) : (
              <select
                value={geminiModelTier}
                onChange={(e) => setGeminiModelTier(e.target.value as ModelTier)}
                className="text-xs font-semibold bg-slate-50 border border-slate-300 rounded-lg px-2.5 py-1.5 text-slate-800 focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                <option value="auto">🤖 Auto-Detect (Task Based)</option>
                <option value="gemini-3.5-flash">✨ Gemini 3.5 Flash (General Tasks)</option>
                <option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash-Lite (Fast Tasks)</option>
                <option value="gemini-3.1-pro-preview">🧠 Gemini 3.1 Pro Preview (Complex Tasks)</option>
              </select>
            )}

            <button
              type="button"
              onClick={() => setIsConfigModalOpen(true)}
              className="px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:text-amber-800 bg-white hover:bg-amber-50 rounded-lg border border-slate-300 hover:border-amber-300 transition-colors flex items-center gap-1.5 shadow-2xs"
              title="Configure Custom Model (Ollama, xAI, LM Studio, or custom endpoint)"
            >
              <Sliders className="h-3.5 w-3.5 text-amber-600" />
              <span className="hidden sm:inline">
                {activeCustomConfig ? `${activeCustomConfig.displayName || activeCustomConfig.modelName}` : "Configure AI"}
              </span>
            </button>
            <button
              onClick={handleClearHistory}
              className="p-1.5 text-slate-500 hover:text-rose-600 hover:bg-rose-50 rounded-lg border border-slate-200 transition-colors"
              title="Clear conversation history"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </header>

      {/* Main Workspace */}
      <div className="max-w-7xl w-full mx-auto flex-1 flex flex-col md:flex-row gap-4 p-3 sm:p-6 overflow-hidden">
        {/* Left Sidebar: Roles & System Instruction Controls */}
        <aside className="w-full md:w-80 flex-shrink-0 flex flex-col gap-4">
          {/* Role Selection Card */}
          <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm p-4">
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-3 flex items-center justify-between">
              <span>Assistant Role</span>
              <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                aiEngine === "grok" ? "bg-amber-100 text-amber-900" : "bg-emerald-50 text-emerald-700"
              }`}>
                {aiEngine === "grok" ? "Grok Persona" : "System Prompt"}
              </span>
            </h2>

            <div className="space-y-2">
              {aiEngine === "grok"
                ? (Object.keys(GROK_ROLES) as GrokRole[]).map((roleKey) => {
                    const config = GROK_ROLES[roleKey];
                    const isSelected = grokRole === roleKey;
                    return (
                      <button
                        key={roleKey}
                        onClick={() => handleRoleChange(roleKey)}
                        className={`w-full text-left p-3 rounded-lg border transition-all ${
                          isSelected
                            ? "border-amber-500 bg-amber-50/50 shadow-xs"
                            : "border-slate-200 bg-white hover:bg-slate-50 text-slate-700"
                        }`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          {config.icon}
                          <span className={`text-sm font-semibold ${isSelected ? "text-amber-950" : "text-slate-900"}`}>
                            {config.name}
                          </span>
                        </div>
                        <p className="text-xs text-slate-500 leading-snug">{config.tagline}</p>
                      </button>
                    );
                  })
                : (Object.keys(GEMINI_ROLES) as GeminiRole[]).map((roleKey) => {
                    const config = GEMINI_ROLES[roleKey];
                    const isSelected = geminiRole === roleKey;
                    return (
                      <button
                        key={roleKey}
                        onClick={() => handleRoleChange(roleKey)}
                        className={`w-full text-left p-3 rounded-lg border transition-all ${
                          isSelected
                            ? "border-emerald-600 bg-emerald-50/50 shadow-xs"
                            : "border-slate-200 bg-white hover:bg-slate-50 text-slate-700"
                        }`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          {config.icon}
                          <span className={`text-sm font-semibold ${isSelected ? "text-emerald-950" : "text-slate-900"}`}>
                            {config.name}
                          </span>
                        </div>
                        <p className="text-xs text-slate-500 leading-snug">{config.tagline}</p>
                      </button>
                    );
                  })}
            </div>
          </div>

          {/* System Instruction Inspector / Editor */}
          <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm p-4">
            <button
              onClick={() => setIsSystemInstructionOpen(!isSystemInstructionOpen)}
              className="w-full flex items-center justify-between text-xs font-bold uppercase tracking-wider text-slate-500"
            >
              <span className="flex items-center gap-1.5">
                <Settings className="h-3.5 w-3.5 text-slate-600" />
                <span>System Instruction</span>
              </span>
              {isSystemInstructionOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>

            {isSystemInstructionOpen && (
              <div className="mt-3 pt-3 border-t border-slate-100">
                {(aiEngine === "grok" ? grokRole : geminiRole) === "custom" ? (
                  <div>
                    <label className="text-[11px] font-semibold text-slate-600 block mb-1">
                      Custom System Instruction:
                    </label>
                    <textarea
                      value={customSystemInstruction}
                      onChange={(e) => setCustomSystemInstruction(e.target.value)}
                      rows={6}
                      className="w-full text-xs font-mono p-2 border border-slate-200 rounded-lg bg-slate-50 text-slate-800 focus:outline-none focus:ring-2 focus:ring-amber-500"
                      placeholder="Enter custom prompt instructions..."
                    />
                  </div>
                ) : (
                  <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-200">
                    <p className="text-[11px] font-mono text-slate-600 whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto">
                      {activeRoleConfig.defaultPrompt}
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </aside>

        {/* Right Section: Messages Stream & Input */}
        <main className="flex-1 flex flex-col bg-white rounded-xl border border-slate-200/90 shadow-sm overflow-hidden">
          {/* Chat Messages Stream */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
            {messages.map((msg) => {
              const isUser = msg.role === "user";
              return (
                <div
                  key={msg.id}
                  className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}
                >
                  {!isUser && (
                    <div className={`h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 shadow-xs mt-1 ${
                      aiEngine === "grok" ? "bg-black text-amber-400" : "bg-gradient-to-tr from-emerald-700 to-teal-600 text-white"
                    }`}>
                      {aiEngine === "grok" ? <Zap className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                    </div>
                  )}

                  <div
                    className={`max-w-[85%] rounded-2xl p-3.5 space-y-2.5 ${
                      isUser
                        ? "bg-slate-900 text-white rounded-tr-none shadow-xs"
                        : msg.isError
                        ? "bg-amber-50 border border-amber-200 text-slate-800 rounded-tl-none shadow-xs"
                        : "bg-slate-50 border border-slate-200 text-slate-800 rounded-tl-none"
                    }`}
                  >
                    {/* Model & Latency Badges (For Assistant Messages) */}
                    {!isUser && !msg.isError && (
                      <div className="flex flex-wrap items-center gap-2 pb-1 border-b border-slate-200 text-[10px] text-slate-500">
                        <span className={`font-bold px-1.5 py-0.5 rounded ${
                          aiEngine === "grok" ? "bg-black text-amber-400" : "bg-emerald-100 text-emerald-800"
                        }`}>
                          {msg.modelUsed || (aiEngine === "grok" ? "grok-2" : "gemini-3.5-flash")}
                        </span>
                        {msg.durationMs && (
                          <span className="text-slate-400">⚡ {msg.durationMs}ms</span>
                        )}
                        <span className="text-slate-400 ml-auto">{msg.timestamp}</span>
                      </div>
                    )}

                    {/* Message Body */}
                    <div className="text-xs leading-relaxed">
                      {msg.isError ? (
                        <div className="space-y-2">
                          <div className="flex items-center gap-1.5 text-amber-800 font-bold">
                            <AlertTriangle className="h-4 w-4 text-amber-600" />
                            <span>{msg.errorHeading || "Assistant Temporarily Unavailable"}</span>
                          </div>
                          <p className="text-slate-700">{msg.text}</p>
                          <div className="pt-2 flex flex-wrap gap-2">
                            {msg.queryAttempted && (
                              <Link
                                href={`/search?q=${encodeURIComponent(msg.queryAttempted)}`}
                                className="inline-flex items-center gap-1 rounded-lg bg-amber-600 text-white px-2.5 py-1.5 text-xs font-semibold shadow-xs hover:bg-amber-700 transition"
                              >
                                <Search className="h-3.5 w-3.5 text-white" />
                                <span>Search Store</span>
                              </Link>
                            )}
                            <Link
                              href="/category/laptops"
                              className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50"
                            >
                              <span>Laptops</span>
                            </Link>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="whitespace-pre-wrap">
                            {msg.text && msg.text.trim()
                              ? msg.text
                              : "I am ready to help you discover products, compare specifications, and complete your order."}
                          </div>

                          {msg.fallbackNotice && (
                            <div className="mt-2.5 pt-2 border-t border-slate-200 text-[11px] text-amber-700 bg-amber-50/80 p-2 rounded-lg flex items-center gap-1.5">
                              <HelpCircle className="h-3.5 w-3.5 text-amber-600 flex-shrink-0" />
                              <span>{msg.fallbackNotice}</span>
                            </div>
                          )}
                        </>
                      )}
                    </div>

                    {/* Matched Product Cards (if any) */}
                    {msg.matchedProducts && msg.matchedProducts.length > 0 && (
                      <div className="pt-2 space-y-2">
                        <p className="text-[11px] font-bold text-slate-500 uppercase tracking-wider text-left">
                          Verified Catalog Products Referenced:
                        </p>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-left">
                          {msg.matchedProducts.map((prod) => {
                            const isAdded = addedItemIds[prod.id];
                            return (
                              <div
                                key={prod.id}
                                className="bg-white border border-slate-200 rounded-xl p-3 flex gap-3 items-center hover:border-amber-300 transition-all shadow-xs"
                              >
                                {prod.imageUrl ? (
                                  <img
                                    src={prod.imageUrl}
                                    alt={prod.title}
                                    className="h-14 w-14 object-cover rounded-lg flex-shrink-0 bg-slate-50"
                                  />
                                ) : (
                                  <div className="h-14 w-14 bg-slate-100 rounded-lg flex items-center justify-center text-slate-400 flex-shrink-0">
                                    <ShoppingBag className="h-6 w-6" />
                                  </div>
                                )}
                                <div className="flex-1 min-w-0">
                                  <h4 className="text-xs font-bold text-slate-900 truncate" title={prod.title}>
                                    {prod.title}
                                  </h4>
                                  <div className="flex items-center gap-2 mt-0.5">
                                    <span className="text-xs font-bold text-slate-900">
                                      {formatMinorToMajor(prod.priceMinor, prod.currency)}
                                    </span>
                                    <span className="text-[10px] text-amber-600 font-semibold">
                                      ★ {prod.rating}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-2 mt-2">
                                    <button
                                      onClick={() => handleAddToCart(prod)}
                                      className={`text-[10px] font-semibold px-2 py-1 rounded-md transition-all flex items-center gap-1 ${
                                        isAdded
                                          ? "bg-emerald-600 text-white"
                                          : "bg-slate-100 text-slate-800 hover:bg-slate-200 border border-slate-200"
                                      }`}
                                    >
                                      {isAdded ? (
                                        <>
                                          <CheckCircle2 className="h-3 w-3" /> Added
                                        </>
                                      ) : (
                                        <>
                                          <ShoppingBag className="h-3 w-3" /> Add to Bag
                                        </>
                                      )}
                                    </button>
                                    <Link
                                      href={`/product/${prod.id}`}
                                      className="text-[10px] font-semibold px-2 py-1 rounded-md bg-slate-100 text-slate-700 hover:bg-slate-200 transition-all flex items-center gap-1"
                                    >
                                      <span>View</span>
                                      <ExternalLink className="h-2.5 w-2.5" />
                                    </Link>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}

                    {/* Follow-up Suggestion Chips */}
                    {msg.followUps && msg.followUps.length > 0 && (
                      <div className="pt-2 flex flex-wrap gap-1.5 justify-start">
                        {msg.followUps.map((chip, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleSendMessage(chip)}
                            className="text-[11px] px-2.5 py-1 rounded-full bg-white border border-slate-200 text-slate-600 hover:bg-amber-50 hover:text-amber-900 hover:border-amber-300 font-medium transition-all shadow-2xs"
                          >
                            💡 {chip}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {isUser && (
                    <div className="h-8 w-8 rounded-full bg-slate-800 text-white flex items-center justify-center flex-shrink-0 shadow-xs mt-1">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              );
            })}

            {loading && (
              <div className="flex gap-3 items-center text-slate-500">
                <div className={`h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 shadow-xs ${
                  aiEngine === "grok" ? "bg-black text-amber-400" : "bg-gradient-to-tr from-emerald-700 to-teal-600 text-white"
                }`}>
                  <Bot className="h-4 w-4 animate-spin" />
                </div>
                <div className="bg-slate-50 border border-slate-200 rounded-2xl rounded-tl-none px-4 py-3 text-xs flex items-center gap-2">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-amber-500" />
                  <span>{aiEngine === "grok" ? "Grok AI is analyzing..." : "Gemini is generating response..."}</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Starter Chips on Initial or Empty States */}
          {messages.length <= 1 && (
            <div className="px-4 py-2 bg-slate-50 border-t border-slate-200/80">
              <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">
                Suggested First-Principles Inquiries:
              </p>
              <div className="flex flex-wrap gap-1.5">
                {activeRoleConfig.starters.map((starter, i) => (
                  <button
                    key={i}
                    onClick={() => handleSendMessage(starter)}
                    className="text-xs bg-white border border-slate-200/80 text-slate-700 hover:bg-amber-50 hover:text-amber-900 hover:border-amber-300 px-3 py-1.5 rounded-lg transition text-left"
                  >
                    💬 {starter}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Chat Input Bar */}
          <div className="p-3 border-t border-slate-200 bg-white">
            <div className="flex items-end gap-2 bg-slate-50 border border-slate-200 rounded-xl p-2 focus-within:ring-2 focus-within:ring-amber-500 focus-within:border-transparent transition-all">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                placeholder={
                  aiEngine === "grok"
                    ? "Ask Grok AI about specs, thermal limits, price-performance tradeoffs, or hardware teardowns..."
                    : "Ask Gemini about products, specs, thermal performance, or checkout rules..."
                }
                className="flex-1 bg-transparent border-0 resize-none text-xs text-slate-900 placeholder:text-slate-400 focus:outline-none min-h-[2.25rem] max-h-32 py-1"
              />
              <button
                type="button"
                onClick={() => handleSendMessage()}
                disabled={!input.trim() || loading}
                className={`p-2 rounded-lg text-white transition-all ${
                  input.trim() && !loading
                    ? (aiEngine === "grok" ? "bg-black hover:bg-slate-800 text-amber-400 shadow-sm" : "bg-[#174c3c] hover:bg-[#103c2f] shadow-sm")
                    : "bg-slate-300 text-slate-500 cursor-not-allowed"
                }`}
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </main>
      </div>
      <AIModelConfigModal
        isOpen={isConfigModalOpen}
        onClose={() => setIsConfigModalOpen(false)}
        onConfigSaved={(cfg) => setActiveCustomConfig(cfg)}
      />
    </div>
  );
}
