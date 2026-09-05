"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useStore } from "@/context/StoreContext";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, type ApiError } from "@/lib/api";
import { Loader2 } from "lucide-react";

interface OrderRecord {
  schema_version: string;
  order_id: string;
  checkout_id: string;
  payment_id: string;
  buyer_id: string;
  merchant_id: string;
  amount_minor: number;
  currency: string;
  status: "confirmed" | "completed" | "cancelled";
  confirmed_at: string;
}

interface OrderPage {
  orders: OrderRecord[];
  count: number;
  total: number;
}

export default function AccountPage() {
  const { userPreferences, updateUserPreferences, orders: storeOrders } = useStore();

  const [activeTab, setActiveTab] = useState<"profile" | "ai_prefs" | "orders" | "addresses" | "security">("ai_prefs");
  const [autoLimit, setAutoLimit] = useState(userPreferences.autoApprovalLimitMinor / 100);
  const [savedSuccess, setSavedSuccess] = useState(false);

  const [serverOrders, setServerOrders] = useState<OrderRecord[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadServerOrders() {
      setOrdersLoading(true);
      try {
        const mappedLocalOrders: OrderRecord[] = (storeOrders || []).map((o) => ({
          schema_version: "1.0",
          order_id: o.orderId,
          checkout_id: o.orderId.replace("ord_", "chk_"),
          payment_id: o.paymentId,
          buyer_id: "byr_active_session",
          merchant_id: "mrc_demo_electronics",
          amount_minor: o.totalMinor,
          currency: o.currency || "INR",
          status: (o.status as any) || "confirmed",
          confirmed_at: o.createdAt || new Date().toISOString(),
        }));

        const res = await apiGet<OrderPage>("/api/v1/orders?limit=20&offset=0");
        const remoteOrders = res.ok && Array.isArray(res.data?.orders) ? res.data.orders : [];

        const combined = [...remoteOrders];
        for (const local of mappedLocalOrders) {
          if (!combined.some((o) => o.order_id === local.order_id)) {
            combined.unshift(local);
          }
        }

        if (!cancelled) {
          setServerOrders(combined);
        }
      } catch (err) {
        console.warn("Failed to load account orders:", err);
      } finally {
        if (!cancelled) setOrdersLoading(false);
      }
    }

    loadServerOrders();
    return () => {
      cancelled = true;
    };
  }, [storeOrders]);

  const handleSavePreferences = (e: React.FormEvent) => {
    e.preventDefault();
    updateUserPreferences({
      autoApprovalLimitMinor: autoLimit * 100,
    });
    setSavedSuccess(true);
    setTimeout(() => setSavedSuccess(false), 2500);
  };

  return (
    <div className="max-w-5xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 rounded-2xl bg-indigo-600 text-white font-black text-xl flex items-center justify-center shadow-xs">
            AS
          </div>
          <div>
            <h1 className="text-xl sm:text-2xl font-black text-slate-900">Alex Shopper</h1>
            <p className="text-xs text-slate-500 font-mono">shopper@agentpay.dev • Verified Account ✓</p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-8 items-start">
        {/* Navigation Sidebar */}
        <div className="md:col-span-4 bg-white p-4 rounded-3xl border border-slate-200 shadow-xs space-y-1 text-xs font-bold">
          {[
            { id: "ai_prefs", label: "✦ AI Shopping Preferences" },
            { id: "orders", label: `📦 Your Orders (${serverOrders.length})` },
            { id: "profile", label: "👤 Personal Profile" },
            { id: "addresses", label: "📍 Saved Addresses" },
            { id: "security", label: "🔒 Security & Limits" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`w-full text-left p-3 rounded-2xl transition-all ${
                activeTab === tab.id
                  ? "bg-slate-900 text-white shadow-2xs"
                  : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content Panel */}
        <div className="md:col-span-8 bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6">
          {activeTab === "ai_prefs" && (
            <form onSubmit={handleSavePreferences} className="space-y-6 text-xs animate-in fade-in">
              <div>
                <div className="flex items-center gap-2">
                  <span className="p-1 bg-indigo-600 text-white rounded-lg font-mono text-[10px]">✦</span>
                  <h2 className="text-lg font-black text-slate-900">AI Shopping &amp; Authorization Preferences</h2>
                </div>
                <p className="text-slate-500 text-[11px] mt-1">
                  Control how AgentPay autonomously evaluates policies and assists with research.
                </p>
              </div>

              {savedSuccess && (
                <div className="p-3 bg-emerald-50 text-emerald-800 rounded-xl font-bold border border-emerald-200">
                  ✓ Preferences saved successfully!
                </div>
              )}

              {/* Toggles */}
              <div className="space-y-4 divide-y divide-slate-100">
                <div className="flex items-center justify-between pt-2">
                  <div>
                    <h4 className="font-bold text-slate-900">AI Recommendations</h4>
                    <p className="text-slate-500 text-[11px]">Generate natural language match insights and fit checklists.</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={userPreferences.aiRecommendations}
                    onChange={(e) => updateUserPreferences({ aiRecommendations: e.target.checked })}
                    className="w-5 h-5 accent-indigo-600 rounded"
                  />
                </div>

                <div className="flex items-center justify-between pt-4">
                  <div>
                    <h4 className="font-bold text-slate-900">Use Customer Review Insights</h4>
                    <p className="text-slate-500 text-[11px]">Synthesize pros, cons, and sentiment metrics from verified reviews.</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={userPreferences.useReviewInsights}
                    onChange={(e) => updateUserPreferences({ useReviewInsights: e.target.checked })}
                    className="w-5 h-5 accent-indigo-600 rounded"
                  />
                </div>

                <div className="flex items-center justify-between pt-4">
                  <div>
                    <h4 className="font-bold text-slate-900">Use Web &amp; Documentation Research</h4>
                    <p className="text-slate-500 text-[11px]">Fetch anti-SSRF verified external hardware documentation.</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={userPreferences.useWebResearch}
                    onChange={(e) => updateUserPreferences({ useWebResearch: e.target.checked })}
                    className="w-5 h-5 accent-indigo-600 rounded"
                  />
                </div>

                <div className="flex items-center justify-between pt-4">
                  <div>
                    <h4 className="font-bold text-slate-900">Explicit Approval Before Payment</h4>
                    <p className="text-slate-500 text-[11px]">Require human confirmation whenever total exceeds auto limit.</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={userPreferences.askBeforePurchases}
                    onChange={(e) => updateUserPreferences({ askBeforePurchases: e.target.checked })}
                    className="w-5 h-5 accent-indigo-600 rounded"
                  />
                </div>
              </div>

              {/* Auto Approval Limit (Requirement 27) */}
              <div className="space-y-2 pt-2 border-t border-slate-100">
                <label className="font-bold text-slate-900 block">Automatic Policy Pre-Approval Limit (INR):</label>
                <div className="flex items-center gap-3">
                  <input
                    type="number"
                    value={autoLimit}
                    onChange={(e) => setAutoLimit(Number(e.target.value))}
                    step={1000}
                    className="p-3 border border-slate-200 rounded-xl font-mono text-sm w-48 font-bold"
                  />
                  <span className="text-slate-500 text-[11px]">Orders below this threshold can be pre-authorized automatically.</span>
                </div>
              </div>

              {/* Preferred Brands */}
              <div className="space-y-2 pt-2 border-t border-slate-100">
                <label className="font-bold text-slate-900 block">Preferred Brands Priority:</label>
                <div className="flex flex-wrap gap-2">
                  {["Lenovo", "Dell", "Sony", "Apple", "Samsung", "Keychron", "LG"].map((brand) => (
                    <button
                      key={brand}
                      type="button"
                      onClick={() => {
                        const exists = userPreferences.preferredBrands.includes(brand);
                        updateUserPreferences({
                          preferredBrands: exists
                            ? userPreferences.preferredBrands.filter((b) => b !== brand)
                            : [...userPreferences.preferredBrands, brand],
                        });
                      }}
                      className={`px-3 py-1.5 rounded-xl font-bold border transition-all ${
                        userPreferences.preferredBrands.includes(brand)
                          ? "bg-indigo-50 border-indigo-600 text-indigo-700"
                          : "bg-slate-50 border-slate-200 text-slate-600"
                      }`}
                    >
                      {brand} {userPreferences.preferredBrands.includes(brand) ? "✓" : "+"}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex justify-end pt-4">
                <button
                  type="submit"
                  className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-xs"
                >
                  Save Settings &rarr;
                </button>
              </div>
            </form>
          )}

          {activeTab === "orders" && (
            <div className="space-y-4 text-xs animate-in fade-in">
              <h2 className="text-lg font-black text-slate-900">Your Live Orders</h2>
              {ordersLoading ? (
                <div className="py-8 flex justify-center items-center gap-2 text-slate-500">
                  <Loader2 className="h-5 w-5 animate-spin text-indigo-600" />
                  <span>Loading orders from server...</span>
                </div>
              ) : serverOrders.length === 0 ? (
                <div className="text-center py-8 text-slate-400">No orders placed yet.</div>
              ) : (
                serverOrders.map((o) => (
                  <div key={o.order_id} className="p-4 bg-slate-50 rounded-2xl border border-slate-200 flex justify-between items-center">
                    <div>
                      <div className="font-bold text-slate-900">Order #{o.order_id}</div>
                      <div className="text-slate-500 font-mono text-[11px]">
                        Amount: {formatMinorToMajor(o.amount_minor, o.currency)} • Status: {o.status.toUpperCase()}
                      </div>
                    </div>
                    <Link href={`/orders/${o.order_id}`} className="text-indigo-600 font-bold hover:underline">
                      Track Order &rarr;
                    </Link>
                  </div>
                ))
              )}
            </div>
          )}

          {activeTab === "profile" && (
            <div className="space-y-4 text-xs animate-in fade-in">
              <h2 className="text-lg font-black text-slate-900">Personal Profile</h2>
              <div className="space-y-3 font-medium text-slate-700">
                <div>Name: <strong>Alex Shopper</strong></div>
                <div>Email: <strong>shopper@agentpay.dev</strong></div>
                <div>Phone: <strong>+91 98765 43210</strong></div>
              </div>
            </div>
          )}

          {activeTab === "addresses" && (
            <div className="space-y-4 text-xs animate-in fade-in">
              <h2 className="text-lg font-black text-slate-900">Saved Addresses</h2>
              <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200 space-y-1">
                <span className="font-bold text-slate-900 block">Default Home Address</span>
                <p className="text-slate-600">742 Evergreen Terrace, Cyber Hub, Bengaluru, Karnataka 560103</p>
              </div>
            </div>
          )}

          {activeTab === "security" && (
            <div className="space-y-4 text-xs animate-in fade-in">
              <h2 className="text-lg font-black text-slate-900">Security &amp; Gate Limits</h2>
              <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200 space-y-2">
                <div>Hard Transaction Ceiling: <strong>₹2,00,000.00</strong></div>
                <div>Cryptographic HMAC Verification: <strong>ENABLED</strong></div>
                <div>Two-Factor Authentication: <strong>ACTIVE</strong></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
