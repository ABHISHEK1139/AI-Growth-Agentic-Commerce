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
  Store,
} from "lucide-react";
import { useStore } from "@/context/StoreContext";
import { getStoredModelConfig, type CustomModelConfig } from "@/catalog/modelConfig";
import { formatMinorToMajor } from "@/lib/money";
import type { ProductItem } from "@/data/products";
import {
  sendGeminiChatMessage,
  type GeminiRole,
  type ChatHistoryItem,
} from "@/catalog/geminiClient";
import {
  sendGrokChatMessage,
  type GrokRole,
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

const COMMERCE_ROLES: Record<
  GrokRole,
  {
    name: string;
    icon: React.ReactNode;
    tagline: string;
    geminiRole: GeminiRole;
    starters: string[];
    defaultPrompt: string;
  }
> = {
  grok_teardown: {
    name: "First-Principles Teardown",
    icon: <Zap className="h-4 w-4 text-amber-500" />,
    tagline: "First-principles hardware teardown, thermal bottlenecks & unfiltered value truth",
    geminiRole: "hardware_specialist",
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
    tagline: "Personal buying advisor for verified electronics, budget fits & accessories",
    geminiRole: "concierge",
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
    geminiRole: "hardware_specialist",
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
    name: "Risk & Policy Auditor",
    icon: <ShieldCheck className="h-4 w-4 text-amber-600" />,
    tagline: "Autonomous spending caps, auto-approval thresholds & risk compliance",
    geminiRole: "merchant_auditor",
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
    geminiRole: "custom",
    starters: [
      "Explain the key factors to consider when buying a laptop in 3 concise bullet points",
      "Compare Dell vs HP warranties and after-sales service reliability in India",
      "What is the best mechanical keyboard switch type for quiet office programming?",
    ],
    defaultPrompt: `You are an adaptable AI shopping and technical commerce assistant. Follow the shopper's instructions precisely while remaining objective, accurate, and helpful.`,
  },
};

export default function AIChatPage() {
  const router = useRouter();
  const { addToCart } = useStore();

  const [activeCustomConfig, setActiveCustomConfig] = useState<CustomModelConfig | null>(null);

  useEffect(() => {
    const stored = getStoredModelConfig();
    if (stored) {
      setActiveCustomConfig(stored);
    }
  }, []);

  const [selectedRole, setSelectedRole] = useState<GrokRole>("grok_teardown");
  const [customSystemInstruction, setCustomSystemInstruction] = useState("");
  const [isSystemInstructionOpen, setIsSystemInstructionOpen] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "msg_init",
      role: "model",
      text: "Welcome! I am **AgentPay AI Commerce Intelligence**.\n\nI provide maximum truth-seeking hardware teardowns, first-principles silicon comparisons, zero-BS pricing breakdowns, and autonomous checkout assistance. What would you like to evaluate?",
      modelUsed: "auto-routed",
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

  const handleRoleChange = (roleKey: GrokRole) => {
    setSelectedRole(roleKey);
    if (roleKey === "custom" && !customSystemInstruction) {
      setCustomSystemInstruction(COMMERCE_ROLES.custom.defaultPrompt);
    }
  };

  const handleClearHistory = () => {
    const roleConfig = COMMERCE_ROLES[selectedRole] || COMMERCE_ROLES.grok_teardown;
    setMessages([
      {
        id: `msg_${Date.now()}`,
        role: "model",
        text: `Reset conversation context to **${roleConfig.name}**. How can I assist you?`,
        modelUsed: "auto-routed",
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        followUps: roleConfig.starters.slice(0, 3),
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
      // 1. Primary route: Grok / Configured Local Model
      const historyPayload: any[] = messages.map((m) => ({
        role: m.role === "user" ? "user" : "assistant",
        text: m.text,
      }));

      const activeRoleConfig = COMMERCE_ROLES[selectedRole] || COMMERCE_ROLES.grok_teardown;

      let response = await sendGrokChatMessage({
        message: query,
        history: historyPayload,
        role: selectedRole,
        customSystemInstruction: selectedRole === "custom" ? customSystemInstruction : undefined,
        modelPreference: "auto",
        customConfig: activeCustomConfig || getStoredModelConfig() || undefined,
      });

      // 2. Cascading fallback to Gemini if primary returned empty or error
      if (!response.ok || !response.answer) {
        const geminiHistory: ChatHistoryItem[] = messages.map((m) => ({
          role: m.role === "user" ? "user" : "model",
          text: m.text,
        }));

        const geminiRes = await sendGeminiChatMessage({
          message: query,
          history: geminiHistory,
          role: activeRoleConfig.geminiRole,
          customSystemInstruction: selectedRole === "custom" ? customSystemInstruction : undefined,
          modelPreference: "auto",
        });

        if (geminiRes.ok && geminiRes.answer) {
          response = {
            ok: true,
            answer: geminiRes.answer,
            modelUsed: geminiRes.modelUsed,
            fallbackNotice: geminiRes.fallbackNotice,
            durationMs: geminiRes.durationMs,
            matchedProducts: geminiRes.matchedProducts,
            followUps: geminiRes.followUps,
          };
        }
      }

      if (response.ok && response.answer) {
        const modelMessage: ChatMessage = {
          id: `mod_${Date.now()}`,
          role: "model",
          text: response.answer,
          modelUsed: response.modelUsed || "auto-routed",
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
          text: response.error || "Unable to reach the assistant. Verified store catalog is active for direct browsing.",
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
    addToCart(product);
    setAddedItemIds((prev) => ({ ...prev, [product.id]: true }));
    setTimeout(() => {
      setAddedItemIds((prev) => ({ ...prev, [product.id]: false }));
    }, 2000);
  };

  const activeRoleConfig = COMMERCE_ROLES[selectedRole] || COMMERCE_ROLES.grok_teardown;

  return (
    <div className="flex flex-col min-h-screen bg-[#f7f8f5] text-slate-900">
      {/* Header Banner */}
      <header className="border-b border-[#e6e8df] bg-white px-4 py-3 sm:px-6 shadow-2xs">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100 text-xs font-bold transition-colors"
              title="Return to Storefront"
            >
              <Store className="h-3.5 w-3.5 text-[#174c3c]" />
              <span className="hidden sm:inline">Storefront</span>
            </Link>

            <div className="h-4 w-px bg-slate-200 hidden sm:block" />

            <div className="h-9 w-9 rounded-xl flex items-center justify-center shadow-xs bg-[#174c3c] text-white">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base sm:text-lg font-bold text-slate-900 tracking-tight">
                  AgentPay AI Commerce Intelligence
                </h1>
                <span className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold border bg-emerald-50 text-emerald-800 border-emerald-200/60">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Auto-Routed Autonomous Brain
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                First-principles hardware teardowns, spec benchmarking, and conversational checkout co-pilot
              </p>
            </div>
          </div>

          {/* Controls: Clean Header Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleClearHistory}
              className="px-3 py-1.5 text-xs font-semibold text-slate-600 hover:text-rose-600 hover:bg-rose-50 rounded-lg border border-slate-200 transition-colors flex items-center gap-1.5 shadow-2xs"
              title="Clear conversation history"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span>Reset Context</span>
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
              <span>Assistant Persona</span>
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-800 border border-emerald-200/60">
                Autonomous
              </span>
            </h2>

            <div className="space-y-2">
              {(Object.keys(COMMERCE_ROLES) as GrokRole[]).map((roleKey) => {
                const config = COMMERCE_ROLES[roleKey];
                const isSelected = selectedRole === roleKey;
                return (
                  <button
                    key={roleKey}
                    onClick={() => handleRoleChange(roleKey)}
                    className={`w-full text-left p-3 rounded-lg border transition-all ${
                      isSelected
                        ? "border-[#174c3c] bg-emerald-50/40 shadow-xs ring-1 ring-[#174c3c]/20"
                        : "border-slate-200 bg-white hover:bg-slate-50 text-slate-700"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      {config.icon}
                      <span className={`text-sm font-semibold ${isSelected ? "text-[#174c3c]" : "text-slate-900"}`}>
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
                {selectedRole === "custom" ? (
                  <div>
                    <label className="text-[11px] font-semibold text-slate-600 block mb-1">
                      Custom System Instruction:
                    </label>
                    <textarea
                      value={customSystemInstruction}
                      onChange={(e) => setCustomSystemInstruction(e.target.value)}
                      rows={6}
                      className="w-full text-xs font-mono p-2 border border-slate-200 rounded-lg bg-slate-50 text-slate-800 focus:outline-none focus:ring-2 focus:ring-[#174c3c]"
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
                    <div className="h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 shadow-xs mt-1 bg-[#174c3c] text-white">
                      <Sparkles className="h-4 w-4" />
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
                    {/* Latency / Timestamp Badges */}
                    {!isUser && !msg.isError && (
                      <div className="flex flex-wrap items-center gap-2 pb-1 border-b border-slate-200 text-[10px] text-slate-500">
                        <span className="font-bold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">
                          {msg.modelUsed || "auto-routed"}
                        </span>
                        {msg.durationMs && (
                          <span className="text-slate-400">⚡ {msg.durationMs}ms</span>
                        )}
                        <span className="text-slate-400 ml-auto">{msg.timestamp}</span>
                      </div>
                    )}

                    {/* Fallback Notice */}
                    {msg.fallbackNotice && (
                      <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-amber-100/70 border border-amber-200 text-[10px] text-amber-900">
                        <AlertTriangle className="h-3 w-3 text-amber-700 flex-shrink-0" />
                        <span>{msg.fallbackNotice}</span>
                      </div>
                    )}

                    {/* Error Header */}
                    {msg.isError && (
                      <div className="flex items-center gap-1.5 text-amber-900 font-bold">
                        <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                        <span>{msg.errorHeading || "Error"}</span>
                      </div>
                    )}

                    {/* Message Body with Markdown Formatting */}
                    <div className="whitespace-pre-wrap leading-relaxed space-y-2">
                      {msg.text.split("\n\n").map((para, i) => (
                        <p key={i}>{para}</p>
                      ))}
                    </div>

                    {/* Matched Product Cards */}
                    {msg.matchedProducts && msg.matchedProducts.length > 0 && (
                      <div className="pt-2 border-t border-slate-200/80 space-y-2">
                        <p className="text-[11px] font-bold text-slate-600 uppercase tracking-wide">
                          Referenced Catalog Products:
                        </p>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          {msg.matchedProducts.map((p) => (
                            <div
                              key={p.id}
                              className="flex items-center justify-between gap-2 p-2 rounded-lg bg-white border border-slate-200 shadow-2xs hover:border-[#174c3c] transition-all"
                            >
                              <div className="min-w-0 flex-1">
                                <h4 className="font-semibold text-slate-900 truncate text-xs">
                                  {p.title}
                                </h4>
                                <div className="flex items-center gap-2 text-[10px] text-slate-500 mt-0.5">
                                  <span className="font-bold text-[#174c3c]">
                                    {formatMinorToMajor(p.priceMinor, p.currency)}
                                  </span>
                                  <span>★ {p.rating}</span>
                                </div>
                              </div>
                              <div className="flex items-center gap-1">
                                <button
                                  type="button"
                                  onClick={() => handleAddToCart(p)}
                                  className={`px-2 py-1 text-[10px] font-bold rounded-md transition-all ${
                                    addedItemIds[p.id]
                                      ? "bg-emerald-600 text-white"
                                      : "bg-[#174c3c] hover:bg-[#103c2f] text-white"
                                  }`}
                                >
                                  {addedItemIds[p.id] ? "Added!" : "+ Add"}
                                </button>
                                <Link
                                  href={`/product/${p.id}`}
                                  className="p-1 text-slate-400 hover:text-slate-600"
                                  title="View product specifications"
                                >
                                  <ExternalLink className="h-3.5 w-3.5" />
                                </Link>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Follow-up Prompts / Starters */}
                    {msg.followUps && msg.followUps.length > 0 && (
                      <div className="pt-2 border-t border-slate-200/80">
                        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">
                          Suggested Explorations:
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {msg.followUps.map((prompt, i) => (
                            <button
                              key={i}
                              onClick={() => handleSendMessage(prompt)}
                              className="text-[11px] text-left px-2.5 py-1 rounded-full bg-white border border-slate-200 hover:border-[#174c3c] hover:bg-emerald-50/50 text-slate-700 transition-all shadow-2xs"
                            >
                              ✦ {prompt}
                            </button>
                          ))}
                        </div>
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
              <div className="flex gap-3 justify-start">
                <div className="h-8 w-8 rounded-full flex items-center justify-center flex-shrink-0 bg-[#174c3c] text-white shadow-xs">
                  <Sparkles className="h-4 w-4 animate-spin" />
                </div>
                <div className="bg-slate-50 border border-slate-200 rounded-2xl rounded-tl-none p-3 max-w-[85%] space-y-1.5">
                  <div className="flex items-center gap-1.5 text-slate-500 font-semibold text-[11px]">
                    <span className="h-2 w-2 rounded-full bg-[#174c3c] animate-pulse" />
                    <span>Analyzing hardware benchmarks and querying catalog...</span>
                  </div>
                  <div className="h-1.5 bg-slate-200 rounded-full w-48 overflow-hidden">
                    <div className="h-full bg-[#174c3c] rounded-full animate-pulse" style={{ width: "60%" }} />
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Prompt Starters Bar (When conversation is fresh) */}
          {messages.length === 1 && (
            <div className="px-4 py-2 bg-slate-50 border-t border-slate-200">
              <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">
                Quick Evaluation Prompts:
              </p>
              <div className="flex flex-wrap gap-1.5">
                {activeRoleConfig.starters.map((starter, i) => (
                  <button
                    key={i}
                    onClick={() => handleSendMessage(starter)}
                    className="text-[11px] text-left px-2.5 py-1 rounded-full bg-white border border-slate-200 hover:border-[#174c3c] hover:bg-emerald-50/50 text-slate-700 transition-all shadow-2xs"
                  >
                    ⚡ {starter}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Input Box */}
          <div className="p-3 bg-white border-t border-slate-200">
            <div className="flex items-end gap-2 bg-slate-50 border border-slate-200 rounded-xl p-2 focus-within:border-[#174c3c] focus-within:ring-1 focus-within:ring-[#174c3c] transition-all">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                rows={1}
                placeholder="Ask about specs, thermal limits, price-performance tradeoffs, or hardware teardowns..."
                className="flex-1 bg-transparent border-0 resize-none text-xs text-slate-900 placeholder:text-slate-400 focus:outline-none min-h-[2.25rem] max-h-32 py-1"
              />
              <button
                type="button"
                onClick={() => handleSendMessage()}
                disabled={!input.trim() || loading}
                className={`p-2 rounded-lg text-white transition-all ${
                  input.trim() && !loading
                    ? "bg-[#174c3c] hover:bg-[#103c2f] shadow-sm"
                    : "bg-slate-300 text-slate-500 cursor-not-allowed"
                }`}
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
