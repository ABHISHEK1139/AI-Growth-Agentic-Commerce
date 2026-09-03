"use client";

import React, { useState } from "react";
import Script from "next/script";
import { resolveApiUrl } from "@/lib/api";

declare global {
  interface Window {
    Razorpay: any;
  }
}

export default function RazorpayStandardCheckoutPage() {
  const [amountInr, setAmountInr] = useState(100);
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [paymentResult, setPaymentResult] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The publishable key is configuration only. There is deliberately no fallback:
  // shipping a literal test key means every deployment that forgets to configure
  // one quietly transacts against somebody else's Razorpay account.
  const razorpayKeyId = (process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID || "").trim();
  const providerConfigured = razorpayKeyId.length > 0;

  const handlePayment = async () => {
    if (!providerConfigured) {
      setError(
        "The payment provider is not configured. Set NEXT_PUBLIC_RAZORPAY_KEY_ID before taking a payment."
      );
      return;
    }
    setLoading(true);
    setError(null);
    setStatusMessage("Step 1/3: Creating Razorpay order via backend...");
    setPaymentResult(null);

    try {
      // Step 1: Create Order via Backend API
      const orderRes = await fetch(resolveApiUrl("/api/create-order"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          amount: amountInr * 100, // paise (100 INR = 10,000 paise)
          currency: "INR",
          receipt: `rcpt_${Date.now()}`,
          notes: {
            channel: "AgentPay Standard Web Checkout",
            description: "Agentic Commerce Test Transaction",
          },
        }),
      });

      const orderData = await orderRes.json();
      if (!orderRes.ok || !orderData.data?.order_id) {
        throw new Error(orderData.detail || "Failed to create Razorpay order.");
      }

      const { order_id, amount, currency } = orderData.data;
      setStatusMessage("Step 2/3: Opening Razorpay Checkout Modal...");

      // Step 2: Open Standard Razorpay Checkout Modal
      const options = {
        key: razorpayKeyId,
        amount: amount,
        currency: currency,
        name: "AgentPay Gateway",
        description: "Agentic Commerce Test Transaction",
        order_id: order_id,
        image: "https://razorpay.com/favicon.png",
        prefill: {
          name: "Test AI Buyer",
          email: "buyer@agentpay.dev",
          contact: "9999999999",
        },
        theme: {
          color: "#4f46e5",
        },
        handler: async function (response: {
          razorpay_payment_id: string;
          razorpay_order_id: string;
          razorpay_signature: string;
        }) {
          setStatusMessage("Step 3/3: Verifying HMAC-SHA256 signature...");
          try {
            // Step 3: Verify Payment Signature via Backend
            const verifyRes = await fetch(resolveApiUrl("/api/verify-payment"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              credentials: "include",
              body: JSON.stringify({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              }),
            });

            const verifyData = await verifyRes.json();
            if (verifyRes.ok && verifyData.data?.verified) {
              setPaymentResult({
                status: "SUCCESS",
                paymentId: response.razorpay_payment_id,
                orderId: response.razorpay_order_id,
                signature: response.razorpay_signature,
                amount: amountInr,
              });
              setStatusMessage("Payment successfully completed & verified!");
            } else {
              throw new Error("Signature verification mismatch.");
            }
          } catch (err: any) {
            setError(err.message || "Payment verification failed.");
          } finally {
            setLoading(false);
          }
        },
        modal: {
          ondismiss: function () {
            setStatusMessage(null);
            setLoading(false);
            setError("Checkout modal was closed by user.");
          },
        },
      };

      if (typeof window.Razorpay !== "undefined") {
        const rzp = new window.Razorpay(options);
        rzp.on("payment.failed", function (response: any) {
          setError(`Payment failed: ${response.error?.description || "Unknown error"}`);
          setLoading(false);
        });
        rzp.open();
      } else {
        throw new Error("Razorpay SDK not loaded. Please refresh.");
      }
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred.");
      setLoading(false);
      setStatusMessage(null);
    }
  };

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />

      <div className="max-w-3xl mx-auto space-y-8">
        {/* Header */}
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-xs">
          <div className="inline-flex items-center gap-2 px-2.5 py-1 bg-indigo-50 text-indigo-700 rounded-full text-xs font-bold uppercase tracking-wider mb-2">
            <span>Razorpay Standard Web Checkout</span>
          </div>
          <h1 className="text-2xl font-black text-slate-900">Live Test-Mode Checkout Integration</h1>
          <p className="text-sm text-slate-500 mt-1">
            Complete a test transaction to verify your Razorpay Gateway integration (Step 3 of 4).
          </p>
        </div>

        {/* Provider not configured: say so instead of borrowing a key */}
        {!providerConfigured && (
          <div className="bg-rose-50 border border-rose-200 p-5 rounded-2xl space-y-2 text-xs text-rose-950">
            <p className="font-black text-sm">The payment provider is not configured</p>
            <p className="leading-relaxed">
              No publishable key is set for this deployment, so no payment can be taken here. Set{" "}
              <code className="font-mono bg-white px-1 py-0.5 rounded border border-rose-200">
                NEXT_PUBLIC_RAZORPAY_KEY_ID
              </code>{" "}
              to your own Razorpay key and reload. Nothing on this page will charge anything until
              then.
            </p>
          </div>
        )}

        {/* Test Credentials Helper Card */}
        <div className="bg-amber-50/70 border border-amber-200 p-5 rounded-2xl">
          <div className="flex items-center gap-2 text-amber-800 font-bold text-sm mb-3">
            <span>Official Razorpay Test Payment Credentials</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div className="bg-white p-3 rounded-xl border border-amber-200/80">
              <span className="font-semibold text-slate-500 block">Test Credit Card:</span>
              <span className="font-mono font-bold text-slate-900">4100 2800 0000 1007</span>
              <div className="text-slate-500 mt-1 flex gap-4">
                <span>Expiry: <strong>12/26</strong></span>
                <span>CVV: <strong>123</strong></span>
              </div>
            </div>
            <div className="bg-white p-3 rounded-xl border border-amber-200/80">
              <span className="font-semibold text-slate-500 block">Test UPI ID:</span>
              <span className="font-mono font-bold text-slate-900">test@razorpay</span>
              <div className="text-slate-500 mt-1">
                <span>Auto-approves instantaneously</span>
              </div>
            </div>
          </div>
        </div>

        {/* Order Summary & Checkout Box */}
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-xs space-y-6">
          <h2 className="text-base font-black text-slate-900 border-b border-slate-100 pb-3">
            Transaction Details
          </h2>

          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600">Merchant Account Key:</span>
            <span className="font-mono font-bold text-xs bg-slate-100 px-2.5 py-1 rounded text-slate-800">
              {providerConfigured ? razorpayKeyId : "not configured"}
            </span>
          </div>

          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600">Product / Item:</span>
            <span className="font-bold text-slate-900">AgentPay AI Gateway Test Plan</span>
          </div>

          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600">Amount (INR):</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setAmountInr(1)}
                className={`px-2.5 py-1 rounded text-xs font-bold border ${
                  amountInr === 1 ? "bg-indigo-600 text-white border-indigo-600" : "bg-slate-50 text-slate-700"
                }`}
              >
                ₹1
              </button>
              <button
                type="button"
                onClick={() => setAmountInr(100)}
                className={`px-2.5 py-1 rounded text-xs font-bold border ${
                  amountInr === 100 ? "bg-indigo-600 text-white border-indigo-600" : "bg-slate-50 text-slate-700"
                }`}
              >
                ₹100
              </button>
              <button
                type="button"
                onClick={() => setAmountInr(499)}
                className={`px-2.5 py-1 rounded text-xs font-bold border ${
                  amountInr === 499 ? "bg-indigo-600 text-white border-indigo-600" : "bg-slate-50 text-slate-700"
                }`}
              >
                ₹499
              </button>
            </div>
          </div>

          <div className="pt-4 border-t border-slate-100 flex items-center justify-between">
            <div>
              <span className="text-xs text-slate-400 block uppercase font-bold">Total to Pay</span>
              <span className="text-2xl font-black text-slate-900">₹{amountInr}.00</span>
            </div>

            <button
              onClick={handlePayment}
              disabled={loading || !providerConfigured}
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold rounded-xl shadow-xs transition-all flex items-center gap-2"
            >
              {loading ? (
                <>
                  <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  <span>Processing...</span>
                </>
              ) : (
                <span>Pay with Razorpay &rarr;</span>
              )}
            </button>
          </div>

          {/* Status Message */}
          {statusMessage && (
            <div className="p-3 bg-indigo-50 border border-indigo-100 rounded-xl text-xs text-indigo-800 font-mono">
              {statusMessage}
            </div>
          )}

          {/* Error Message */}
          {error && (
            <div className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-800">
              <strong>Error:</strong> {error}
            </div>
          )}

          {/* Success Payment Result Box */}
          {paymentResult && (
            <div className="p-5 bg-emerald-50 border border-emerald-200 rounded-2xl space-y-3">
              <div className="flex items-center gap-2 text-emerald-800 font-black text-sm">
                <span>Payment Verified Successfully!</span>
              </div>
              <p className="text-xs text-emerald-700">
                The HMAC-SHA256 signature was verified by the backend. You can now click <strong>&ldquo;I have done the transaction &rarr;&rdquo;</strong> on your Razorpay Dashboard!
              </p>
              <div className="space-y-1 font-mono text-xs text-emerald-900 bg-white/80 p-3 rounded-xl border border-emerald-200/60">
                <div>Payment ID: <strong>{paymentResult.paymentId}</strong></div>
                <div>Order ID: <strong>{paymentResult.orderId}</strong></div>
                <div>Amount: <strong>₹{paymentResult.amount}</strong></div>
                <div>Status: <strong>PAID / VERIFIED</strong></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
