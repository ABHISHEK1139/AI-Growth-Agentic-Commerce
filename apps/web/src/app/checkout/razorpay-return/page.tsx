"use client";

import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { apiPost, type ApiError } from "@/lib/api";
import { useStore } from "@/context/StoreContext";

/**
 * Razorpay redirect return page.
 *
 * Razorpay's Standard Checkout redirects here after the buyer completes or abandons
 * payment. The redirect URL from the gateway includes these query parameters:
 *
 *   - `razorpay_order_id`  — the backend order ID
 *   - `razorpay_payment_id` — the Razorpay payment ID (absent if buyer abandoned)
 *   - `razorpay_signature`  — HMAC signature (absent if abandoned)
 *   - `checkout_id`         — the gateway checkout ID
 *   - `status`              — "success" or "failed"
 *
 * This page calls `POST /api/v1/payments/razorpay/verify-signature` to confirm a
 * successful payment with the backend, then shows the appropriate outcome screen.
 * If the buyer abandoned, the page shows a friendly retry prompt.
 */

interface VerifyResult {
  verified: boolean;
  order_id: string;
  payment_id: string;
  confirmed_order_id: string;
  status: string;
  amount_minor?: number;
  currency?: string;
}

type Phase = "verifying" | "success" | "failed" | "error";

export default function RazorpayReturnPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-slate-50">
          <div className="text-center space-y-4">
            <div className="text-5xl">⏳</div>
            <h1 className="text-2xl font-black text-slate-900">Confirming your payment…</h1>
            <p className="text-sm text-slate-500">Verifying with the gateway, please wait.</p>
          </div>
        </div>
      }
    >
      <RazorpayReturnBody />
    </Suspense>
  );
}

function RazorpayReturnBody() {
  const searchParams = useSearchParams();
  const { cart, placeOrder, clearCart } = useStore();
  const [phase, setPhase] = useState<Phase>("verifying");
  const [error, setError] = useState<string | null>(null);
  const [orderId, setOrderId] = useState<string | null>(null);
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [confirmedOrderId, setConfirmedOrderId] = useState<string | null>(null);
  // Verify exactly once: without this guard the cart-clear below re-triggers
  // the callback (cart is a dependency) and the verification POST fires twice.
  const verifiedOnceRef = useRef(false);

  const razorpayOrderId = searchParams.get("razorpay_order_id");
  const razorpayPaymentId = searchParams.get("razorpay_payment_id");
  const razorpaySignature = searchParams.get("razorpay_signature");
  const checkoutId = searchParams.get("checkout_id");
  const status = searchParams.get("status");

  const verifyAndConfirm = useCallback(async () => {
    if (verifiedOnceRef.current) return;
    verifiedOnceRef.current = true;

    // Buyer abandoned — show retry screen
    if (!razorpayPaymentId || status === "failed") {
      setPhase("failed");
      return;
    }

    if (!razorpayOrderId) {
      setError("Return URL is missing required parameters. Please contact support.");
      setPhase("error");
      return;
    }

    // A return URL without a signature proves nothing. Refuse to verify
    // rather than letting the backend decide what an empty string means.
    if (!razorpaySignature) {
      setPhase("failed");
      return;
    }

    try {
      const res = await apiPost<VerifyResult>(
        "/api/v1/payments/razorpay/verify-signature",
        {
          razorpay_order_id: razorpayOrderId,
          razorpay_payment_id: razorpayPaymentId,
          razorpay_signature: razorpaySignature,
          checkout_id: checkoutId || undefined,
        }
      );

      if (res.ok && res.data) {
        const { order_id, payment_id, confirmed_order_id, status: verifiedStatus, verified } = res.data;
        setOrderId(order_id);
        setPaymentId(payment_id);
        setConfirmedOrderId(confirmed_order_id);
        const localTotal = cart.reduce((acc, i) => acc + i.product.priceMinor * i.quantity, 0);
        if (typeof res.data.amount_minor === "number" && res.data.amount_minor !== localTotal) {
          setError(
            `The confirmed amount does not match your cart total. No local order was recorded — please contact support.`
          );
          setPhase("error");
          return;
        }
        const isSuccess = verifiedStatus === "paid" || verifiedStatus === "confirmed" || verified;
        if (isSuccess) {
          if (confirmed_order_id && cart.length > 0) {
            placeOrder({
              orderId: confirmed_order_id,
              paymentId: payment_id || `pay_${Date.now().toString(36)}`,
              items: cart,
              totalMinor: cart.reduce((acc, i) => acc + i.product.priceMinor * i.quantity, 0),
              currency: cart[0]?.product.currency || "INR",
              policySummary: "Approved and settled via Razorpay redirect verification",
            });
          }
          clearCart();
        }
        setPhase(isSuccess ? "success" : "failed");
      } else {
        setError(!res.ok ? (res.error.message || "Payment verification failed.") : "Payment verification failed.");
        setPhase("error");
      }
    } catch (e: any) {
      setError(e?.message || "An unexpected error occurred during verification.");
      setPhase("error");
    }
  }, [razorpayOrderId, razorpayPaymentId, razorpaySignature, status, checkoutId, cart, placeOrder, clearCart]);

  useEffect(() => {
    verifyAndConfirm();
  }, [verifyAndConfirm]);

  if (phase === "verifying") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center space-y-4">
          <div className="text-5xl">⏳</div>
          <h1 className="text-2xl font-black text-slate-900">Confirming your payment…</h1>
          <p className="text-sm text-slate-500">Verifying with the gateway, please wait.</p>
        </div>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="max-w-md w-full mx-auto p-6">
          <div className="bg-white rounded-2xl shadow-sm border border-red-100 p-8 text-center space-y-4">
            <div className="text-5xl">⚠️</div>
            <h1 className="text-2xl font-black text-slate-900">Verification failed</h1>
            <p className="text-sm text-slate-600">{error}</p>
            <div className="space-y-2">
              {checkoutId && (
                <p className="text-xs text-slate-400">
                  Checkout ID: {checkoutId}
                </p>
              )}
              {orderId && (
                <p className="text-xs text-slate-400">
                  Order ID: {orderId}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-2 pt-2">
              <Link
                href="/checkout"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#174c3c] text-white font-bold text-sm px-6 hover:bg-[#103c2f] transition-colors"
              >
                Try again
              </Link>
              <Link
                href="/search"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-slate-200 text-slate-600 font-bold text-sm px-6 hover:bg-slate-50 transition-colors"
              >
                Continue shopping
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (phase === "failed") {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="max-w-md w-full mx-auto p-6">
          <div className="bg-white rounded-2xl shadow-sm border border-amber-100 p-8 text-center space-y-4">
            <div className="text-5xl">😕</div>
            <h1 className="text-2xl font-black text-slate-900">Payment not completed</h1>
            <p className="text-sm text-slate-600">
              It looks like you didn&apos;t complete the payment. No charges have been made.
            </p>
            <div className="space-y-2">
              {checkoutId && (
                <p className="text-xs text-slate-400">
                  Checkout ID: {checkoutId}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-2 pt-2">
              <Link
                href="/checkout"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#174c3c] text-white font-bold text-sm px-6 hover:bg-[#103c2f] transition-colors"
              >
                Try payment again
              </Link>
              <Link
                href="/search"
                className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-slate-200 text-slate-600 font-bold text-sm px-6 hover:bg-slate-50 transition-colors"
              >
                Continue shopping
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // phase === "success"
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <div className="max-w-md w-full mx-auto p-6">
        <div className="bg-white rounded-2xl shadow-sm border border-emerald-100 p-8 text-center space-y-4">
          <div className="text-5xl">✅</div>
          <h1 className="text-2xl font-black text-emerald-700">Payment confirmed!</h1>
          <p className="text-sm text-slate-600">
            Your order has been placed. Thank you for shopping with AgentPay.
          </p>
          <div className="space-y-1 text-xs text-slate-400">
            {confirmedOrderId && (
              <p>Order ID: <span className="font-mono">{confirmedOrderId}</span></p>
            )}
            {paymentId && (
              <p>Payment ID: <span className="font-mono">{paymentId}</span></p>
            )}
            {orderId && (
              <p>Razorpay Order ID: <span className="font-mono">{orderId}</span></p>
            )}
          </div>
          <div className="flex flex-col gap-2 pt-2">
            <Link
              href={confirmedOrderId ? `/orders/${confirmedOrderId}` : "/orders"}
              className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#174c3c] text-white font-bold text-sm px-6 hover:bg-[#103c2f] transition-colors"
            >
              View order
            </Link>
            <Link
              href="/search"
              className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-slate-200 text-slate-600 font-bold text-sm px-6 hover:bg-slate-50 transition-colors"
            >
              Continue shopping
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
