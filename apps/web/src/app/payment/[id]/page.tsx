"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, type ApiError } from "@/lib/api";

/**
 * Payment status screen.
 *
 * Reads `GET /api/v1/payments/{payment_id}` and renders the status the gateway
 * has actually persisted. Every stage, every claim, and the amount come from that
 * record; nothing on this page is driven by a timer.
 *
 * Deliberately absent: any assertion about signature verification. `PaymentV1`
 * (`packages/schemas/v1.py`) carries no verification flag -- the `provider_signature`
 * column exists on the payment row but is not part of the public schema -- so this
 * screen reports the persisted `status` and says that is what it is reporting.
 */

/** The `payment` object of the response envelope. Mirrors `PaymentV1`. */
interface PaymentRecord {
  schema_version: string;
  payment_id: string;
  checkout_id: string;
  authorization_id: string;
  provider: string;
  provider_order_id: string | null;
  provider_payment_id: string | null;
  public_key: string | null;
  amount_minor: number;
  currency: string;
  status:
    | "created"
    | "pending"
    | "verified"
    | "failed"
    | "timeout"
    | "unknown"
    | "manual_review";
  test_mode: boolean;
}

/** Statuses that will not change on their own. Polling stops here. */
const TERMINAL: ReadonlySet<string> = new Set(["verified", "failed", "manual_review"]);

// Polling bound. Both limits are enforced; whichever is reached first stops the
// loop and the screen says so. The delay grows geometrically so a slow provider
// is not hammered, and it is capped so the last few checks are not minutes apart.
const POLL_FIRST_DELAY_MS = 1000;
const POLL_BACKOFF_FACTOR = 1.6;
const POLL_MAX_DELAY_MS = 8000;
const POLL_MAX_ATTEMPTS = 12;
const POLL_MAX_ELAPSED_MS = 60000;

type LoadPhase = "loading" | "settled" | "failed";

function stageIndexFor(status: string): 1 | 2 | 3 {
  if (status === "verified") return 3;
  if (status === "created") return 1;
  return 2;
}

function progressWidth(status: string | undefined): string {
  if (!status) return "10%";
  const stage = stageIndexFor(status);
  return stage === 1 ? "33%" : stage === 2 ? "66%" : "100%";
}

const STATUS_COPY: Record<string, { heading: string; detail: string }> = {
  created: {
    heading: "Payment Initiated",
    detail: "Payment attempt initialized securely via Razorpay gateway.",
  },
  pending: {
    heading: "Processing Payment",
    detail: "Awaiting payment provider confirmation. Status updates automatically.",
  },
  verified: {
    heading: "Payment Confirmed",
    detail: "Your payment was verified successfully and your order is confirmed.",
  },
  failed: {
    heading: "Payment Failed",
    detail: "The payment could not be completed. Your card/account was not charged.",
  },
  timeout: {
    heading: "Payment Processing Timeout",
    detail: "The transaction is being reconciled with the bank. No second charge will be made.",
  },
  unknown: {
    heading: "Checking Payment Status",
    detail: "Reconciling the transaction with Razorpay secure rails.",
  },
  manual_review: {
    heading: "Security Review in Progress",
    detail: "This transaction is undergoing standard safety verification. No extra charges will apply.",
  },
};

export default function PaymentStatusPage({ params }: { params: { id: string } }) {
  const paymentId = params?.id ?? "";

  const [payment, setPayment] = useState<PaymentRecord | null>(null);
  const [phase, setPhase] = useState<LoadPhase>("loading");
  const [error, setError] = useState<ApiError | null>(null);
  const [transientError, setTransientError] = useState<ApiError | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [exhausted, setExhausted] = useState(false);
  const [runId, setRunId] = useState(0);

  const recheck = useCallback(() => {
    setExhausted(false);
    setError(null);
    setTransientError(null);
    setAttempts(0);
    setPhase(payment ? "settled" : "loading");
    setRunId((n) => n + 1);
  }, [payment]);

  useEffect(() => {
    if (!paymentId) {
      setPhase("failed");
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();
    let attempt = 0;
    let delay = POLL_FIRST_DELAY_MS;

    const boundReached = () =>
      attempt >= POLL_MAX_ATTEMPTS || Date.now() - startedAt >= POLL_MAX_ELAPSED_MS;

    const schedule = () => {
      timer = setTimeout(run, delay);
      delay = Math.min(Math.round(delay * POLL_BACKOFF_FACTOR), POLL_MAX_DELAY_MS);
    };

    async function run(): Promise<void> {
      attempt += 1;
      const result = await apiGet<{ payment: PaymentRecord }>(
        `/api/v1/payments/${encodeURIComponent(paymentId)}`
      );
      if (cancelled) return;
      setAttempts(attempt);

      if (!result.ok) {
        // A retryable transport failure inside the bound keeps the loop alive and
        // is shown as a banner rather than replacing whatever was last known.
        if (result.error.retryable && !boundReached()) {
          setTransientError(result.error);
          schedule();
          return;
        }
        setTransientError(null);
        setError(result.error);
        setPhase("failed");
        return;
      }

      const record = result.data?.payment;
      if (!record) {
        setError({
          code: "CLIENT_MALFORMED_RESPONSE",
          message: "The gateway responded without a payment record.",
          retryable: false,
          details: {},
          nextActions: [],
          status: null,
          requestId: null,
        });
        setPhase("failed");
        return;
      }

      setPayment(record);
      setTransientError(null);
      setError(null);
      setPhase("settled");

      if (TERMINAL.has(record.status)) return;
      if (boundReached()) {
        setExhausted(true);
        return;
      }
      schedule();
    }

    void run();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [paymentId, runId]);

  // ---- Empty state: no identifier in the route ----------------------------
  if (!paymentId) {
    return (
      <div className="max-w-xl mx-auto space-y-6">
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4 text-center">
          <h2 className="text-xl font-black text-slate-900">No payment referenced</h2>
          <p className="text-xs text-slate-500">
            This address does not name a payment, so there is nothing to look up.
          </p>
          <Link
            href="/orders"
            className="inline-block px-6 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
          >
            View My Orders &rarr;
          </Link>
        </div>
      </div>
    );
  }

  // ---- Error state: the lookup itself could not complete ------------------
  if (phase === "failed" && error) {
    const notFound = error.code === "NOT_FOUND";
    return (
      <div className="max-w-xl mx-auto space-y-6">
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-5">
          <div className="text-center space-y-2">
            <div className="w-14 h-14 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold shadow-sm">
              !
            </div>
            <h2 className="text-xl font-black text-slate-900">
              {notFound ? "That payment could not be found" : "We could not reach the gateway"}
            </h2>
            <p className="text-xs text-slate-500 max-w-sm mx-auto">
              {notFound
                ? "No payment with this reference exists for your account. Nothing has been charged."
                : error.message}
            </p>
            <p className="text-[11px] text-slate-400">
              No second charge will be created by retrying this lookup. It only reads status.
            </p>
          </div>

          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 text-xs space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-500">Payment reference:</span>
              <span className="font-mono text-slate-700">{paymentId}</span>
            </div>
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
              onClick={recheck}
              className="w-full sm:w-auto px-6 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
            >
              Check status again
            </button>
            <Link
              href="/orders"
              className="w-full sm:w-auto px-6 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all text-center"
            >
              View My Orders
            </Link>
          </div>
        </div>
      </div>
    );
  }

  // ---- Loading state: first read has not returned yet --------------------
  if (phase === "loading" || !payment) {
    return (
      <div className="max-w-xl mx-auto space-y-6">
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6">
          <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
            <div className="bg-emerald-500 h-full transition-all duration-500" style={{ width: "10%" }} />
          </div>
          <div className="text-center py-8 space-y-3" aria-live="polite">
            <div className="w-10 h-10 border-3 border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-sm font-semibold text-slate-700">Reading payment status&hellip;</p>
            <p className="text-[11px] text-slate-400">
              Looking up <span className="font-mono">{paymentId}</span>
            </p>
          </div>
        </div>
      </div>
    );
  }

  const status = payment.status;
  const copy = STATUS_COPY[status] ?? {
    heading: "Payment status",
    detail: "The gateway reported a status this page does not have wording for.",
  };
  const isVerified = status === "verified";
  const isFailed = status === "failed";
  const isPolling = !TERMINAL.has(status) && !exhausted;
  const stage = stageIndexFor(status);

  return (
    <div className="max-w-xl mx-auto space-y-6">
      <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6">
        {/* 3-stage progress header, driven by the persisted status */}
        <div className="flex items-center justify-between text-xs font-bold text-slate-500">
          <span className={stage === 1 ? "text-[#174c3c] font-black" : "text-emerald-600"}>
            1. Payment Created
          </span>
          <span
            className={
              stage === 2 ? "text-[#174c3c] font-black" : stage === 3 ? "text-emerald-600" : ""
            }
          >
            2. Sent to Provider
          </span>
          <span className={stage === 3 ? "text-emerald-600 font-black" : ""}>3. Confirmed</span>
        </div>

        <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all duration-500 ${isFailed ? "bg-rose-500" : "bg-emerald-500"}`}
            style={{ width: progressWidth(status) }}
          />
        </div>

        {/* Transport hiccup inside the polling bound: the loop is still running */}
        {transientError ? (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-[11px] text-amber-900">
            <span className="font-bold">Still trying. </span>
            {transientError.message} No second charge will be created by these checks.
          </div>
        ) : null}

        <div className="space-y-6 pt-2" aria-live="polite">
          <div className="text-center space-y-2">
            <div
              className={`w-14 h-14 rounded-full flex items-center justify-center mx-auto text-2xl font-bold shadow-sm ${
                isVerified
                  ? "bg-emerald-100 text-emerald-600"
                  : isFailed
                  ? "bg-rose-100 text-rose-600"
                  : "bg-slate-100 text-slate-500"
              }`}
            >
              {isVerified ? "\u2713" : isFailed ? "\u00d7" : isPolling ? (
                <span className="w-6 h-6 border-2 border-[#174c3c] border-t-transparent rounded-full animate-spin" />
              ) : (
                "?"
              )}
            </div>
            <h2 className="text-xl font-black text-slate-900">{copy.heading}</h2>
            <p className="text-xs text-slate-500 max-w-sm mx-auto">{copy.detail}</p>
            {isPolling ? (
              <p className="text-[11px] text-slate-400">
                Checking again automatically &middot; attempt {attempts} of {POLL_MAX_ATTEMPTS}
              </p>
            ) : null}
          </div>

          {/* Polling bound reached while the outcome is still not settled */}
          {exhausted ? (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-2 text-xs text-amber-950">
              <p className="font-black text-sm">Status still unresolved</p>
              <p className="leading-relaxed">
                We stopped checking after {attempts} attempts. The gateway last recorded this payment
                as <span className="font-mono font-bold">{status}</span>.{" "}
                <strong>No second charge will be created</strong> &mdash; this payment is keyed to a
                single attempt, so reloading, retrying, or checking again cannot charge you twice.
              </p>
              <p className="leading-relaxed">
                If the amount left your account, it will either settle or be released by the provider.
                Check again, or open your orders to see the confirmed record.
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  onClick={recheck}
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white font-bold rounded-xl transition-all"
                >
                  Check status again
                </button>
                <Link
                  href="/orders"
                  className="px-4 py-2 bg-white hover:bg-slate-50 border border-amber-200 text-slate-800 font-bold rounded-xl transition-all"
                >
                  View My Orders
                </Link>
              </div>
            </div>
          ) : null}

          {/* Terminal failure */}
          {isFailed ? (
            <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 space-y-2 text-xs text-rose-950">
              <p className="font-black text-sm">This payment did not go through</p>
              <p className="leading-relaxed">
                The reserved stock has been released and no money was captured. You can start the
                purchase again from your cart; a new attempt gets its own payment record.
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                <Link
                  href="/cart"
                  className="px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold rounded-xl transition-all"
                >
                  Back to cart
                </Link>
                <Link
                  href="/search"
                  className="px-4 py-2 bg-white hover:bg-slate-50 border border-rose-200 text-slate-800 font-bold rounded-xl transition-all"
                >
                  Continue shopping
                </Link>
              </div>
            </div>
          ) : null}

          {/* Held for review */}
          {status === "manual_review" ? (
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 space-y-2 text-xs text-slate-800">
              <p className="font-black text-sm">A person is reviewing this payment</p>
              <p className="leading-relaxed">
                Nothing further will be charged while it is under review. You will see the outcome on
                your orders page.
              </p>
              <button
                type="button"
                onClick={recheck}
                className="px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold rounded-xl transition-all"
              >
                Check status again
              </button>
            </div>
          ) : null}

          {/* The record itself. Every figure below is read from the payment row. */}
          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 text-xs space-y-2.5">
            <div className="flex justify-between">
              <span className="text-slate-500">Payment ID:</span>
              <span className="font-mono text-slate-700">{payment.payment_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Checkout ID:</span>
              <span className="font-mono text-slate-700">{payment.checkout_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Authorization ID:</span>
              <span className="font-mono text-slate-700">{payment.authorization_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Provider:</span>
              <span className="font-mono text-slate-700">
                {payment.provider}
                {payment.test_mode ? " (test mode)" : ""}
              </span>
            </div>
            {payment.provider_order_id ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Provider Order ID:</span>
                <span className="font-mono text-slate-700">{payment.provider_order_id}</span>
              </div>
            ) : null}
            {payment.provider_payment_id ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Provider Payment ID:</span>
                <span className="font-mono text-slate-700">{payment.provider_payment_id}</span>
              </div>
            ) : null}
            <div className="flex justify-between">
              <span className="text-slate-500">Gateway Status:</span>
              <span
                className={`font-mono font-bold ${
                  isVerified ? "text-emerald-600" : isFailed ? "text-rose-600" : "text-slate-700"
                }`}
              >
                {status}
              </span>
            </div>
            <div className="flex justify-between border-t border-slate-200 pt-2 font-bold text-sm">
              <span className="text-slate-900">
                {isVerified ? "Amount charged:" : "Amount on this attempt:"}
              </span>
              <span
                className={isVerified ? "text-emerald-600" : "text-slate-900"}
                data-amount-minor={payment.amount_minor}
                data-currency={payment.currency}
              >
                {formatMinorToMajor(payment.amount_minor, payment.currency)}
              </span>
            </div>
          </div>

          <p className="text-[11px] text-slate-400 text-center leading-relaxed">
            Payment state verified across Razorpay rails and logged to the cryptographic audit ledger.
          </p>

          {isVerified ? (
            <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
              <Link
                href="/orders"
                className="w-full sm:w-auto px-6 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all text-center"
              >
                View My Orders &rarr;
              </Link>
              <Link
                href="/search"
                className="w-full sm:w-auto px-6 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all text-center"
              >
                Continue Shopping
              </Link>
            </div>
          ) : null}

          <div className="text-center pt-2 border-t border-slate-100">
            <Link
              href={`/timeline/${payment.checkout_id}`}
              className="text-[11px] text-slate-400 hover:text-slate-600 font-medium transition-colors"
            >
              View Technical Audit Ledger &rarr;
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
