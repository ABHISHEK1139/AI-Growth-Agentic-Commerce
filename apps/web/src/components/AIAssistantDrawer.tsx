"use client";

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useStore } from "@/context/StoreContext";
import type { ProductItem } from "@/data/products";
import { formatMinorToMajor } from "@/lib/money";
import { exploreCatalog, askProductQuestion } from "@/catalog/client";
import { exploreOfferToProductItem } from "@/catalog/adapt";

interface Message {
  id: string;
  sender: "user" | "agent";
  text: string;
  matchedProducts?: ProductItem[];
  highlightedProduct?: ProductItem;
  structuredIntent?: Record<string, any>;
  tradeoffOptions?: Array<{ label: string; action: () => void }>;
  relaxationOptions?: Array<{ label: string; budget: number }>;
  actionSuggestion?: {
    label: string;
    action: () => void;
  };
  researchResult?: {
    source_label: string;
    source_url?: string | null;
    confidence_level: string;
    reason_for_web_search?: string | null;
    transparency_steps: string[];
  };
  timestamp: string;
}

export function AIAssistantDrawer() {
  const router = useRouter();
  const {
    isAiDrawerOpen,
    closeAiDrawer,
    aiDrawerContext,
    currentIntent,
    updateIntent,
    removeIntentConstraint,
    setSortBy,
    setHighlightedProductId,
    addToCart,
    getProductById,
  } = useStore();

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeProductsInView, setActiveProductsInView] = useState<ProductItem[]>([]);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Initialize context when opened
  useEffect(() => {
    if (!isAiDrawerOpen) return;

    if (aiDrawerContext.product) {
      setActiveProductsInView([aiDrawerContext.product]);
    }

    if (aiDrawerContext.customPrompt) {
      handleUserSubmit(aiDrawerContext.customPrompt);
    } else if (messages.length === 0) {
      if (aiDrawerContext.pageType === "product" && aiDrawerContext.product) {
        setMessages([
          {
            id: "msg_init",
            sender: "agent",
            text: `Hi! I'm ready to answer any questions about the **${aiDrawerContext.product.title}** (ports, battery endurance, real customer reviews, or compatibility).`,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } else if (aiDrawerContext.pageType === "compare") {
        setMessages([
          {
            id: "msg_init",
            sender: "agent",
            text: "I can help analyze your comparison set. Tell me your priorities (battery life, weight, or performance) and I will determine the best match.",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } else if (aiDrawerContext.pageType === "checkout") {
        setMessages([
          {
            id: "msg_init",
            sender: "agent",
            text: "Need clarification on delivery windows, merchant return policy, or spending limits before completing payment?",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } else {
        setMessages([
          {
            id: "msg_init",
            sender: "agent",
            text: "Hello! Tell me what you're looking for in plain English. For example:\n• *\"Find me a laptop for programming under ₹70,000\"*\n• *\"Wireless ANC headphones for flight travel\"*",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAiDrawerOpen, aiDrawerContext]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const handleUserSubmit = async (customText?: string) => {
    const query = customText || input;
    if (!query.trim()) return;

    const userMsg: Message = {
      id: `usr_${Date.now()}`,
      sender: "user",
      text: query,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };

    setMessages((prev) => [...prev, userMsg]);
    if (!customText) setInput("");
    setLoading(true);

    const qLower = query.toLowerCase();

    // 1. FAST LOCAL ACTIONS ON CONTEXT (Cart, Select, Sort)
    // Ordinal selection: "second one" / "2nd one"
    if (
      (qLower.includes("second one") || qLower.includes("second product") || qLower.includes("2nd one")) &&
      activeProductsInView.length > 1
    ) {
      const selected = activeProductsInView[1];
      setMessages((prev) => [
        ...prev,
        {
          id: `agt_${Date.now()}`,
          sender: "agent",
          text: `Selected the second option: **${selected.title}** (${formatMinorToMajor(selected.priceMinor, selected.currency)}).\n\nWould you like to review specifications, ask a question, or proceed to checkout?`,
          highlightedProduct: selected,
          actionSuggestion: {
            label: `Open ${selected.title.slice(0, 30)}... →`,
            action: () => {
              closeAiDrawer();
              router.push(`/product/${selected.id}`);
            },
          },
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
      setLoading(false);
      return;
    }

    // "Show me the cheapest" on active items if we already have items and not searching for a new product
    if (
      (qLower.includes("cheapest") || qLower.includes("lowest price")) &&
      activeProductsInView.length > 0 &&
      !qLower.includes("laptop") &&
      !qLower.includes("phone") &&
      !qLower.includes("headphone")
    ) {
      setSortBy("price_asc");
      const sorted = [...activeProductsInView].sort((a, b) => a.priceMinor - b.priceMinor);
      const cheapest = sorted[0];
      setHighlightedProductId(cheapest.id);
      setMessages((prev) => [
        ...prev,
        {
          id: `agt_${Date.now()}`,
          sender: "agent",
          text: `Sorted catalog by **Price: Low to High**. The most affordable option among active results is **${cheapest.title}** at **${formatMinorToMajor(cheapest.priceMinor, cheapest.currency)}**.`,
          highlightedProduct: cheapest,
          actionSuggestion: {
            label: "View Lowest Price Offer →",
            action: () => {
              closeAiDrawer();
              router.push(`/product/${cheapest.id}`);
            },
          },
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
      setLoading(false);
      return;
    }

    // "Add to cart" / "Buy it"
    if (
      qLower.includes("add to cart") ||
      qLower.includes("add it") ||
      qLower.includes("buy it") ||
      qLower.includes("checkout")
    ) {
      const targetProd = aiDrawerContext.product || activeProductsInView[0];
      if (targetProd) {
        addToCart(targetProd, 1);
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text: `✓ Added **${targetProd.title}** (${formatMinorToMajor(targetProd.priceMinor, targetProd.currency)}) to your cart.\n\nInventory reservation and price lock will be applied when you proceed to checkout.`,
            actionSuggestion: {
              label: "Proceed to Gated Checkout →",
              action: () => {
                closeAiDrawer();
                router.push("/checkout");
              },
            },
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text: "Please search and select a product first before adding to cart.",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      }
      setLoading(false);
      return;
    }

    // 2. PRODUCT SPEC Q&A & RESEARCH: Route to POST /api/v1/research/ask
    const isProductSpecQuestion =
      Boolean(aiDrawerContext.product) ||
      qLower.includes("usb") ||
      qLower.includes("hdmi") ||
      qLower.includes("port") ||
      qLower.includes("ram upgradable") ||
      qLower.includes("expandable") ||
      qLower.includes("displayport") ||
      qLower.includes("thunderbolt") ||
      qLower.includes("windows 11") ||
      qLower.includes("weight") ||
      qLower.includes("nit") ||
      qLower.includes("bios") ||
      qLower.includes("firmware") ||
      qLower.includes("review") ||
      qLower.includes("rating") ||
      qLower.includes("complaint") ||
      qLower.includes("sentiment") ||
      qLower.includes("what do people say") ||
      qLower.includes("feedback") ||
      qLower.includes("battery life");

    const targetProductForQA = aiDrawerContext.product || activeProductsInView[0];

    if (isProductSpecQuestion && targetProductForQA) {
      try {
        const res = await askProductQuestion({
          product_id: targetProductForQA.id,
          product_title: targetProductForQA.title,
          question: query,
          catalog_specs: (targetProductForQA.catalogSpecs ||
            targetProductForQA.specsGrouped?.connectivity ||
            targetProductForQA.specsGrouped?.performance ||
            {}) as Record<string, unknown>,
          reviews_summary: {
            average_rating: targetProductForQA.rating,
            rating_number: targetProductForQA.reviewCount,
            summary: targetProductForQA.sentiment?.customerLikes?.[0] || "Verified customer satisfaction",
          },
          offer_data: {
            unit_price_minor: targetProductForQA.priceMinor,
            currency: targetProductForQA.currency,
            available_stock: targetProductForQA.stock,
            delivery_days: targetProductForQA.deliveryDays,
            return_period_days: targetProductForQA.returnDays,
          },
        });

        if (res.ok) {
          const data = res.data;
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: "agent",
              text: data.answer || `Verified specification for ${targetProductForQA.title}.`,
              researchResult: {
                source_label: data.source_label || "Catalog & Technical Analysis",
                source_url: data.source_url,
                confidence_level: data.confidence_level || "HIGH",
                reason_for_web_search: data.reason_for_web_search,
                transparency_steps: data.transparency_steps || [],
              },
              timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            },
          ]);
          setLoading(false);
          return;
        }
      } catch (err) {
        console.warn("Product Q&A research call note:", err);
      }
    }

    // 3. SERVER EXPLORE / NATURAL LANGUAGE CATALOG SEARCH: POST /api/explore
    try {
      const res = await exploreCatalog({
        prompt: query,
        category: currentIntent.category,
        max_price_minor: currentIntent.budget_max,
      });

      if (!res.ok) {
        const isGuard =
          res.error.code === "PROMPT_INJECTION_SUSPECTED" || res.error.code?.includes("GUARD");
        const msgText = isGuard
          ? "I'm focused exclusively on shopping, product specifications, and checkout assistance. I can help you find, compare, or configure products in our catalog."
          : `I encountered an issue searching the catalog: ${res.error.message}`;

        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text: msgText,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
        setLoading(false);
        return;
      }

      const data = res.data;

      if (data.guard_blocked) {
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text:
              data.message ||
              "I'm focused exclusively on shopping, product specifications, and checkout assistance.",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
        setLoading(false);
        return;
      }

      const matchedItems: ProductItem[] = Array.isArray(data.products)
        ? data.products.map((p) => exploreOfferToProductItem(p, data.catalog_source ?? null))
        : [];

      setActiveProductsInView(matchedItems);

      // Update structured intent from server
      if (data.intent) {
        updateIntent({
          queryText: data.intent.query || query,
          category: data.intent.category || null,
          budget_max: data.intent.budget_minor || null,
          min_memory_gb: data.intent.min_memory_gb || null,
          delivery_max_days: data.intent.max_delivery_days || null,
        });
      }

      if (matchedItems.length === 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text: `No exact matches found for "${query}" in our verified catalog.\n\nWould you like to expand your budget ceiling or try different filters?`,
            relaxationOptions: [
              { label: "Set Budget to ₹70,000", budget: 7000000 },
              { label: "Set Budget to ₹1,50,000", budget: 15000000 },
            ],
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      } else {
        const top = matchedItems[0];
        const researchEvidence = data.research?.evidence || [];
        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text:
              `Found **${matchedItems.length} verified offer${matchedItems.length > 1 ? "s" : ""}** satisfying your request:\n` +
              `• **Top Match:** ${top.title} (${formatMinorToMajor(top.priceMinor, top.currency)})\n` +
              (data.intent?.category ? `• **Category:** ${data.intent.category}\n` : "") +
              (data.intent?.budget_minor
                ? `• **Budget Ceiling:** ≤ ${formatMinorToMajor(data.intent.budget_minor, data.intent.currency || "INR")}\n`
                : "") +
              `• **Delivery:** Guaranteed within ${top.deliveryDays} days`,
            matchedProducts: matchedItems.slice(0, 3),
            highlightedProduct: top,
            structuredIntent: data.intent || undefined,
            researchResult:
              researchEvidence.length > 0
                ? {
                    source_label:
                      data.catalog_source === "postgresql"
                        ? "Live Merchant Database"
                        : "Verified Catalog Source",
                    source_url: researchEvidence[0]?.source_url,
                    confidence_level: "HIGH",
                    reason_for_web_search: null,
                    transparency_steps: researchEvidence.map((e) => `✦ ${e.claim}`),
                  }
                : undefined,
            actionSuggestion: {
              label: `View All ${matchedItems.length} Results in Storefront →`,
              action: () => {
                closeAiDrawer();
                router.push(`/search?q=${encodeURIComponent(query)}`);
              },
            },
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      }
    } catch (err) {
      console.warn("Explore API call error:", err);
      setMessages((prev) => [
        ...prev,
        {
          id: `agt_${Date.now()}`,
          sender: "agent",
          text: `An error occurred while querying the live catalog for "${query}". Please check your network connection or try again.`,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  if (!isAiDrawerOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        onClick={closeAiDrawer}
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
      />

      {/* Slide-out Panel */}
      <div className="absolute inset-y-0 right-0 max-w-full flex pl-10">
        <div className="w-screen max-w-md bg-white shadow-2xl flex flex-col justify-between border-l border-slate-200">
          {/* Drawer Header */}
          <div className="p-4 border-b border-slate-200 bg-slate-50/90 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="p-1.5 bg-indigo-600 text-white rounded-xl text-xs font-bold font-mono">✦</span>
              <div>
                <h3 className="font-black text-slate-900 text-sm">AgentPay Shopping Assistant</h3>
                <p className="text-[11px] text-slate-500">Context-Aware AI &amp; Hardware Spec Intelligence</p>
              </div>
            </div>
            <button
              onClick={closeAiDrawer}
              className="p-1.5 hover:bg-slate-200 rounded-xl text-slate-400 hover:text-slate-700 font-black text-sm transition-all"
            >
              ✕
            </button>
          </div>

          {/* Active Intent Context Bar */}
          {(currentIntent.category || currentIntent.budget_max || (currentIntent.brands && currentIntent.brands.length > 0)) && (
            <div className="px-4 py-2 bg-indigo-50/70 border-b border-indigo-100 flex flex-wrap items-center gap-1.5 text-[10px]">
              <span className="font-bold text-indigo-900 uppercase">Active Intent:</span>
              {currentIntent.category && (
                <span className="px-2 py-0.5 bg-white border border-indigo-200 text-indigo-700 rounded-md font-medium">
                  {currentIntent.category}
                </span>
              )}
              {currentIntent.budget_max && (
                <span className="px-2 py-0.5 bg-white border border-indigo-200 text-indigo-700 rounded-md font-medium">
                  ≤ {formatMinorToMajor(currentIntent.budget_max, "INR")}
                </span>
              )}
              {currentIntent.brands?.map((b) => (
                <span key={b} className="px-2 py-0.5 bg-white border border-indigo-200 text-indigo-700 rounded-md font-medium">
                  Brand: {b}
                </span>
              ))}
            </div>
          )}

          {/* Messages Container */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex flex-col ${msg.sender === "user" ? "items-end" : "items-start"}`}
              >
                <div
                  className={`max-w-[88%] p-3.5 rounded-2xl space-y-2.5 ${
                    msg.sender === "user"
                      ? "bg-indigo-600 text-white rounded-br-xs shadow-xs"
                      : "bg-slate-100/90 text-slate-800 rounded-bl-xs border border-slate-200/60"
                  }`}
                >
                  <p className="whitespace-pre-line leading-relaxed font-medium">{msg.text}</p>

                  {/* Matched Products Card List */}
                  {msg.matchedProducts && msg.matchedProducts.length > 0 && (
                    <div className="space-y-2 pt-1">
                      {msg.matchedProducts.map((p) => (
                        <div
                          key={p.id}
                          onClick={() => {
                            closeAiDrawer();
                            router.push(`/product/${p.id}`);
                          }}
                          className="bg-white p-2.5 rounded-xl border border-slate-200 shadow-2xs hover:border-indigo-500 cursor-pointer transition-all flex items-center gap-3"
                        >
                          <img
                            src={p.imageUrl}
                            alt={p.title}
                            className="w-12 h-12 rounded-lg object-cover"
                          />
                          <div className="flex-1 min-w-0">
                            <h5 className="font-bold text-slate-900 truncate text-[11px]">{p.title}</h5>
                            <span className="font-black text-indigo-600 text-xs">
                              {formatMinorToMajor(p.priceMinor, p.currency)}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Highlighted Best Match Badge */}
                  {msg.highlightedProduct && (
                    <div className="bg-indigo-50 p-3 rounded-xl border border-indigo-200 space-y-1.5">
                      <div className="font-bold text-indigo-950 text-[11px] flex items-center gap-1">
                        <span>⭐ Top Recommendation:</span>
                      </div>
                      <div className="font-semibold text-slate-900 text-xs">{msg.highlightedProduct.title}</div>
                      <div className="font-black text-indigo-600 text-sm">
                        {formatMinorToMajor(msg.highlightedProduct.priceMinor, msg.highlightedProduct.currency)}
                      </div>
                    </div>
                  )}

                  {/* Trade-off Resolution Options */}
                  {msg.tradeoffOptions && (
                    <div className="space-y-1.5 pt-1">
                      {msg.tradeoffOptions.map((opt, i) => (
                        <button
                          key={i}
                          onClick={opt.action}
                          className="w-full text-left p-2 bg-white hover:bg-indigo-50 text-indigo-950 border border-slate-200 hover:border-indigo-300 rounded-xl text-[11px] font-bold transition-all"
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Constraint Relaxation Buttons */}
                  {msg.relaxationOptions && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {msg.relaxationOptions.map((opt, i) => (
                        <button
                          key={i}
                          onClick={() => {
                            updateIntent({ budget_max: opt.budget });
                            handleUserSubmit(`Set budget ceiling to ${opt.budget / 100} INR`);
                          }}
                          className="px-3 py-1.5 bg-indigo-600 text-white rounded-xl text-[11px] font-bold shadow-xs hover:bg-indigo-700"
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Research Transparency & Verified Source Citation */}
                  {msg.researchResult && (
                    <div className="mt-2 space-y-2 pt-2 border-t border-slate-200/80">
                      {/* Progression steps */}
                      {msg.researchResult.transparency_steps && msg.researchResult.transparency_steps.length > 0 && (
                        <div className="bg-slate-50 p-2.5 rounded-lg border border-slate-200 space-y-1 font-mono text-[10px] text-slate-600">
                          <div className="font-bold text-slate-800 uppercase tracking-wider text-[9px] mb-1">Research Audit Trail:</div>
                          {msg.researchResult.transparency_steps.map((step, idx) => (
                            <div key={idx} className="flex items-center gap-1.5">
                              <span>{step}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Verified Source Citation Badge */}
                      <div className="flex flex-wrap items-center gap-2 pt-1">
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
                          🛡️ Source: {msg.researchResult.source_label}
                        </span>
                        {msg.researchResult.source_url && (
                          <a
                            href={msg.researchResult.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-[10px] font-semibold text-indigo-600 hover:underline flex items-center gap-0.5"
                          >
                            View official document ↗
                          </a>
                        )}
                      </div>

                      {/* Transparency Explanation Accordion */}
                      {msg.researchResult.reason_for_web_search && (
                        <details className="text-[10px] text-slate-500 bg-white p-2 rounded-lg border border-slate-200 cursor-pointer">
                          <summary className="font-semibold text-slate-700 hover:text-indigo-600">
                            Why did you search the web?
                          </summary>
                          <p className="mt-1 text-slate-600 leading-relaxed">
                            {msg.researchResult.reason_for_web_search}
                          </p>
                        </details>
                      )}
                    </div>
                  )}

                  {/* Action Suggestion CTA */}
                  {msg.actionSuggestion && (
                    <button
                      type="button"
                      onClick={msg.actionSuggestion.action}
                      className="w-full py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-[11px] rounded-xl transition-all shadow-xs"
                    >
                      {msg.actionSuggestion.label}
                    </button>
                  )}
                </div>
                <span className="text-[10px] text-slate-400 mt-1 px-1">{msg.timestamp}</span>
              </div>
            ))}

            {loading && (
              <div className="flex items-center gap-2 text-slate-500 text-xs font-medium p-3 bg-slate-50 rounded-2xl w-fit">
                <span className="w-3.5 h-3.5 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin" />
                <span>Checking specifications &amp; review evidence...</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* Quick Context Action Prompts */}
          <div className="p-2.5 bg-slate-50 border-t border-slate-200 flex flex-wrap gap-1.5">
            <button
              onClick={() => handleUserSubmit("Which one is best?")}
              className="text-[10px] px-2.5 py-1 bg-white hover:bg-indigo-50 hover:text-indigo-700 border border-slate-200 rounded-lg text-slate-600 font-semibold transition-all"
            >
              ⭐ Which is best?
            </button>
            <button
              onClick={() => handleUserSubmit("Show me the cheapest one")}
              className="text-[10px] px-2.5 py-1 bg-white hover:bg-indigo-50 hover:text-indigo-700 border border-slate-200 rounded-lg text-slate-600 font-semibold transition-all"
            >
              💰 Cheapest option
            </button>
            <button
              onClick={() => handleUserSubmit("Only Lenovo")}
              className="text-[10px] px-2.5 py-1 bg-white hover:bg-indigo-50 hover:text-indigo-700 border border-slate-200 rounded-lg text-slate-600 font-semibold transition-all"
            >
              🏷️ Only Lenovo
            </button>
            <button
              onClick={() => handleUserSubmit("Does this laptop have HDMI?")}
              className="text-[10px] px-2.5 py-1 bg-white hover:bg-indigo-50 hover:text-indigo-700 border border-slate-200 rounded-lg text-slate-600 font-semibold transition-all"
            >
              🔌 Check HDMI port
            </button>
            <button
              onClick={() => handleUserSubmit("Is the battery actually good?")}
              className="text-[10px] px-2.5 py-1 bg-white hover:bg-indigo-50 hover:text-indigo-700 border border-slate-200 rounded-lg text-slate-600 font-semibold transition-all"
            >
              🔋 Battery review insight
            </button>
          </div>

          {/* Input Box */}
          <div className="p-3.5 border-t border-slate-200 bg-white">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleUserSubmit();
              }}
              className="relative flex items-center"
            >
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about products, specs, reviews, or add to cart..."
                className="w-full pl-3.5 pr-20 py-2.5 text-xs bg-slate-50 border border-slate-200 focus:border-indigo-600 rounded-xl focus:outline-none"
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="absolute right-1 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold text-xs rounded-lg transition-all"
              >
                Send
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
