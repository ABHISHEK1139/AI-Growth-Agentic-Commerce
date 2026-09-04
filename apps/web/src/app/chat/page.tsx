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
  Clock,
  Settings,
  HelpCircle,
  AlertTriangle,
  Search,
} from "lucide-react";
import { useStore } from "@/context/StoreContext";
import { formatMinorToMajor } from "@/lib/money";
import type { ProductItem } from "@/data/products";
import {
  sendGeminiChatMessage,
  type GeminiRole,
  type ModelTier,
  type ChatHistoryItem,
} from "@/catalog/geminiClient";

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

const ROLE_CONFIGS: Record<
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
    tagline: "Deep technical comparisons, silicon benchmarks & developer workflows",
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

export default function GeminiChatPage() {
  const router = useRouter();
  const { addToCart } = useStore();

  const [activeRole, setActiveRole] = useState<GeminiRole>("concierge");
  const [modelPreference, setModelPreference] = useState<ModelTier>("auto");
  const [customSystemInstruction, setCustomSystemInstruction] = useState("");
  const [isSystemInstructionOpen, setIsSystemInstructionOpen] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "msg_init",
      role: "model",
      text: "Welcome! I am your **Google Gemini AI Assistant**.\n\nI can help you browse our verified store catalog, compare hardware specifications, break down thermal and silicon benchmarks, or guide autonomous checkout rules. What would you like to explore?",
      modelUsed: "gemini-3.5-flash",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      followUps: [
        "Find me a laptop for programming under ₹75,000",
        "4K monitor for coding with USB-C PD",
        "Noise-cancelling wireless headphones for travel",
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

  const handleRoleChange = (newRole: GeminiRole) => {
    setActiveRole(newRole);
    if (newRole === "custom" && !customSystemInstruction) {
      setCustomSystemInstruction(ROLE_CONFIGS.custom.defaultPrompt);
    }
  };

  const handleClearHistory = () => {
    setMessages([
      {
        id: `msg_${Date.now()}`,
        role: "model",
        text: `Switched context to **${ROLE_CONFIGS[activeRole].name}**. How can I assist you?`,
        modelUsed: "gemini-3.5-flash",
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        followUps: ROLE_CONFIGS[activeRole].starters.slice(0, 3),
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

    // Build history items
    const historyPayload: ChatHistoryItem[] = messages.map((m) => ({
      role: m.role === "user" ? "user" : "model",
      text: m.text,
    }));

    try {
      const response = await sendGeminiChatMessage({
        message: query,
        history: historyPayload,
        role: activeRole,
        customSystemInstruction: activeRole === "custom" ? customSystemInstruction : undefined,
        modelPreference,
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
          text: response.error || "We couldn't connect to the AI model right now. You can try again or browse products directly in our catalog.",
          isError: true,
          errorHeading: "Assistant Temporarily Unavailable",
          queryAttempted: query,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        };
        setMessages((prev) => [...prev, errorMessage]);
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

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[#f7f7f2] text-slate-900 flex flex-col">
      {/* Header Banner */}
      <header className="border-b border-[#e6e8df] bg-white px-4 py-3 sm:px-6">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-emerald-600 to-teal-700 text-white flex items-center justify-center shadow-sm">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-slate-900 tracking-tight">Gemini AI Assistant</h1>
                <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-800 border border-emerald-200/60">
                  <Bot className="h-3 w-3" /> Multi-Turn Chat
                </span>
              </div>
              <p className="text-xs text-slate-500">
                Powered by official <span className="font-semibold text-slate-700">@google/genai</span> SDK with multi-role system instructions
              </p>
            </div>
          </div>

          {/* Quick Model Selector in Header */}
          <div className="flex items-center gap-2">
            <label className="text-xs font-semibold text-slate-600 hidden sm:inline">Model Tier:</label>
            <select
              value={modelPreference}
              onChange={(e) => setModelPreference(e.target.value as ModelTier)}
              className="text-xs font-semibold bg-slate-50 border border-slate-300 rounded-lg px-2.5 py-1.5 text-slate-800 focus:outline-none focus:ring-2 focus:ring-emerald-500"
            >
              <option value="auto">🤖 Auto-Detect (Task Based)</option>
              <option value="gemini-3.5-flash">✨ Gemini 3.5 Flash (General Tasks)</option>
              <option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash-Lite (Fast Tasks)</option>
              <option value="gemini-3.1-pro-preview">🧠 Gemini 3.1 Pro Preview (Complex Tasks)</option>
            </select>

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
              <span className="text-[10px] text-emerald-700 font-semibold bg-emerald-50 px-1.5 py-0.5 rounded">System Prompt</span>
            </h2>

            <div className="space-y-2">
              {(Object.keys(ROLE_CONFIGS) as GeminiRole[]).map((roleKey) => {
                const config = ROLE_CONFIGS[roleKey];
                const isSelected = activeRole === roleKey;
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
                {activeRole === "custom" ? (
                  <div>
                    <label className="text-[11px] font-semibold text-slate-600 block mb-1">
                      Custom System Instruction:
                    </label>
                    <textarea
                      value={customSystemInstruction}
                      onChange={(e) => setCustomSystemInstruction(e.target.value)}
                      rows={6}
                      className="w-full text-xs font-mono p-2 border border-slate-200 rounded-lg bg-slate-50 text-slate-800 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                      placeholder="Enter custom prompt instructions for Gemini..."
                    />
                  </div>
                ) : (
                  <div>
                    <p className="text-[11px] text-slate-500 mb-2">
                      Active instruction passed to Gemini API for this persona:
                    </p>
                    <div className="text-[11px] font-mono text-slate-700 bg-slate-50 p-2.5 rounded-lg border border-slate-200 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                      {ROLE_CONFIGS[activeRole].defaultPrompt}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Model Architecture Guide */}
          <div className="bg-white rounded-xl border border-slate-200/90 shadow-sm p-4 text-xs text-slate-600 space-y-2 hidden md:block">
            <h3 className="font-bold text-slate-800 flex items-center gap-1.5">
              <Zap className="h-4 w-4 text-amber-500" />
              <span>Model Routing Logic</span>
            </h3>
            <ul className="space-y-1.5 text-[11px] text-slate-600">
              <li className="flex items-start gap-1.5">
                <span className="font-bold text-slate-800">⚡ gemini-3.1-flash-lite:</span>
                <span>Fast tasks, short price/stock lookups, quick single-item answers.</span>
              </li>
              <li className="flex items-start gap-1.5">
                <span className="font-bold text-slate-800">✨ gemini-3.5-flash:</span>
                <span>General tasks, multi-turn conversational shopping, pros/cons.</span>
              </li>
              <li className="flex items-start gap-1.5">
                <span className="font-bold text-slate-800">🧠 gemini-3.1-pro-preview:</span>
                <span>Complex tasks, deep silicon & architectural comparisons, benchmark synthesis.</span>
              </li>
            </ul>
          </div>
        </aside>

        {/* Right Area: Scrollable Chat Thread & Input */}
        <main className="flex-1 flex flex-col bg-white rounded-xl border border-slate-200/90 shadow-sm overflow-hidden min-h-[500px]">
          {/* Thread Message Container */}
          <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-5">
            {messages.map((msg) => {
              const isUser = msg.role === "user";

              return (
                <div key={msg.id} className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
                  {!isUser && (
                    <div className="h-8 w-8 rounded-full bg-gradient-to-tr from-emerald-700 to-teal-600 text-white flex items-center justify-center flex-shrink-0 shadow-xs mt-1">
                      <Bot className="h-4 w-4" />
                    </div>
                  )}

                  <div className={`max-w-[85%] sm:max-w-[75%] space-y-2 ${isUser ? "text-right" : "text-left"}`}>
                    {/* Meta info badge */}
                    <div className="flex items-center gap-2 text-[10px] text-slate-400 font-medium px-1">
                      <span>{msg.timestamp}</span>
                      {msg.modelUsed && (
                        <span className="inline-flex items-center gap-1 rounded bg-slate-100 text-slate-600 px-1.5 py-0.5 font-mono text-[9px] border border-slate-200">
                          {msg.modelUsed.includes("lite") ? (
                            <Zap className="h-2.5 w-2.5 text-amber-600" />
                          ) : msg.modelUsed.includes("pro") ? (
                            <Cpu className="h-2.5 w-2.5 text-indigo-600" />
                          ) : (
                            <Sparkles className="h-2.5 w-2.5 text-emerald-600" />
                          )}
                          {msg.modelUsed}
                        </span>
                      )}
                      {msg.durationMs && (
                        <span className="text-[9px] font-mono text-slate-400">
                          {msg.durationMs}ms
                        </span>
                      )}
                    </div>

                    {/* Chat Bubble */}
                    <div
                      className={`p-4 rounded-2xl text-sm leading-relaxed ${
                        isUser
                          ? "bg-[#174c3c] text-white rounded-tr-none shadow-xs font-medium"
                          : msg.isError
                          ? "bg-amber-50/95 text-slate-800 rounded-tl-none border border-amber-200 shadow-xs"
                          : "bg-slate-50 text-slate-800 rounded-tl-none border border-slate-200/80 shadow-xs"
                      }`}
                    >
                      {msg.isError ? (
                        <div className="space-y-3">
                          <div className="flex items-start gap-3">
                            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-amber-100 text-amber-800 border border-amber-200">
                              <AlertTriangle className="h-4 w-4" />
                            </div>
                            <div className="flex-1">
                              <h4 className="text-sm font-bold text-slate-900">
                                {msg.errorHeading || "Assistant Temporarily Unavailable"}
                              </h4>
                              <p className="mt-1 text-xs leading-relaxed text-slate-600 font-normal">
                                {msg.text && msg.text.trim()
                                  ? msg.text
                                  : "We couldn't connect to the AI model right now, but our verified catalog, search, and checkout are fully operational."}
                              </p>
                            </div>
                          </div>

                          {/* Fallback recovery actions */}
                          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-amber-200/60">
                            {msg.queryAttempted && (
                              <button
                                type="button"
                                onClick={() => handleSendMessage(msg.queryAttempted)}
                                className="inline-flex items-center gap-1.5 rounded-lg bg-[#174c3c] px-3 py-1.5 text-xs font-bold text-white transition hover:bg-[#103c2f] shadow-xs active:scale-95"
                              >
                                <RefreshCw className="h-3.5 w-3.5" />
                                <span>Try again</span>
                              </button>
                            )}
                            {msg.queryAttempted && (
                              <Link
                                href={`/search?q=${encodeURIComponent(msg.queryAttempted)}`}
                                className="inline-flex items-center gap-1.5 rounded-lg bg-white border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                              >
                                <Search className="h-3.5 w-3.5 text-slate-500" />
                                <span>Search Store</span>
                              </Link>
                            )}
                            <Link
                              href="/category/laptops"
                              className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50"
                            >
                              <span>Laptops</span>
                            </Link>
                            <Link
                              href="/search?deals=true"
                              className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50"
                            >
                              <span>Deals</span>
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

                          {/* Fallback Notice (if free-tier quota handled gracefully) */}
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
                                className="bg-white border border-slate-200 rounded-xl p-3 flex gap-3 items-center hover:border-emerald-300 transition-all shadow-xs"
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
                                    <span className="text-xs font-bold text-emerald-800">
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
                                          : "bg-emerald-50 text-emerald-800 hover:bg-emerald-100 border border-emerald-200"
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
                            className="text-[11px] px-2.5 py-1 rounded-full bg-white border border-slate-200 text-slate-600 hover:bg-emerald-50 hover:text-emerald-800 hover:border-emerald-300 font-medium transition-all shadow-2xs"
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
                <div className="h-8 w-8 rounded-full bg-gradient-to-tr from-emerald-700 to-teal-600 text-white flex items-center justify-center flex-shrink-0 shadow-xs">
                  <Bot className="h-4 w-4 animate-spin" />
                </div>
                <div className="bg-slate-50 border border-slate-200 rounded-2xl rounded-tl-none px-4 py-3 text-xs flex items-center gap-2">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-emerald-600" />
                  <span>Gemini is generating response...</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Starter Chips on Initial or Empty States */}
          {messages.length <= 1 && (
            <div className="px-4 py-2 border-t border-slate-100 bg-slate-50/50">
              <p className="text-[11px] font-semibold text-slate-500 mb-1.5">Suggested Questions:</p>
              <div className="flex flex-wrap gap-1.5">
                {ROLE_CONFIGS[activeRole].starters.map((starter, i) => (
                  <button
                    key={i}
                    onClick={() => handleSendMessage(starter)}
                    className="text-xs px-2.5 py-1 rounded-lg bg-white border border-slate-200 text-slate-700 hover:bg-emerald-50 hover:text-emerald-800 hover:border-emerald-300 transition-all font-medium"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Input Area */}
          <div className="p-3 sm:p-4 border-t border-slate-200 bg-slate-50/80">
            <div className="relative flex items-end gap-2 bg-white rounded-xl border border-slate-300 focus-within:border-emerald-600 focus-within:ring-2 focus-within:ring-emerald-500/20 p-2 shadow-xs transition-all">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={2}
                placeholder={`Ask ${ROLE_CONFIGS[activeRole].name} anything about our products, specs, or orders... (Press Enter to send)`}
                className="w-full resize-none text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none bg-transparent px-2 py-1 max-h-32"
              />

              <button
                onClick={() => handleSendMessage()}
                disabled={!input.trim() || loading}
                className="inline-flex items-center justify-center h-10 w-10 rounded-lg bg-[#174c3c] text-white hover:bg-[#103c2f] disabled:opacity-40 disabled:hover:bg-[#174c3c] transition-all flex-shrink-0 shadow-xs"
                title="Send message"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
            <div className="flex items-center justify-between mt-2 px-1 text-[11px] text-slate-400">
              <span>Shift + Enter for new line</span>
              <span>Active Model: <strong className="text-slate-600 font-mono">{modelPreference}</strong></span>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
