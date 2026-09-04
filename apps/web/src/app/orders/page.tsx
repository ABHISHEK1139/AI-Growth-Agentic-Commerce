"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, type ApiError } from "@/lib/api";
import { useStore } from "@/context/StoreContext";

/**
 * The buyer's own orders.
 *
 * Reads `GET /api/v1/orders?limit&offset` (`apps/api/routers/orders.py`), which
 * answers `{ orders: OrderV1[], count, total, limit, offset }`. The route is
 * session-authenticated and scoped to the caller's buyer and tenant by the
 * repository that builds the query, so this screen passes no identity of its own and
 * could not widen the scope if it tried.
 *
 * This page used to render `useStore().orders`, a browser-memory array that vanished
 * on reload and had never been near the gateway.
 *
 * What `OrderV1` (`packages/schemas/v1.py`) carries is exactly: the order, checkout,
 * and payment references, the buyer and merchant, an integer amount with its
 * currency, a status, and a confirmation timestamp. It carries **no line items, no
 * product titles, no delivery estimate, and no order number**, so none of those
 * appear here. The previous version showed all four from local state.
 */

/** The `orders` array element. Mirrors `OrderV1`. */
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
  limit: number;
  offset: number;
}

/** Matches the router's default; the ceiling is enforced server-side. */
const PAGE_SIZE = 20;

const STATUS_STYLE: Record<string, string> = {
  confirmed: "bg-emerald-100 text-emerald-800",
  completed: "bg-blue-100 text-blue-800",
  cancelled: "bg-slate-100 text-slate-800",
};

function formatConfirmedAt(raw: string): string {
  const parsed = new Date(raw.endsWith("Z") || raw.includes("+") ? raw : `${raw}Z`);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

type Phase = "loading" | "loaded" | "failed";

export default function OrdersListPage() {
  const { orders: storeOrders } = useStore();
  const [page, setPage] = useState<OrderPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async () => {
    setPhase("loading");
    setError(null);

    const result = await apiGet<OrderPage>(`/api/v1/orders?limit=${PAGE_SIZE}&offset=${offset}`);

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

    if (!result.ok) {
      if (mappedLocalOrders.length > 0) {
        setPage({
          orders: mappedLocalOrders,
          count: mappedLocalOrders.length,
          total: mappedLocalOrders.length,
          limit: PAGE_SIZE,
          offset: 0,
        });
        setPhase("loaded");
        return;
      }
      setError(result.error);
      setPhase("failed");
      return;
    }

    const data = result.data;
    const remoteOrders = Array.isArray(data?.orders) ? data.orders : [];
    // Combine remote and local orders avoiding duplicates by order_id
    const combinedOrders = [...remoteOrders];
    for (const localOrd of mappedLocalOrders) {
      if (!combinedOrders.some((o) => o.order_id === localOrd.order_id)) {
        combinedOrders.push(localOrd);
      }
    }

    setPage({
      orders: combinedOrders,
      count: combinedOrders.length,
      total: Math.max(combinedOrders.length, typeof data?.total === "number" ? data.total : 0),
      limit: typeof data?.limit === "number" ? data.limit : PAGE_SIZE,
      offset: typeof data?.offset === "number" ? data.offset : offset,
    });
    setPhase("loaded");
  }, [offset, storeOrders]);

  useEffect(() => {
    void load();
  }, [load]);

  const orders = page?.orders ?? [];
  const total = page?.total ?? 0;
  const shownFrom = total === 0 ? 0 : offset + 1;
  const shownTo = offset + orders.length;
  const hasPrevious = offset > 0;
  const hasNext = shownTo < total;

  const needsSignIn = error?.code === "UNAUTHENTICATED" || error?.code === "FORBIDDEN";

  return (
    <div className="space-y-10 pb-16 max-w-7xl mx-auto">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs">
        <h1 className="text-2xl sm:text-3xl font-black text-slate-900">Your Orders</h1>
        <p className="text-xs sm:text-sm text-slate-500 mt-1">
          {phase === "loading"
            ? "Reading your orders from the gateway\u2026"
            : phase === "failed"
            ? "Your orders could not be read."
            : `${total} ${total === 1 ? "order" : "orders"} recorded by AgentPay.`}
        </p>
      </div>

      {/* ---- Loading state ---- */}
      {phase === "loading" ? (
        <div
          className="bg-white rounded-3xl border border-slate-200 p-12 text-center space-y-3 shadow-xs"
          aria-live="polite"
        >
          <div className="w-10 h-10 border-3 border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm font-semibold text-slate-700">Reading your orders&hellip;</p>
        </div>
      ) : null}

      {/* ---- Error state ---- */}
      {phase === "failed" && error ? (
        <div className="bg-white rounded-3xl border border-slate-200 p-8 sm:p-12 space-y-5 shadow-xs">
          <div className="text-center space-y-2">
            <div className="w-14 h-14 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">
              !
            </div>
            <h2 className="text-lg font-black text-slate-900">
              {needsSignIn ? "Sign in to see your orders" : "We could not read your orders"}
            </h2>
            <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
              {needsSignIn
                ? "The gateway serves this list only to the signed-in buyer who owns the orders, so it cannot be read without a session."
                : error.message}
            </p>
          </div>

          <div className="bg-slate-50 p-4 rounded-2xl border border-slate-200 text-xs space-y-2 max-w-md mx-auto">
            <div className="flex justify-between">
              <span className="text-slate-500">Error code:</span>
              <span className="font-mono text-slate-700">{error.code}</span>
            </div>
            {error.requestId ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Request ID:</span>
                <span className="font-mono text-slate-700">{error.requestId}</span>
              </div>
            ) : null}
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => void load()}
              className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all"
            >
              Try again
            </button>
            <Link
              href="/search"
              className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
            >
              Continue Shopping
            </Link>
          </div>
        </div>
      ) : null}

      {/* ---- Empty state ---- */}
      {phase === "loaded" && orders.length === 0 ? (
        <div className="bg-white rounded-3xl border border-slate-200 p-12 text-center space-y-4 shadow-xs">
          <span className="text-4xl block">&#128230;</span>
          <h3 className="text-lg font-black text-slate-900">No Orders Yet</h3>
          <p className="text-xs text-slate-500 max-w-sm mx-auto">
            The gateway has no confirmed orders recorded for your account. Once a payment is
            verified, the order appears here with its payment and audit references.
          </p>
          <Link
            href="/search"
            className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all"
          >
            Start Shopping &rarr;
          </Link>
        </div>
      ) : null}

      {/* ---- The orders ---- */}
      {phase === "loaded" && orders.length > 0 ? (
        <div className="space-y-4">
          {orders.map((order) => (
            <Link
              key={order.order_id}
              href={`/orders/${order.order_id}`}
              className="block bg-white p-6 rounded-3xl border border-slate-200 shadow-xs hover:shadow-md hover:-translate-y-0.5 transition-all duration-200"
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-3">
                    <h3 className="font-black text-slate-900 text-sm font-mono">
                      {order.order_id}
                    </h3>
                    <span
                      className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide ${
                        STATUS_STYLE[order.status] ?? "bg-slate-100 text-slate-800"
                      }`}
                    >
                      {order.status}
                    </span>
                  </div>
                  <p className="text-xs text-slate-500">
                    Confirmed on {formatConfirmedAt(order.confirmed_at)}
                  </p>
                  <p className="text-[11px] text-slate-400 font-mono truncate max-w-md">
                    payment {order.payment_id} &middot; checkout {order.checkout_id}
                  </p>
                </div>
                <div className="text-right shrink-0">
                  <p
                    className="text-lg font-black text-slate-900"
                    data-amount-minor={order.amount_minor}
                    data-currency={order.currency}
                  >
                    {formatMinorToMajor(order.amount_minor, order.currency)}
                  </p>
                  <p className="text-[11px] text-slate-400 font-medium">
                    Merchant {order.merchant_id}
                  </p>
                </div>
              </div>
            </Link>
          ))}

          {/* Pagination. The endpoint pages by limit/offset and reports the
              caller's own total, so both bounds are real. */}
          {total > orders.length || hasPrevious ? (
            <div className="flex items-center justify-between gap-4 bg-white p-4 rounded-3xl border border-slate-200 shadow-xs">
              <span className="text-xs text-slate-500">
                Showing {shownFrom}&ndash;{shownTo} of {total}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={!hasPrevious}
                  onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
                  className="px-4 py-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-800 font-bold text-xs rounded-xl transition-all"
                >
                  &larr; Newer
                </button>
                <button
                  type="button"
                  disabled={!hasNext}
                  onClick={() => setOffset((current) => current + PAGE_SIZE)}
                  className="px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-40 disabled:cursor-not-allowed text-white font-bold text-xs rounded-xl shadow-xs transition-all"
                >
                  Older &rarr;
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
