"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { resolveApiUrl } from "@/lib/api";

type DeploymentMode = "standalone" | "existing_store" | "marketplace";
type PlatformTab = "shopify" | "woocommerce" | "generic_rest" | "feed";

export default function MerchantIntegrationsPage() {
  // The gateway origin is configuration, never a literal. Resolved after mount so
  // the server-rendered markup and the client agree.
  const [apiOrigin, setApiOrigin] = useState("");
  useEffect(() => {
    const configured = (process.env.NEXT_PUBLIC_API_BASE_URL || "").trim();
    if (configured) {
      try {
        setApiOrigin(new URL(configured).origin);
        return;
      } catch {
        // Not an absolute URL; fall back to this app's own origin.
      }
    }
    setApiOrigin(window.location.origin);
  }, []);

  const [activeMode, setActiveMode] = useState<DeploymentMode>("existing_store");
  const [activePlatform, setActivePlatform] = useState<PlatformTab>("shopify");
  const [storeDomain, setStoreDomain] = useState("mystore.myshopify.com");
  const [apiKey, setApiKey] = useState("shpat_live_98a72b3c4d5e");
  const [syncStatus, setSyncStatus] = useState<{
    running: boolean;
    result: string | null;
    productsCount: number;
    offersCount: number;
    durationMs: number;
  }>({
    running: false,
    result: null,
    productsCount: 0,
    offersCount: 0,
    durationMs: 0,
  });

  const handleTestSync = async () => {
    setSyncStatus({ running: true, result: null, productsCount: 0, offersCount: 0, durationMs: 0 });
    try {
      const res = await fetch(resolveApiUrl("/api/v1/connectors/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          merchant_id: `mer_${activePlatform}_${Date.now().toString().slice(-4)}`,
          platform_type: activePlatform,
          store_url: storeDomain,
          api_key: apiKey,
        }),
      });
      const data = await res.json();
      if (res.ok && data.ok && data.sync_result) {
        setSyncStatus({
          running: false,
          result: data.sync_result.status === "failed" ? "error" : "success",
          productsCount: data.sync_result.products_imported ?? 0,
          offersCount: data.sync_result.offers_updated ?? 0,
          durationMs: data.sync_result.duration_ms ?? 0,
        });
      } else {
        setSyncStatus({
          running: false,
          result: "error",
          productsCount: data?.sync_result?.products_imported ?? 0,
          offersCount: data?.sync_result?.offers_updated ?? 0,
          durationMs: data?.sync_result?.duration_ms ?? 0,
        });
      }
    } catch (err) {
      console.warn("Connector sync test error:", err);
      setSyncStatus({
        running: false,
        result: "error",
        productsCount: 0,
        offersCount: 0,
        durationMs: 0,
      });
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-16">
      {/* Header Banner */}
      <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-slate-900 text-white p-6 sm:p-8 rounded-3xl border border-indigo-900/60 shadow-xl flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div className="space-y-2 max-w-3xl">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-indigo-500/20 text-indigo-300 rounded-full font-mono text-xs font-bold uppercase tracking-wider border border-indigo-400/30">
            <span>✦ Universal AI Commerce Adapter</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black tracking-tight">
            Connect Your E-Commerce Store to AI Buyers
          </h1>
          <p className="text-xs sm:text-sm text-slate-300 leading-relaxed">
            AgentPay acts as an intelligent adapter layer on top of your existing platform (Shopify, WooCommerce, Custom REST, or Feeds) to expose your catalog, real-time inventory, and policy-gated checkout to autonomous AI agents and Razorpay execution.
          </p>
        </div>

        <Link
          href="/agent/playground"
          className="px-5 py-3.5 bg-indigo-500 hover:bg-indigo-600 text-white font-bold text-xs rounded-xl shadow-lg transition-all flex items-center gap-2 self-start md:self-auto shrink-0"
        >
          <span>✦</span>
          <span>Open AI Playground &rarr;</span>
        </Link>
      </div>

      {/* Visual Adapter Architecture Flow */}
      <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-black text-slate-900 text-sm">Platform-Agnostic Adapter Architecture</h3>
          <span className="text-[11px] font-mono text-indigo-600 font-bold bg-indigo-50 px-2.5 py-1 rounded-full border border-indigo-100">
            Zero Platform Lock-in
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 text-xs items-center">
          {/* Box 1: Existing Store */}
          <div className="p-4 bg-slate-50 border border-slate-200 rounded-2xl space-y-1.5 text-center">
            <div className="font-black text-slate-900 text-xs">1. E-Commerce Platform</div>
            <div className="text-[10px] text-slate-500">Shopify / WooCommerce / Custom API</div>
            <div className="text-[10px] font-mono text-slate-700 bg-white p-1.5 rounded-lg border border-slate-200 mt-2">
              Products • Stock • Orders
            </div>
          </div>

          <div className="text-center font-bold text-indigo-500 text-lg hidden md:block">&rarr;</div>

          {/* Box 2: AgentPay AI Gateway */}
          <div className="p-4 bg-indigo-50/80 border-2 border-indigo-500/40 rounded-2xl space-y-1.5 text-center md:col-span-1 shadow-xs">
            <div className="font-black text-indigo-950 text-xs">2. AgentPay Gateway</div>
            <div className="text-[10px] text-indigo-700">Canonical Translation &amp; AI Layer</div>
            <div className="text-[10px] font-mono text-indigo-900 bg-white p-1.5 rounded-lg border border-indigo-200 mt-2">
              Research • Policy • Auth Gate
            </div>
          </div>

          <div className="text-center font-bold text-indigo-500 text-lg hidden md:block">&rarr;</div>

          {/* Box 3: AI Buyers & Payment */}
          <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-2xl space-y-1.5 text-center">
            <div className="font-black text-emerald-950 text-xs">3. Autonomous Buyers</div>
            <div className="text-[10px] text-emerald-700">AI Agents + Razorpay Checkout</div>
            <div className="text-[10px] font-mono text-emerald-900 bg-white p-1.5 rounded-lg border border-emerald-200 mt-2">
              MCP • OpenAPI • Verified Pay
            </div>
          </div>
        </div>
      </div>

      {/* Deployment Modes Selection */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-black text-slate-900 text-sm">Deployment Modes</h3>
          <span className="text-xs text-slate-500">Choose how you deploy AgentPay</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <button
            type="button"
            onClick={() => setActiveMode("standalone")}
            className={`p-5 rounded-2xl border text-left transition-all space-y-2 ${
              activeMode === "standalone"
                ? "bg-indigo-600 text-white border-indigo-600 shadow-md"
                : "bg-white text-slate-700 border-slate-200 hover:border-slate-300"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                activeMode === "standalone" ? "bg-white/20 text-white" : "bg-slate-100 text-slate-700"
              }`}>
                Mode 1
              </span>
              <span className="text-sm">🏪</span>
            </div>
            <div className="font-black text-sm">Standalone Store</div>
            <p className={`text-[11px] leading-relaxed ${activeMode === "standalone" ? "text-indigo-100" : "text-slate-500"}`}>
              Built-in seed catalog with local PostgreSQL/SQLite database, native AI shopping assistant, and Razorpay standard checkout.
            </p>
          </button>

          <button
            type="button"
            onClick={() => setActiveMode("existing_store")}
            className={`p-5 rounded-2xl border text-left transition-all space-y-2 ${
              activeMode === "existing_store"
                ? "bg-indigo-600 text-white border-indigo-600 shadow-md"
                : "bg-white text-slate-700 border-slate-200 hover:border-slate-300"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                activeMode === "existing_store" ? "bg-white/20 text-white" : "bg-slate-100 text-slate-700"
              }`}>
                Mode 2 (Recommended)
              </span>
              <span className="text-sm">🔌</span>
            </div>
            <div className="font-black text-sm">Existing Platform Adapter</div>
            <p className={`text-[11px] leading-relaxed ${activeMode === "existing_store" ? "text-indigo-100" : "text-slate-500"}`}>
              Plug on top of Shopify, WooCommerce, or Custom REST API. Converts your existing catalog into machine-readable AI buyer interfaces in 5 minutes.
            </p>
          </button>

          <button
            type="button"
            onClick={() => setActiveMode("marketplace")}
            className={`p-5 rounded-2xl border text-left transition-all space-y-2 ${
              activeMode === "marketplace"
                ? "bg-indigo-600 text-white border-indigo-600 shadow-md"
                : "bg-white text-slate-700 border-slate-200 hover:border-slate-300"
            }`}
          >
            <div className="flex items-center justify-between">
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                activeMode === "marketplace" ? "bg-white/20 text-white" : "bg-slate-100 text-slate-700"
              }`}>
                Mode 3
              </span>
              <span className="text-sm">🌐</span>
            </div>
            <div className="font-black text-sm">Multi-Merchant Marketplace</div>
            <p className={`text-[11px] leading-relaxed ${activeMode === "marketplace" ? "text-indigo-100" : "text-slate-500"}`}>
              Aggregates offers across thousands of distinct merchants with tenant isolation, comparative AI matching, and centralized payment settlements.
            </p>
          </button>
        </div>
      </div>

      {/* Platform Connectors & Sync Simulator */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h3 className="font-black text-slate-900 text-base">5-Minute Store Connector &amp; Live Ingestion</h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Connect your platform credentials to sync products, prices, and stock into AgentPay&apos;s canonical AI schema.
            </p>
          </div>

          {/* Platform Switcher Tabs */}
          <div className="flex items-center bg-slate-100 p-1 rounded-xl gap-1 text-xs font-bold">
            <button
              onClick={() => {
                setActivePlatform("shopify");
                setStoreDomain("mystore.myshopify.com");
              }}
              className={`px-3 py-1.5 rounded-lg transition-all ${
                activePlatform === "shopify" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Shopify
            </button>
            <button
              onClick={() => {
                setActivePlatform("woocommerce");
                setStoreDomain("https://store.example.com");
              }}
              className={`px-3 py-1.5 rounded-lg transition-all ${
                activePlatform === "woocommerce" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              WooCommerce
            </button>
            <button
              onClick={() => {
                setActivePlatform("generic_rest");
                setStoreDomain("https://api.mycustomstore.com/v1");
              }}
              className={`px-3 py-1.5 rounded-lg transition-all ${
                activePlatform === "generic_rest" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Custom REST API
            </button>
            <button
              onClick={() => {
                setActivePlatform("feed");
                setStoreDomain("products.csv");
              }}
              className={`px-3 py-1.5 rounded-lg transition-all ${
                activePlatform === "feed" ? "bg-white text-indigo-700 shadow-xs" : "text-slate-600 hover:text-slate-900"
              }`}
            >
              Catalog Feed (CSV/JSONL)
            </button>
          </div>
        </div>

        {/* Configuration & Sync Console */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 text-xs">
          {/* Left: Input Form */}
          <div className="space-y-4 p-5 bg-slate-50 rounded-2xl border border-slate-200">
            <div className="space-y-1.5">
              <label className="font-bold text-slate-800 text-[11px] uppercase tracking-wider">
                {activePlatform === "shopify"
                  ? "Shopify Store Domain"
                  : activePlatform === "woocommerce"
                  ? "WordPress / WooCommerce URL"
                  : activePlatform === "generic_rest"
                  ? "Merchant REST Base URL"
                  : "Catalog Feed Filename"}
              </label>
              <input
                type="text"
                value={storeDomain}
                onChange={(e) => setStoreDomain(e.target.value)}
                className="w-full px-3.5 py-2.5 bg-white border border-slate-300 rounded-xl text-slate-900 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="space-y-1.5">
              <label className="font-bold text-slate-800 text-[11px] uppercase tracking-wider">
                {activePlatform === "feed" ? "Feed Format" : "Admin API Key / Access Token"}
              </label>
              <input
                type="text"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full px-3.5 py-2.5 bg-white border border-slate-300 rounded-xl text-slate-900 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="p-3 bg-white rounded-xl border border-slate-200 space-y-1.5 text-[11px] text-slate-600">
              <div className="font-bold text-slate-800">Connector Sync Features:</div>
              <div className="flex items-center gap-2">✓ Automatic Product Variants &amp; Spec Extraction</div>
              <div className="flex items-center gap-2">✓ Real-time Inventory &amp; Price Polling</div>
              <div className="flex items-center gap-2">✓ Policy Enforcement (14-day return, max order caps)</div>
              <div className="flex items-center gap-2">✓ Webhook Dispatch on Verified Razorpay Payment</div>
            </div>

            <button
              type="button"
              disabled={syncStatus.running}
              onClick={handleTestSync}
              className="w-full py-3 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold rounded-xl shadow-xs transition-all flex items-center justify-center gap-2"
            >
              {syncStatus.running ? (
                <>
                  <span className="animate-spin">⟳</span>
                  <span>Synchronizing Catalog &amp; Inventory...</span>
                </>
              ) : (
                <>
                  <span>✦</span>
                  <span>Test Connection &amp; Ingest Catalog</span>
                </>
              )}
            </button>
          </div>

          {/* Right: Live Canonical Mapping Console */}
          <div className="space-y-3 p-5 bg-slate-950 text-slate-200 rounded-2xl font-mono text-[11px] flex flex-col justify-between border border-slate-800">
            <div className="space-y-2">
              <div className="flex items-center justify-between text-slate-400 border-b border-slate-800 pb-2">
                <span>CANONICAL SCHEMA ADAPTER PREVIEW</span>
                <span className="text-emerald-400">SCHEMA: V1.0</span>
              </div>

              {syncStatus.result === "success" ? (
                <div className="space-y-2 text-emerald-300">
                  <div>✓ Connection verified to {storeDomain}</div>
                  <div>✓ Canonical Ingestion Complete ({syncStatus.durationMs}ms)</div>
                  <div className="text-[10px] text-slate-400">
                    Imported {syncStatus.productsCount} products &amp; {syncStatus.offersCount} offers into AI catalog.
                  </div>
                </div>
              ) : syncStatus.result === "error" ? (
                <div className="space-y-2 text-rose-300 py-4">
                  <div className="font-bold">✕ Ingestion Failed or Store Inaccessible</div>
                  <div className="text-[10px] text-slate-400">
                    Could not synchronize with {storeDomain}. Verify endpoint accessibility and credentials.
                  </div>
                </div>
              ) : (
                <div className="space-y-2 text-slate-400 py-6 text-center">
                  <p>Click &quot;Test Connection &amp; Ingest Catalog&quot; to test platform conversion into AgentPay canonical schema.</p>
                  <p className="text-[10px] text-slate-500">External JSON is automatically sanitized, validated, and cached.</p>
                </div>
              )}
            </div>

            <div className="pt-2 border-t border-slate-800 flex items-center justify-between text-[10px] text-slate-400">
              <span>Status: {syncStatus.running ? "SYNCING..." : syncStatus.result === "success" ? "CONNECTED ✓" : syncStatus.result === "error" ? "FAILED ✕" : "IDLE"}</span>
              <span>Adapter Engine: Online</span>
            </div>
          </div>
        </div>
      </div>

      {/* Developer Protocols Grid: MCP, OpenAPI, Razorpay Webhook */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-xs">
        {/* MCP Protocol */}
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="p-1.5 bg-indigo-100 text-indigo-700 rounded-xl font-bold font-mono text-xs">MCP</span>
            <span className="px-2.5 py-0.5 bg-emerald-100 text-emerald-800 rounded-full font-bold text-[10px]">Active ✓</span>
          </div>
          <h4 className="font-bold text-slate-900 text-sm">Model Context Protocol</h4>
          <p className="text-slate-500 text-[11px]">
            Claude Desktop, Cursor, and LangChain agents call your store natively via standard MCP JSON-RPC tools.
          </p>
          <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-200 font-mono text-[10px] text-slate-700">
            {apiOrigin ? `${apiOrigin}/mcp/v1/sse` : "/mcp/v1/sse"}
          </div>
        </div>

        {/* REST OpenAPI */}
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="p-1.5 bg-purple-100 text-purple-700 rounded-xl font-bold font-mono text-xs">REST</span>
            <span className="px-2.5 py-0.5 bg-emerald-100 text-emerald-800 rounded-full font-bold text-[10px]">OpenAPI 3.1 ✓</span>
          </div>
          <h4 className="font-bold text-slate-900 text-sm">Commerce REST API</h4>
          <p className="text-slate-500 text-[11px]">
            Full suite of endpoints for intent extraction, product catalog search, offer revalidation, and gated checkout.
          </p>
          <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-200 font-mono text-[10px] text-slate-700">
            POST /api/v1/checkout/create
          </div>
        </div>

        {/* Razorpay Webhooks */}
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-3">
          <div className="flex items-center justify-between">
            <span className="p-1.5 bg-blue-100 text-blue-700 rounded-xl font-bold font-mono text-xs">RAZORPAY</span>
            <span className="px-2.5 py-0.5 bg-emerald-100 text-emerald-800 rounded-full font-bold text-[10px]">Verified ✓</span>
          </div>
          <h4 className="font-bold text-slate-900 text-sm">Payment Execution Layer</h4>
          <p className="text-slate-500 text-[11px]">
            HMAC-SHA256 signature verification, webhook settlement, and automatic inventory decrements upon captured payment.
          </p>
          <div className="p-2.5 bg-slate-50 rounded-xl border border-slate-200 font-mono text-[10px] text-slate-700">
            POST /api/verify-payment
          </div>
        </div>
      </div>
    </div>
  );
}
