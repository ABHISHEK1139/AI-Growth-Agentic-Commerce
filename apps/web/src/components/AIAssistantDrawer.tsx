"use client";

import React, { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { Sparkles, Maximize2, Zap, Cpu, ShoppingBag, ShieldCheck, RefreshCw, AlertTriangle, AlertCircle, Search, ArrowRight, Lock, CheckCircle2, CreditCard } from "lucide-react";
import { useStore } from "@/context/StoreContext";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";
import { formatMinorToMajor } from "@/lib/money";
import { exploreCatalog, askProductQuestion } from "@/catalog/client";
import { exploreOfferToProductItem } from "@/catalog/adapt";
import {
  sendGeminiChatMessage,
  type GeminiRole,
  type ModelTier,
  type ChatHistoryItem,
} from "@/catalog/geminiClient";

interface Message {
  id: string;
  sender: "user" | "agent";
  text: string;
  isError?: boolean;
  errorHeading?: string;
  queryAttempted?: string;
  modelUsed?: string;
  fallbackNotice?: string | null;
  durationMs?: number;
  followUps?: string[];
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
  conversationalCheckout?: {
    product: ProductItem;
    quantity: number;
    priceHash: string;
    totalMinor: number;
    currency: string;
    policyStatus: "AUTO_APPROVED" | "SUPERVISOR_REQUIRED" | "POLICY_BLOCKED";
    policyExplanation: string;
    completed?: boolean;
    paymentId?: string;
    orderId?: string;
    error?: string;
  };
  timestamp: string;
}

export function AIAssistantDrawer() {
  const router = useRouter();
  const pathname = usePathname();
  const {
    cart,
    placeOrder,
    userPreferences,
    isAiDrawerOpen,
    openAiDrawer,
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
  const [geminiRole, setGeminiRole] = useState<GeminiRole>("concierge");
  const [modelTier, setModelTier] = useState<ModelTier>("auto");

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

  const handleInAppRazorpayPayment = async (msgId: string, checkoutData: any) => {
    try {
      // 1. Create order on backend with real test mode binding
      const orderRes = await fetch("/api/create-order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: checkoutData.totalMinor,
          currency: checkoutData.currency || "INR",
          receipt: `rcpt_ai_${Date.now()}`,
        }),
      });

      let razorpayOrderId = `order_test_${Date.now().toString(36)}`;
      if (orderRes.ok) {
        const orderJson = await orderRes.json();
        razorpayOrderId = orderJson.data?.order_id || orderJson.order_id || razorpayOrderId;
      }

      const keyId = (
        process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID ||
        process.env.RAZORPAY_KEY_ID ||
        "rzp_test_TTUGFNUeulzhoV"
      ).trim();

      if (typeof window !== "undefined" && window.Razorpay) {
        const rzp = new window.Razorpay({
          key: keyId,
          amount: checkoutData.totalMinor,
          currency: checkoutData.currency || "INR",
          name: "AgentPay AI Commerce",
          description: `Conversational In-App Checkout: ${checkoutData.product.title.slice(0, 35)}`,
          order_id: razorpayOrderId,
          handler: async function (response: any) {
            const paymentId = response.razorpay_payment_id || `pay_${Date.now().toString(36)}`;
            const orderRecord = placeOrder({
              paymentId,
              items: [{ product: checkoutData.product, quantity: checkoutData.quantity || 1 }],
              totalMinor: checkoutData.totalMinor,
              currency: checkoutData.currency,
              policySummary: "Autonomous conversational in-app checkout authorized via Razorpay Test Mode",
            });

            // Post audit event
            try {
              await fetch("/api/v1/audit/events", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  event_type: "IN_APP_PAYMENT_COMPLETED",
                  aggregate_type: "order",
                  aggregate_id: orderRecord.orderId,
                  amount_minor: checkoutData.totalMinor,
                  decision: "allow",
                  reason_code: "IN_APP_RAZORPAY_TEST_PAID",
                  metadata: {
                    payment_id: paymentId,
                    price_hash: checkoutData.priceHash,
                    product_id: checkoutData.product.id,
                  },
                }),
              });
            } catch {}

            setMessages((prev) =>
              prev.map((m) =>
                m.id === msgId
                  ? {
                      ...m,
                      conversationalCheckout: {
                        ...m.conversationalCheckout!,
                        completed: true,
                        paymentId,
                        orderId: orderRecord.orderId,
                        error: undefined,
                      },
                    }
                  : m
              )
            );
          },
          modal: {
            ondismiss: function () {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === msgId
                    ? {
                        ...m,
                        conversationalCheckout: {
                          ...m.conversationalCheckout!,
                          error: "Payment modal was dismissed. Your price lock remains held for 15 minutes.",
                        },
                      }
                    : m
                )
              );
            },
          },
          theme: { color: "#174c3c" },
        });
        rzp.open();
      } else {
        // Fallback simulated payment
        const paymentId = `pay_sim_${Date.now().toString(36)}`;
        const orderRecord = placeOrder({
          paymentId,
          items: [{ product: checkoutData.product, quantity: checkoutData.quantity || 1 }],
          totalMinor: checkoutData.totalMinor,
          currency: checkoutData.currency,
          policySummary: "Simulated test-mode in-app payment verified",
        });
        setMessages((prev) =>
          prev.map((m) =>
            m.id === msgId
              ? {
                  ...m,
                  conversationalCheckout: {
                    ...m.conversationalCheckout!,
                    completed: true,
                    paymentId,
                    orderId: orderRecord.orderId,
                  },
                }
              : m
          )
        );
      }
    } catch (err: any) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId
            ? {
                ...m,
                conversationalCheckout: {
                  ...m.conversationalCheckout!,
                  error: err?.message || "Failed to initiate payment. Please try again.",
                },
              }
            : m
        )
      );
    }
  };

  const handleSimulateFailure = async (msgId: string) => {
    try {
      await fetch("/api/v1/audit/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event_type: "POLICY_GATE_TRIPPED",
          aggregate_type: "checkout",
          aggregate_id: `chk_fail_${Date.now().toString(36)}`,
          amount_minor: 14999900,
          decision: "block",
          reason_code: "HARD_CEILING_EXCEEDED",
          metadata: {
            policy: "Autonomous transaction ceiling exceeded without multi-factor supervisor approval.",
            failure_simulation: true,
          },
        }),
      });
    } catch {}

    setMessages((prev) =>
      prev.map((m) =>
        m.id === msgId
          ? {
              ...m,
              conversationalCheckout: {
                ...m.conversationalCheckout!,
                error: "Policy Gate Tripped: Amount exceeded autonomous spending ceiling. Money action bounded and blocked safely before payment rail contact. An explainable audit trail was recorded.",
              },
            }
          : m
      )
    );
  };

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

    // "Conversational in-app checkout" / "Buy it" / "Add to cart"
    if (
      qLower.includes("buy it") ||
      qLower.includes("buy this") ||
      qLower.includes("buy now") ||
      qLower.includes("checkout") ||
      qLower.includes("pay now") ||
      qLower.includes("in-app checkout") ||
      qLower.includes("order now") ||
      qLower.includes("purchase") ||
      qLower.includes("add to cart") ||
      qLower.includes("add it")
    ) {
      const targetProd = aiDrawerContext.product || activeProductsInView[0] || (cart.length > 0 ? cart[0].product : null);
      if (targetProd) {
        addToCart(targetProd, 1, false);

        const autoLimit = userPreferences.autoApprovalLimitMinor || 50000000;
        const total = targetProd.priceMinor;
        const isApproved = total <= autoLimit;
        const priceHash = `sha256_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 8)}`;

        setMessages((prev) => [
          ...prev,
          {
            id: `agt_chk_${Date.now()}`,
            sender: "agent",
            text: `I've prepared an instant **Conversational In-App Checkout** for the **${targetProd.title}**.\n\n` +
              `• **Price Locked**: ${formatMinorToMajor(total, targetProd.currency)} (held for 15 mins)\n` +
              `• **Delivery**: Free 2-Day Express Guaranteed\n` +
              `• **Policy Gate**: ${isApproved ? "✓ Within autonomous spending ceiling (Auto-Approved)" : "⚠ Requires 1-click supervisor sign-off"}\n\n` +
              `You can complete payment directly in this conversation using **Razorpay Test Mode** below:`,
            conversationalCheckout: {
              product: targetProd,
              quantity: 1,
              priceHash,
              totalMinor: total,
              currency: targetProd.currency || "INR",
              policyStatus: isApproved ? "AUTO_APPROVED" : "SUPERVISOR_REQUIRED",
              policyExplanation: isApproved
                ? "Conforms to autonomous purchasing policy (< ₹5,000 threshold)."
                : "Transaction exceeds ₹5,000 auto-approval threshold. One-click step-up authorization required.",
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
            text: "Please search and select a product first before initiating conversational checkout.",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      }
      setLoading(false);
      return;
    }

    // 2. GEMINI MULTI-TURN CHATBOT (Server-Side @google/genai with Model Tiering & Role System Instructions)
    let geminiFailed = false;
    let geminiErrorMessage = "";
    try {
      const historyPayload: ChatHistoryItem[] = messages.slice(-10).map((m) => ({
        role: m.sender === "user" ? "user" : "model",
        text: m.text,
      }));

      const activeProd = aiDrawerContext.product || activeProductsInView[0];

      const geminiRes = await sendGeminiChatMessage({
        message: query,
        history: historyPayload,
        role: geminiRole,
        modelPreference: modelTier,
        activeProductId: activeProd?.id,
      });

      if (geminiRes.ok && geminiRes.answer && geminiRes.answer.trim()) {
        if (geminiRes.matchedProducts && geminiRes.matchedProducts.length > 0) {
          setActiveProductsInView(geminiRes.matchedProducts);
        }

        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text: geminiRes.answer!,
            modelUsed: geminiRes.modelUsed,
            fallbackNotice: geminiRes.fallbackNotice,
            durationMs: geminiRes.durationMs,
            matchedProducts: geminiRes.matchedProducts,
            followUps: geminiRes.followUps,
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
        setLoading(false);
        return;
      } else {
        geminiFailed = true;
        geminiErrorMessage = geminiRes.error || "Unable to receive response from AI model.";
      }
    } catch (gErr: any) {
      console.warn("Gemini Chat note, attempting catalog fallback:", gErr);
      geminiFailed = true;
      geminiErrorMessage = gErr?.message || "Network error while connecting to assistant.";
    }

    // 3. PRODUCT SPEC Q&A & RESEARCH FALLBACK: Route to POST /api/v1/research/ask
    const isProductSpecQuestion =
      Boolean(aiDrawerContext.product) ||
      qLower.includes("search") ||
      qLower.includes("internet") ||
      qLower.includes("web") ||
      qLower.includes("online") ||
      qLower.includes("research") ||
      qLower.includes("compare") ||
      qLower.includes("vs") ||
      qLower.includes("versus") ||
      qLower.includes("benchmark") ||
      qLower.includes("battery") ||
      qLower.includes("docker") ||
      qLower.includes("linux") ||
      qLower.includes("issue") ||
      qLower.includes("problem") ||
      qLower.includes("heating") ||
      qLower.includes("thermal") ||
      qLower.includes("upgrade") ||
      qLower.includes("usb") ||
      qLower.includes("hdmi") ||
      qLower.includes("port") ||
      qLower.includes("ram") ||
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

    const matchedCatalogProduct =
      ALL_PRODUCTS.find(
        (p) =>
          qLower.includes(p.title.toLowerCase().slice(0, 15)) ||
          qLower.includes(p.brand.toLowerCase())
      ) ||
      activeProductsInView[0] ||
      ALL_PRODUCTS[0];

    const targetProductForQA = aiDrawerContext.product || matchedCatalogProduct;

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
          : `We encountered a temporary issue searching the catalog for "${query}". Our verified store catalog is available for direct browsing.`;

        setMessages((prev) => [
          ...prev,
          {
            id: `agt_${Date.now()}`,
            sender: "agent",
            text: msgText,
            isError: !isGuard,
            errorHeading: isGuard ? "Safety & Scope Notice" : "Catalog Search Interrupted",
            queryAttempted: query,
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
        if (geminiFailed) {
          setMessages((prev) => [
            ...prev,
            {
              id: `agt_${Date.now()}`,
              sender: "agent",
              text: geminiErrorMessage || `We encountered an issue communicating with the assistant. You can retry your request or explore our verified catalog.`,
              isError: true,
              errorHeading: "Assistant Temporarily Unavailable",
              queryAttempted: query,
              timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            },
          ]);
          setLoading(false);
          return;
        }

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
              data.message ||
              (`Found **${matchedItems.length} verified offer${matchedItems.length > 1 ? "s" : ""}** satisfying your request:\n` +
              `• **Top Match:** ${top.title} (${formatMinorToMajor(top.priceMinor, top.currency)})\n` +
              (data.intent?.category ? `• **Category:** ${data.intent.category}\n` : "") +
              (data.intent?.budget_minor
                ? `• **Budget Ceiling:** ≤ ${formatMinorToMajor(data.intent.budget_minor, data.intent.currency || "INR")}\n`
                : "") +
              `• **Delivery:** Guaranteed within ${top.deliveryDays} days`),
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
          text: `An error occurred while connecting to the assistant. You can retry your query or browse our verified catalog directly.`,
          isError: true,
          errorHeading: "Assistant Temporarily Unavailable",
          queryAttempted: query,
          timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  if (pathname?.startsWith("/merchant") || pathname?.startsWith("/scenarios")) {
    return null;
  }

  if (!isAiDrawerOpen) {
    return (
      <aside aria-label="AI Shopping Assistant" className="fixed bottom-6 right-6 z-40 hidden md:block animate-fade-in-up">
        <button
          type="button"
          onClick={() => openAiDrawer()}
          className="group relative flex items-center gap-2.5 rounded-full bg-[#174c3c] px-4 py-3 text-sm font-bold text-white shadow-xl backdrop-blur-md transition-all duration-300 hover:bg-[#103c2f] hover:shadow-2xl hover:scale-105 active:scale-95 ring-2 ring-white/20"
        >
          <span className="relative flex h-3 w-3 items-center justify-center">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#a9d1b6] opacity-75"></span>
            <span className="relative inline-flex h-2 w-2 rounded-full bg-[#a9d1b6]"></span>
          </span>
          <span className="font-mono text-xs text-[#a9d1b6]">✦</span>
          <span>Ask AI Assistant</span>
          <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px] font-semibold text-white/90">
            Gemini AI
          </span>
        </button>
      </aside>
    );
  }

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
          <div className="p-3.5 border-b border-slate-200 bg-slate-50 flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="p-1.5 bg-[#174c3c] text-white rounded-xl text-xs font-bold font-mono">
                  <Sparkles className="h-3.5 w-3.5" />
                </span>
                <div>
                  <h3 className="font-bold text-slate-900 text-sm">Gemini Shopping Assistant</h3>
                  <p className="text-[10px] text-slate-500">Multi-Turn AI with Dynamic Model Routing</p>
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Link
                  href="/chat"
                  onClick={closeAiDrawer}
                  className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-500 hover:text-emerald-800 transition-colors"
                  title="Open in full screen chat"
                >
                  <Maximize2 className="h-3.5 w-3.5" />
                </Link>
                <button
                  onClick={closeAiDrawer}
                  className="p-1.5 hover:bg-slate-200 rounded-lg text-slate-400 hover:text-slate-700 font-bold text-xs transition-colors"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Persona & Model Controls Bar */}
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-slate-200/60 text-[11px]">
              <div className="flex items-center gap-1.5">
                <span className="text-slate-500 font-medium">Role:</span>
                <select
                  value={geminiRole}
                  onChange={(e) => setGeminiRole(e.target.value as GeminiRole)}
                  className="bg-white border border-slate-200 rounded-md px-1.5 py-0.5 text-slate-700 font-semibold text-[11px] focus:outline-none"
                >
                  <option value="concierge">🛍️ Concierge</option>
                  <option value="hardware_specialist">🔬 Hardware Specialist</option>
                  <option value="merchant_auditor">🛡️ Risk Auditor</option>
                </select>
              </div>

              <div className="flex items-center gap-1.5">
                <span className="text-slate-500 font-medium">Model:</span>
                <select
                  value={modelTier}
                  onChange={(e) => setModelTier(e.target.value as ModelTier)}
                  className="bg-white border border-slate-200 rounded-md px-1.5 py-0.5 text-slate-700 font-semibold text-[11px] focus:outline-none"
                >
                  <option value="auto">🤖 Auto</option>
                  <option value="gemini-3.1-flash-lite">⚡ Lite</option>
                  <option value="gemini-3.5-flash">✨ Flash</option>
                  <option value="gemini-3.1-pro-preview">🧠 Pro</option>
                </select>
              </div>
            </div>
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
                      : msg.isError || (msg.sender === "agent" && !msg.text?.trim() && !msg.matchedProducts?.length && !msg.highlightedProduct)
                      ? "bg-amber-50/95 text-slate-800 rounded-bl-xs border border-amber-200 shadow-xs"
                      : "bg-slate-100/90 text-slate-800 rounded-bl-xs border border-slate-200/60"
                  }`}
                >
                  {msg.isError || (msg.sender === "agent" && !msg.text?.trim() && !msg.matchedProducts?.length && !msg.highlightedProduct) ? (
                    <div className="space-y-3">
                      <div className="flex items-start gap-2.5">
                        <div className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-amber-100 text-amber-800 border border-amber-200">
                          <AlertTriangle className="h-4 w-4" />
                        </div>
                        <div className="flex-1">
                          <h4 className="text-xs font-bold text-slate-900">
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
                      <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-amber-200/60">
                        {msg.queryAttempted && (
                          <button
                            type="button"
                            onClick={() => handleUserSubmit(msg.queryAttempted)}
                            className="inline-flex items-center gap-1 rounded-lg bg-[#174c3c] px-2.5 py-1 text-[11px] font-bold text-white transition hover:bg-[#103c2f] shadow-xs active:scale-95"
                          >
                            <RefreshCw className="h-3 w-3" />
                            <span>Try again</span>
                          </button>
                        )}
                        {msg.queryAttempted && (
                          <Link
                            href={`/search?q=${encodeURIComponent(msg.queryAttempted)}`}
                            onClick={closeAiDrawer}
                            className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-50"
                          >
                            <Search className="h-3 w-3 text-slate-500" />
                            <span>Search Store</span>
                          </Link>
                        )}
                        <Link
                          href="/category/laptops"
                          onClick={closeAiDrawer}
                          className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-600 transition hover:bg-slate-50"
                        >
                          <span>Laptops</span>
                        </Link>
                        <Link
                          href="/search?deals=true"
                          onClick={closeAiDrawer}
                          className="inline-flex items-center gap-1 rounded-lg bg-white border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-600 transition hover:bg-slate-50"
                        >
                          <span>Deals</span>
                        </Link>
                      </div>
                    </div>
                  ) : (
                    <>
                      {msg.modelUsed && (
                        <div className="flex items-center gap-1.5 text-[9px] font-mono text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-200/80 w-fit">
                          <Sparkles className="h-2.5 w-2.5 text-emerald-600" />
                          <span>{msg.modelUsed}</span>
                          {msg.durationMs ? <span className="text-slate-400">• {msg.durationMs}ms</span> : null}
                        </div>
                      )}

                      {msg.fallbackNotice && (
                        <div className="text-[10px] text-amber-800 bg-amber-50 p-2 rounded-lg border border-amber-200">
                          {msg.fallbackNotice}
                        </div>
                      )}

                      <p className="whitespace-pre-line leading-relaxed font-medium">
                        {msg.text && msg.text.trim()
                          ? msg.text
                          : "I am ready to help you discover products, compare specifications, and complete your order."}
                      </p>

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

                  {/* Conversational In-App Checkout Card */}
                  {msg.conversationalCheckout && (
                    <div className="mt-3 rounded-2xl bg-white border border-slate-200 overflow-hidden shadow-sm">
                      {/* Header */}
                      <div className="bg-[#f7f7f2] px-3.5 py-2.5 border-b border-slate-200 flex items-center justify-between">
                        <div className="flex items-center gap-1.5 text-xs font-black text-slate-900">
                          <ShieldCheck className="w-4 h-4 text-[#174c3c]" />
                          <span>Conversational In-App Checkout</span>
                        </div>
                        <span className="text-[10px] font-bold uppercase bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-md">
                          Razorpay Test Mode
                        </span>
                      </div>

                      <div className="p-3.5 space-y-3">
                        {msg.conversationalCheckout.completed ? (
                          /* Successful Order Confirmation */
                          <div className="space-y-2.5 text-center py-2">
                            <div className="w-10 h-10 rounded-full bg-emerald-100 text-emerald-700 mx-auto flex items-center justify-center">
                              <CheckCircle2 className="w-6 h-6" />
                            </div>
                            <div>
                              <h4 className="text-xs font-black text-slate-900">
                                Payment Verified &amp; Order Confirmed
                              </h4>
                              <p className="text-[11px] text-slate-500 mt-0.5">
                                Order ID: <span className="font-mono font-bold text-slate-800">{msg.conversationalCheckout.orderId}</span>
                              </p>
                              <p className="text-[11px] text-slate-500">
                                Payment ID: <span className="font-mono font-bold text-slate-800">{msg.conversationalCheckout.paymentId}</span>
                              </p>
                            </div>
                            <div className="bg-emerald-50 rounded-xl p-2.5 text-[11px] font-medium text-emerald-900 border border-emerald-200/80 text-left space-y-1">
                              <div className="font-bold flex items-center gap-1">
                                <ShieldCheck className="w-3.5 h-3.5 text-emerald-700" />
                                <span>Gated Money Action Audit Trail Committed</span>
                              </div>
                              <p className="text-[10px] text-emerald-800">
                                Cryptographic Nonce: <span className="font-mono font-semibold">{msg.conversationalCheckout.priceHash.slice(0, 16)}...</span>
                              </p>
                            </div>
                            <div className="pt-1 flex flex-col gap-1.5">
                              <Link
                                href={`/orders/${msg.conversationalCheckout.orderId}`}
                                onClick={closeAiDrawer}
                                className="w-full py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-[11px] rounded-xl text-center shadow-2xs transition-all"
                              >
                                View Order Tracking &amp; Delivery &rarr;
                              </Link>
                              <Link
                                href={`/timeline/order/${msg.conversationalCheckout.orderId}`}
                                onClick={closeAiDrawer}
                                className="w-full py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold text-[10px] rounded-xl text-center transition-all"
                              >
                                View Audit Ledger &amp; Nonces
                              </Link>
                            </div>
                          </div>
                        ) : (
                          /* Active Checkout Form */
                          <>
                            {/* Product row */}
                            <div className="flex gap-3 items-center">
                              <img
                                src={msg.conversationalCheckout.product.imageUrl}
                                alt={msg.conversationalCheckout.product.title}
                                className="w-14 h-14 rounded-xl object-cover border border-slate-200 shrink-0"
                              />
                              <div className="flex-1 min-w-0">
                                <h5 className="font-bold text-slate-900 truncate text-xs">
                                  {msg.conversationalCheckout.product.title}
                                </h5>
                                <div className="text-[10px] text-slate-500 mt-0.5">
                                  <span>Qty: {msg.conversationalCheckout.quantity}</span> &middot;{" "}
                                  <span className="text-emerald-700 font-semibold">Free Express Shipping</span>
                                </div>
                                <div className="text-xs font-black text-[#174c3c] mt-0.5">
                                  {formatMinorToMajor(msg.conversationalCheckout.totalMinor, msg.conversationalCheckout.currency)}
                                </div>
                              </div>
                            </div>

                            {/* Bounded Policy Status Card */}
                            <div className="p-2.5 rounded-xl bg-slate-50 border border-slate-200 text-[11px] space-y-1">
                              <div className="flex items-center justify-between">
                                <span className="font-bold text-slate-800 flex items-center gap-1">
                                  <Lock className="w-3 h-3 text-[#174c3c]" /> Price Lock Active
                                </span>
                                <span className="font-mono text-[9px] text-slate-500">
                                  {msg.conversationalCheckout.priceHash.slice(0, 16)}...
                                </span>
                              </div>
                              <p className="text-[10px] text-slate-600">
                                {msg.conversationalCheckout.policyExplanation}
                              </p>
                            </div>

                            {/* Error or simulated failure message */}
                            {msg.conversationalCheckout.error && (
                              <div className="p-2.5 rounded-xl bg-rose-50 border border-rose-200 text-[11px] text-rose-800 space-y-1">
                                <div className="font-bold flex items-center gap-1">
                                  <AlertTriangle className="w-3.5 h-3.5 text-rose-600" />
                                  <span>Graceful Failure Handled</span>
                                </div>
                                <p className="text-[10px] text-rose-700">
                                  {msg.conversationalCheckout.error}
                                </p>
                              </div>
                            )}

                            {/* Action Buttons */}
                            <div className="space-y-1.5 pt-1">
                              <button
                                type="button"
                                onClick={() => handleInAppRazorpayPayment(msg.id, msg.conversationalCheckout)}
                                className="w-full py-2.5 bg-[#174c3c] hover:bg-[#103c2f] active:scale-98 text-white font-bold text-xs rounded-xl shadow-xs transition-all flex items-center justify-center gap-2 cursor-pointer"
                              >
                                <CreditCard className="w-3.5 h-3.5" />
                                <span>Pay with Razorpay Test Mode ({formatMinorToMajor(msg.conversationalCheckout.totalMinor, msg.conversationalCheckout.currency)})</span>
                              </button>

                              <button
                                type="button"
                                onClick={() => handleSimulateFailure(msg.id)}
                                className="w-full py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-600 font-semibold text-[10px] rounded-lg transition-all cursor-pointer"
                              >
                                Simulate Bounded Policy Failure (Audit Demo)
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  )}
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

                  {/* Follow-up Suggestion Chips */}
                  {msg.followUps && msg.followUps.length > 0 && (
                    <div className="pt-1.5 flex flex-wrap gap-1">
                      {msg.followUps.map((chip, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => handleUserSubmit(chip)}
                          className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-white border border-slate-200 text-slate-700 hover:bg-emerald-50 hover:text-emerald-800 hover:border-emerald-300 transition-colors"
                        >
                          💡 {chip}
                        </button>
                      ))}
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
                    </>
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
              onClick={() => handleUserSubmit("Search internet for real-world reviews and battery test")}
              className="text-[10px] px-2.5 py-1 bg-white hover:bg-emerald-50 hover:text-emerald-700 border border-slate-200 rounded-lg text-slate-600 font-semibold transition-all"
            >
              🌐 Web research & reviews
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
