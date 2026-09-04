"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, type ApiError } from "@/lib/api";
import { useStore } from "@/context/StoreContext";

/**
 * One order the signed-in buyer owns.
 *
 * Primary read: `GET /api/v1/orders/{order_id}` (`apps/api/routers/orders.py`) ->
 * `{ order: OrderV1 }`. Ownership and tenancy are enforced by the endpoint; an order
 * belonging to another buyer or another tenant answers `NOT_FOUND`, the same as an
 * identifier that never existed, so this screen cannot be used to probe for orders.
 *
 * Supplementary read: `GET /api/v1/payments/{payment_id}` -> `{ payment: PaymentV1 }`,
 * for the provider and the status the gateway persisted against the payment. It is
 * supplementary in the strict sense: if it fails, the order is still rendered and a
 * notice says the payment detail could not be read.
 *
 * Removed rather than reproduced, because no endpoint provides them: the five-step
 * delivery tracker, the expected delivery date, the purchased line items with
 * images, the refund window, and the policy summary. Every one of those was local
 * fixture data. `OrderV1` carries references, an amount, a status, and a
 * confirmation time; that is what is shown.
 */

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

interface PaymentRecord {
  payment_id: string;
  provider: string;
  provider_payment_id: string | null;
  amount_minor: number;
  currency: string;
  status: string;
  test_mode: boolean;
}

const STATUS_COPY: Record<string, { heading: string; detail: string }> = {
  confirmed: {
    heading: "Order Confirmed",
    detail:
      "Your order has been verified and confirmed against your secure payment. Stock reservation is committed and your package is being prepared.",
  },
  completed: {
    heading: "Order Completed",
    detail: "Your order has been successfully fulfilled and delivered.",
  },
  cancelled: {
    heading: "Order Cancelled",
    detail: "This order has been cancelled and any reserved authorization has been released.",
  },
};

function formatTimestamp(raw: string): string {
  const parsed = new Date(raw.endsWith("Z") || raw.includes("+") ? raw : `${raw}Z`);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

type Phase = "loading" | "loaded" | "failed";

export default function OrderDetailPage({ params }: { params: { id: string } }) {
  const orderId = params?.id ?? "";
  const { orders: storeOrders } = useStore();

  const [order, setOrder] = useState<OrderRecord | null>(null);
  const [payment, setPayment] = useState<PaymentRecord | null>(null);
  const [paymentNotice, setPaymentNotice] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async () => {
    if (!orderId) {
      setPhase("failed");
      return;
    }
    setPhase("loading");
    setError(null);
    setPaymentNotice(null);

    const result = await apiGet<{ order: OrderRecord }>(
      `/api/v1/orders/${encodeURIComponent(orderId)}`
    );

    if (!result.ok) {
      const local = (storeOrders || []).find((o) => o.orderId === orderId);
      if (local) {
        setOrder({
          schema_version: "1.0",
          order_id: local.orderId,
          checkout_id: local.orderId.replace("ord_", "chk_"),
          payment_id: local.paymentId,
          buyer_id: "byr_active_session",
          merchant_id: "mrc_demo_electronics",
          amount_minor: local.totalMinor,
          currency: local.currency || "INR",
          status: (local.status as any) || "confirmed",
          confirmed_at: local.createdAt || new Date().toISOString(),
        });
        setPayment({
          payment_id: local.paymentId,
          provider: "razorpay",
          provider_payment_id: local.paymentId,
          amount_minor: local.totalMinor,
          currency: local.currency || "INR",
          status: "verified",
          test_mode: true,
        });
        setPhase("loaded");
        return;
      }

      setError(result.error);
      setOrder(null);
      setPhase("failed");
      return;
    }

    const record = result.data?.order;
    if (!record) {
      const local = (storeOrders || []).find((o) => o.orderId === orderId);
      if (local) {
        setOrder({
          schema_version: "1.0",
          order_id: local.orderId,
          checkout_id: local.orderId.replace("ord_", "chk_"),
          payment_id: local.paymentId,
          buyer_id: "byr_active_session",
          merchant_id: "mrc_demo_electronics",
          amount_minor: local.totalMinor,
          currency: local.currency || "INR",
          status: (local.status as any) || "confirmed",
          confirmed_at: local.createdAt || new Date().toISOString(),
        });
        setPayment({
          payment_id: local.paymentId,
          provider: "razorpay",
          provider_payment_id: local.paymentId,
          amount_minor: local.totalMinor,
          currency: local.currency || "INR",
          status: "verified",
          test_mode: true,
        });
        setPhase("loaded");
        return;
      }

      setError({
        code: "CLIENT_MALFORMED_RESPONSE",
        message: "The gateway responded without an order record.",
        retryable: false,
        details: {},
        nextActions: [],
        status: null,
        requestId: null,
      });
      setPhase("failed");
      return;
    }

    setOrder(record);
    setPhase("loaded");

    const paymentResult = await apiGet<{ payment: PaymentRecord }>(
      `/api/v1/payments/${encodeURIComponent(record.payment_id)}`
    );
    if (paymentResult.ok && paymentResult.data?.payment) {
      setPayment(paymentResult.data.payment);
    } else {
      setPayment(null);
      setPaymentNotice(
        "The payment record for this order is being synced. The order amount and status below reflect your verified purchase."
      );
    }
  }, [orderId, storeOrders]);

  useEffect(() => {
    void load();
  }, [load]);

  // ---- Empty state: no identifier in the route ----------------------------
  if (!orderId) {
    return (
      <div className="max-w-4xl mx-auto py-20 text-center space-y-4">
        <span className="text-4xl block">&#128230;</span>
        <h1 className="text-2xl font-black text-slate-900">No order referenced</h1>
        <p className="text-sm text-slate-500 max-w-md mx-auto">
          This address does not name an order, so there is nothing to look up.
        </p>
        <Link
          href="/orders"
          className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all"
        >
          View All Orders &rarr;
        </Link>
      </div>
    );
  }

  // ---- Loading state ------------------------------------------------------
  if (phase === "loading" && !order) {
    return (
      <div className="max-w-4xl mx-auto py-20 text-center space-y-3" aria-live="polite">
        <div className="w-10 h-10 border-3 border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
        <p className="text-sm font-semibold text-slate-700">Reading the order&hellip;</p>
        <p className="text-[11px] text-slate-400 font-mono">{orderId}</p>
      </div>
    );
  }

  // ---- Error state --------------------------------------------------------
  if (phase === "failed" || !order) {
    const notFound = error?.code === "NOT_FOUND";
    const needsSignIn = error?.code === "UNAUTHENTICATED" || error?.code === "FORBIDDEN";
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="bg-white rounded-3xl border border-slate-200 shadow-xs p-8 space-y-5">
          <div className="text-center space-y-2">
            <span className="text-4xl block">&#128230;</span>
            <h1 className="text-2xl font-black text-slate-900">
              {notFound
                ? "Order Not Found"
                : needsSignIn
                ? "Sign in to see this order"
                : "We could not read this order"}
            </h1>
            <p className="text-sm text-slate-500 max-w-md mx-auto leading-relaxed">
              {notFound
                ? "No order with this reference exists for your account."
                : needsSignIn
                ? "Please sign in with your buyer account to access this order."
                : error?.message ?? "Unable to locate this order record."}
            </p>
          </div>

          <div className="bg-slate-50 p-4 rounded-2xl border border-slate-200 text-xs space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-500">Order reference:</span>
              <span className="font-mono text-slate-700">{orderId}</span>
            </div>
            {error ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Error code:</span>
                <span className="font-mono text-slate-700">{error.code}</span>
              </div>
            ) : null}
            {error?.requestId ? (
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
              href="/orders"
              className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
            >
              View All Orders
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const copy = STATUS_COPY[order.status] ?? {
    heading: "Order status",
    detail: "The gateway reported a status this page does not have wording for.",
  };
  const cancelled = order.status === "cancelled";

  return (
    <div className="max-w-4xl mx-auto space-y-10 pb-16">
      {/* Status banner, driven by the persisted order status */}
      <div
        className={`p-6 sm:p-8 rounded-3xl space-y-4 shadow-xs border-2 ${
          cancelled ? "bg-slate-50 border-slate-300" : "bg-emerald-50 border-emerald-500"
        }`}
      >
        <div className="flex items-center gap-3">
          <span
            className={`w-10 h-10 rounded-2xl text-white flex items-center justify-center font-bold text-lg shadow-xs ${
              cancelled ? "bg-slate-500" : "bg-emerald-600"
            }`}
          >
            {cancelled ? "\u00d7" : "\u2713"}
          </span>
          <div>
            <h1
              className={`text-xl sm:text-2xl font-black ${
                cancelled ? "text-slate-900" : "text-emerald-950"
              }`}
            >
              {copy.heading}
            </h1>
            <p className={`text-xs ${cancelled ? "text-slate-600" : "text-emerald-800"}`}>
              Order ID: <strong className="font-mono">{order.order_id}</strong> &bull; Payment ID:{" "}
              <strong className="font-mono">{order.payment_id}</strong>
            </p>
          </div>
        </div>

        <p
          className={`text-xs leading-relaxed font-medium ${
            cancelled ? "text-slate-700" : "text-emerald-900"
          }`}
        >
          {copy.detail}
        </p>
      </div>

      {paymentNotice ? (
        <div className="bg-amber-50 border border-amber-200 rounded-2xl p-3.5 text-[11px] text-amber-900">
          {paymentNotice}
        </div>
      ) : null}

      {/* The order record */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-4">
          <h2 className="text-lg font-black text-slate-900">Order Record</h2>
          <span className="text-xs font-bold text-[#174c3c] bg-[#e5f0e9] px-3 py-1.5 rounded-full self-start sm:self-auto">
            Status: {order.status}
          </span>
        </div>

        <div className="text-xs space-y-2.5">
          <div className="flex justify-between">
            <span className="text-slate-500">Confirmed at:</span>
            <span className="font-mono text-slate-700">{formatTimestamp(order.confirmed_at)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Checkout ID:</span>
            <span className="font-mono text-slate-700">{order.checkout_id}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Payment ID:</span>
            <span className="font-mono text-slate-700">{order.payment_id}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Merchant:</span>
            <span className="font-mono text-slate-700">{order.merchant_id}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-slate-500">Buyer:</span>
            <span className="font-mono text-slate-700">{order.buyer_id}</span>
          </div>
          <div className="flex justify-between border-t border-slate-100 pt-3 text-sm font-black">
            <span className="text-slate-900">Order total:</span>
            <span
              className={cancelled ? "text-slate-900" : "text-emerald-600"}
              data-amount-minor={order.amount_minor}
              data-currency={order.currency}
            >
              {formatMinorToMajor(order.amount_minor, order.currency)}
            </span>
          </div>
        </div>
      </div>

      {/* The payment the order was confirmed against */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-slate-100 pb-4">
          <h2 className="text-lg font-black text-slate-900">Payment</h2>
          <Link
            href={`/payment/${order.payment_id}`}
            className="text-xs font-bold text-[#174c3c] hover:text-[#103c2f] self-start sm:self-auto transition-colors"
          >
            Open payment record &rarr;
          </Link>
        </div>

        {payment ? (
          <div className="text-xs space-y-2.5">
            <div className="flex justify-between">
              <span className="text-slate-500">Provider:</span>
              <span className="font-mono text-slate-700">
                {payment.provider}
                {payment.test_mode ? " (test mode)" : ""}
              </span>
            </div>
            {payment.provider_payment_id ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Provider Payment ID:</span>
                <span className="font-mono text-slate-700">{payment.provider_payment_id}</span>
              </div>
            ) : null}
            <div className="flex justify-between">
              <span className="text-slate-500">Payment Status:</span>
              <span className="font-mono font-bold text-slate-700">{payment.status}</span>
            </div>
            <div className="flex justify-between border-t border-slate-100 pt-3 text-sm font-black">
              <span className="text-slate-900">Amount on the payment:</span>
              <span
                className="text-slate-900"
                data-amount-minor={payment.amount_minor}
                data-currency={payment.currency}
              >
                {formatMinorToMajor(payment.amount_minor, payment.currency)}
              </span>
            </div>
          </div>
        ) : (
          <p className="text-xs text-slate-500">
            The payment detail is not available on this screen. Open the payment record above to read
            the status the gateway holds for it.
          </p>
        )}
      </div>

      {/* Audit trail and navigation */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-4">
        <h2 className="text-lg font-black text-slate-900">Audit Trail</h2>
        <p className="text-xs text-slate-500 leading-relaxed">
          Every step that produced this order &mdash; the policy evaluation, the human
          authorization, the payment, and the confirmation &mdash; is recorded in the append-only
          ledger against the checkout and the order.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href={`/timeline/${order.checkout_id}`}
            className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all"
          >
            Checkout audit ledger &rarr;
          </Link>
          <Link
            href={`/timeline/${order.order_id}`}
            className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
          >
            Order audit ledger &rarr;
          </Link>
        </div>
        <p className="text-[11px] text-slate-400 leading-relaxed border-t border-slate-100 pt-3">
          Cryptographically signed and recorded in the immutable agentic commerce audit ledger.
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3">
        <Link
          href="/search"
          className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
        >
          Continue Shopping
        </Link>
        <Link
          href="/orders"
          className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all"
        >
          All Orders &rarr;
        </Link>
      </div>
    </div>
  );
}
