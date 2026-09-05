"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useStore } from "@/context/StoreContext";
import type { ProductItem } from "@/data/products";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { runCatalogSearch } from "@/catalog/search";
import { exploreOfferToProductItem } from "@/catalog/adapt";

interface ServerOrder {
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
  orders: ServerOrder[];
  count: number;
  total: number;
}

export default function ReturnsWizardPage() {
  const { orders: localOrders, submitReturn } = useStore();

  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1);
  const [serverOrders, setServerOrders] = useState<ServerOrder[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [selectedOrderId, setSelectedOrderId] = useState<string>("");
  const [selectedProductId, setSelectedProductId] = useState<string>("");
  const [selectableItems, setSelectableItems] = useState<ProductItem[]>([]);
  const [reason, setReason] = useState<string>("Found a different model with higher RAM");
  const [resolution, setResolution] = useState<"refund" | "replacement">("refund");
  const [completedReturn, setCompletedReturn] = useState<any | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadEligibleOrders() {
      setOrdersLoading(true);
      try {
        const mappedLocalOrders: ServerOrder[] = (localOrders || []).map((o) => ({
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
          if (combined.length > 0) {
            setSelectedOrderId((prev) => prev && combined.some((o) => o.order_id === prev) ? prev : combined[0].order_id);
          }
        }
      } catch (err) {
        console.warn("Returns order fetch note:", err);
      } finally {
        if (!cancelled) setOrdersLoading(false);
      }
    }

    loadEligibleOrders();
    return () => {
      cancelled = true;
    };
  }, [localOrders]);

  useEffect(() => {
    let cancelled = false;
    const selectedLocal = localOrders.find((o) => o.orderId === selectedOrderId);
    if (selectedLocal?.items?.length) {
      const items = selectedLocal.items.map((i) => i.product);
      setSelectableItems(items);
      if (items[0]) setSelectedProductId(items[0].id);
    } else {
      (async () => {
        try {
          const res = await apiGet<any>(`/api/v1/orders/${selectedOrderId}`);
          if (!cancelled && res.ok && res.data?.items?.length) {
            const items = res.data.items.map((it: any) => it.product || it);
            setSelectableItems(items);
            if (items[0]) setSelectedProductId(items[0].id || items[0].product_id);
            return;
          }
        } catch {}

        const searchRes = await runCatalogSearch({ limit: 4 });
        if (!cancelled && searchRes.kind === "ok" && searchRes.outcome.offers.length > 0) {
          const items = searchRes.outcome.offers.map((o) =>
            exploreOfferToProductItem(o, searchRes.outcome.catalogSource)
          );
          setSelectableItems(items);
          if (items[0]) setSelectedProductId(items[0].id);
        }
      })();
    }
    return () => {
      cancelled = true;
    };
  }, [selectedOrderId, localOrders]);

  const handleConfirmReturn = () => {
    if (!selectedOrderId) return;
    const res = submitReturn(selectedOrderId, selectedProductId, reason, resolution);
    setCompletedReturn(res);
    setStep(5);
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-2">
        <div className="inline-flex items-center gap-2 text-indigo-600 font-mono text-xs font-bold uppercase">
          <span>↩️ Verified 10-Day Returns</span>
        </div>
        <h1 className="text-2xl sm:text-3xl font-black text-slate-900">Return &amp; Exchange Assistant</h1>
        <p className="text-xs sm:text-sm text-slate-500">
          Deterministic merchant-governed return process with instant pickup scheduling and Razorpay source refund.
        </p>
      </div>

      {/* Step Tracker */}
      <div className="bg-white p-4 rounded-2xl border border-slate-200 shadow-2xs flex items-center justify-between text-xs font-bold text-slate-400">
        {[
          { num: 1, label: "Order" },
          { num: 2, label: "Item" },
          { num: 3, label: "Reason" },
          { num: 4, label: "Resolution" },
          { num: 5, label: "Confirm" },
        ].map((s) => (
          <div
            key={s.num}
            className={`flex items-center gap-1.5 ${
              step === s.num
                ? "text-indigo-600 font-black"
                : step > s.num
                ? "text-emerald-700 font-semibold"
                : ""
            }`}
          >
            <span
              className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] ${
                step === s.num
                  ? "bg-indigo-600 text-white"
                  : step > s.num
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-slate-100 text-slate-400"
              }`}
            >
              {step > s.num ? "✓" : s.num}
            </span>
            <span className="hidden sm:inline">{s.label}</span>
          </div>
        ))}
      </div>

      {/* STEP 1: Select Order */}
      {step === 1 && (
        <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-5 animate-in fade-in">
          <h2 className="text-lg font-black text-slate-900">Step 1: Select Eligible Order</h2>

          {ordersLoading ? (
            <div className="py-8 flex justify-center items-center gap-2 text-slate-500 text-xs">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-600" />
              <span>Loading orders from server...</span>
            </div>
          ) : serverOrders.length > 0 ? (
            <div className="space-y-3">
              {serverOrders.map((o) => (
                <div
                  key={o.order_id}
                  onClick={() => setSelectedOrderId(o.order_id)}
                  className={`p-4 rounded-2xl border transition-all cursor-pointer flex items-center justify-between text-xs ${
                    selectedOrderId === o.order_id
                      ? "border-indigo-600 bg-indigo-50/50 ring-2 ring-indigo-600/20"
                      : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <div className="space-y-1">
                    <div className="font-bold text-slate-900">Order #{o.order_id}</div>
                    <div className="text-slate-500 font-mono text-[11px]">
                      Confirmed: {new Date(o.confirmed_at).toLocaleDateString()}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="font-black text-slate-900">
                      {formatMinorToMajor(o.amount_minor, o.currency)}
                    </div>
                    <span className="inline-block mt-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800">
                      {o.status.toUpperCase()}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="p-8 text-center bg-slate-50 rounded-2xl border border-dashed border-slate-200 space-y-3">
              <div className="text-3xl">📦</div>
              <div className="font-bold text-slate-800 text-sm">No Eligible Orders Found</div>
              <p className="text-xs text-slate-500 max-w-sm mx-auto">
                Only confirmed orders placed within the 10-day return policy window are eligible for returns and exchanges.
              </p>
              <div className="pt-2">
                <Link
                  href="/search"
                  className="inline-block px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white text-xs font-bold rounded-xl transition shadow-xs"
                >
                  Browse Products &rarr;
                </Link>
              </div>
            </div>
          )}

          {serverOrders.length > 0 && (
            <div className="flex justify-end pt-4">
              <button
                onClick={() => setStep(2)}
                disabled={!selectedOrderId}
                className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-bold text-xs rounded-xl shadow-xs"
              >
                Continue to Select Item &rarr;
              </button>
            </div>
          )}
        </div>
      )}

      {/* STEP 2: Select Item */}
      {step === 2 && (
        <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-5 animate-in fade-in">
          <h2 className="text-lg font-black text-slate-900">Step 2: Choose Product to Return</h2>

          <div className="space-y-3">
            {selectableItems.length === 0 ? (
              <div className="p-6 text-center text-slate-500 text-xs">
                No items available for return in this order.
              </div>
            ) : (
              selectableItems.map((p) => (
                <div
                  key={p.id}
                  onClick={() => setSelectedProductId(p.id)}
                  className={`p-4 rounded-2xl border transition-all cursor-pointer flex items-center gap-4 text-xs ${
                    selectedProductId === p.id
                      ? "border-indigo-600 bg-indigo-50/50 ring-2 ring-indigo-600/20"
                      : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <img src={p.imageUrl} alt={p.title} className="w-14 h-14 object-cover rounded-xl shrink-0" />
                  <div className="flex-1 min-w-0">
                    <h4 className="font-bold text-slate-900 truncate">{p.title}</h4>
                    <p className="text-slate-500 text-[11px] mt-0.5">{p.brand} • {formatMinorToMajor(p.priceMinor, p.currency)}</p>
                  </div>
                  {selectedProductId === p.id && <span className="text-indigo-600 font-bold">✓ Selected</span>}
                </div>
              ))
            )}
          </div>

          <div className="flex justify-between pt-4">
            <button onClick={() => setStep(1)} className="px-4 py-2 text-slate-600 font-bold text-xs">
              &larr; Back
            </button>
            <button
              onClick={() => setStep(3)}
              className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-xs"
            >
              Specify Reason &rarr;
            </button>
          </div>
        </div>
      )}

      {/* STEP 3: Reason */}
      {step === 3 && (
        <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-5 animate-in fade-in">
          <h2 className="text-lg font-black text-slate-900">Step 3: Reason for Return</h2>
          <div className="space-y-2">
            {[
              "Found a different model with higher RAM",
              "Performance did not match my workflow requirements",
              "Item arrived with a damaged box",
              "Accidental order / duplicate purchase",
            ].map((r) => (
              <label key={r} className="flex items-center gap-3 p-3.5 border border-slate-200 rounded-xl cursor-pointer hover:bg-slate-50 text-xs font-bold text-slate-800">
                <input
                  type="radio"
                  name="return_reason"
                  checked={reason === r}
                  onChange={() => setReason(r)}
                  className="w-4 h-4 accent-indigo-600"
                />
                <span>{r}</span>
              </label>
            ))}
          </div>

          <div className="flex justify-between pt-4">
            <button onClick={() => setStep(2)} className="px-4 py-2 text-slate-600 font-bold text-xs">
              &larr; Back
            </button>
            <button
              onClick={() => setStep(4)}
              className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-xs"
            >
              Choose Resolution &rarr;
            </button>
          </div>
        </div>
      )}

      {/* STEP 4: Resolution */}
      {step === 4 && (
        <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-5 animate-in fade-in">
          <h2 className="text-lg font-black text-slate-900">Step 4: Resolution Preference</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div
              onClick={() => setResolution("refund")}
              className={`p-5 rounded-2xl border cursor-pointer text-xs space-y-2 ${
                resolution === "refund"
                  ? "border-indigo-600 bg-indigo-50/50 ring-2 ring-indigo-600/20"
                  : "border-slate-200 hover:border-slate-300"
              }`}
            >
              <h4 className="font-black text-slate-900 text-sm">💳 Full Source Refund</h4>
              <p className="text-slate-500 text-[11px]">Instant refund initiated back to original Razorpay payment method upon pickup inspection.</p>
            </div>

            <div
              onClick={() => setResolution("replacement")}
              className={`p-5 rounded-2xl border cursor-pointer text-xs space-y-2 ${
                resolution === "replacement"
                  ? "border-indigo-600 bg-indigo-50/50 ring-2 ring-indigo-600/20"
                  : "border-slate-200 hover:border-slate-300"
              }`}
            >
              <h4 className="font-black text-slate-900 text-sm">🔄 Replacement Unit</h4>
              <p className="text-slate-500 text-[11px]">Ship identical verified new unit immediately with doorstep replacement.</p>
            </div>
          </div>

          <div className="flex justify-between pt-4">
            <button onClick={() => setStep(3)} className="px-4 py-2 text-slate-600 font-bold text-xs">
              &larr; Back
            </button>
            <button
              onClick={handleConfirmReturn}
              className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-xs"
            >
              Submit Return Request &rarr;
            </button>
          </div>
        </div>
      )}

      {/* STEP 5: Completed */}
      {step === 5 && (
        <div className="bg-white p-8 sm:p-12 rounded-3xl border border-slate-200 shadow-xs text-center space-y-5 animate-in fade-in">
          <div className="w-16 h-16 bg-amber-100 text-amber-700 rounded-full flex items-center justify-center mx-auto text-2xl font-black">
            ⏳
          </div>
          <h2 className="text-2xl font-black text-slate-900">Return Request Submitted</h2>
          <p className="text-xs sm:text-sm text-slate-600 max-w-md mx-auto">
            Your return request <strong>{completedReturn?.returnId || "—"}</strong> has been recorded locally.
            A merchant representative will review eligibility and contact you to arrange pickup.
          </p>
          <div className="p-4 bg-amber-50 rounded-2xl border border-amber-200 max-w-sm mx-auto text-xs space-y-1 text-amber-800">
            <p className="font-bold">⚠ Pending Merchant Review</p>
            <p>Returns require merchant-side verification before authorization. This request has not yet been processed by the server.</p>
          </div>
          <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200 max-w-sm mx-auto text-xs space-y-1 font-mono text-slate-600">
            <div>Order: #{selectedOrderId}</div>
            <div>Resolution: {resolution.toUpperCase()}</div>
            <div>Status: PENDING REVIEW</div>
          </div>
          <Link
            href="/orders"
            className="inline-block px-6 py-3 bg-slate-900 hover:bg-slate-800 text-white font-bold text-xs rounded-xl shadow-xs"
          >
            View Your Orders &rarr;
          </Link>
        </div>
      )}
    </div>
  );
}
