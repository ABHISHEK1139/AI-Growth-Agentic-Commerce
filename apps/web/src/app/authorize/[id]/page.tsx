"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { formatMinorToMajor } from "@/lib/money";
import { calculateRemainingSeconds, formatCountdown, hasExpired, serverOffsetMs } from "@/lib/time";
import { apiGet, apiPost, type ApiError } from "@/lib/api";

/**
 * Human authorization gate.
 *
 * Everything shown is read from the gateway:
 *   GET  /api/v1/authorization/{id}            -> { authorization: AuthorizationV1 }
 *   GET  /api/v1/checkout/{checkout_id}        -> { checkout: CheckoutV1 }   (itemised amounts)
 *   GET  /api/v1/catalog/products/{product_id} -> { product }                (title)
 *   GET  /api/v1/catalog/offers/{offer_id}     -> { offer: OfferV1 }         (delivery, returns)
 *   POST /api/v1/authorization/{id}/approve | /reject
 *
 * The countdown is anchored to the server clock, measured from the `Date` header of
 * the authorization response (see `lib/time.ts`). Expiry is enforced server-side;
 * the countdown only stops this screen from inviting a decision that would be
 * refused.
 */

interface PolicyDecision {
  decision: "ALLOW" | "REQUIRE_APPROVAL" | "BLOCK";
  reason_code: string;
  policy_version: string;
}

interface AuthorizationRecord {
  schema_version: string;
  authorization_id: string;
  buyer_id: string;
  merchant_id: string;
  checkout_id: string;
  amount_ceiling_minor: number;
  currency: string;
  category: string;
  price_hash: string;
  status: "pending" | "approved" | "rejected" | "revoked" | "consumed" | "expired";
  valid_until: string;
  policy: PolicyDecision;
}

interface PriceBreakdown {
  unit_price_minor: number;
  quantity: number;
  subtotal_minor: number;
  shipping_minor: number;
  tax_minor: number;
  discount_minor: number;
  total_minor: number;
  currency: string;
}

interface CheckoutRecord {
  checkout_id: string;
  buyer_id: string;
  merchant_id: string;
  offer_id: string;
  offer_version: number;
  product_id: string;
  status: string;
  pricing: PriceBreakdown;
  price_hash: string;
  expires_at: string;
}

interface OfferRecord {
  offer_id: string;
  delivery_days: number;
  return_period_days: number;
  available_quantity: number;
  expires_at: string;
}

interface ProductRecord {
  product_id: string;
  title: string;
}

const DECISION_LABEL: Record<string, string> = {
  ALLOW: "Allowed by policy",
  REQUIRE_APPROVAL: "Your explicit approval is required",
  BLOCK: "Blocked by policy",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Awaiting your decision",
  approved: "Approved",
  rejected: "Rejected",
  revoked: "Revoked",
  consumed: "Already used for a payment",
  expired: "Expired",
};

export default function AuthorizationScreen({ params }: { params?: { id: string } }) {
  const router = useRouter();
  const routeParams = useParams<{ id: string }>();
  const authorizationId = routeParams?.id || params?.id || "";

  const [auth, setAuth] = useState<AuthorizationRecord | null>(null);
  const [checkout, setCheckout] = useState<CheckoutRecord | null>(null);
  const [offer, setOffer] = useState<OfferRecord | null>(null);
  const [product, setProduct] = useState<ProductRecord | null>(null);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  /** Set when the supplementary catalog/checkout reads failed but the gate is usable. */
  const [detailNotice, setDetailNotice] = useState<string | null>(null);

  const [offsetMs, setOffsetMs] = useState(0);
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);

  const [submitting, setSubmitting] = useState<null | "approve" | "reject">(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [creatingPayment, setCreatingPayment] = useState(false);

  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const load = useCallback(async () => {
    if (!authorizationId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    setDetailNotice(null);

    const authResult = await apiGet<{ authorization: AuthorizationRecord }>(
      `/api/v1/authorization/${encodeURIComponent(authorizationId)}`
    );
    if (cancelledRef.current) return;

    if (!authResult.ok) {
      setLoadError(authResult.error);
      setLoading(false);
      return;
    }

    // Anchor the countdown to the clock that issued this response, not this browser's.
    setOffsetMs(serverOffsetMs(authResult.serverDateMs, Date.now()));

    const record = authResult.data?.authorization;
    if (!record) {
      setLoadError({
        code: "CLIENT_MALFORMED_RESPONSE",
        message: "The gateway responded without an authorization record.",
        retryable: false,
        details: {},
        nextActions: [],
        status: null,
        requestId: null,
      });
      setLoading(false);
      return;
    }
    setAuth(record);

    const checkoutResult = await apiGet<{ checkout: CheckoutRecord }>(
      `/api/v1/checkout/${encodeURIComponent(record.checkout_id)}`
    );
    if (cancelledRef.current) return;

    if (checkoutResult.ok && checkoutResult.data?.checkout) {
      const checkoutRecord = checkoutResult.data.checkout;
      setCheckout(checkoutRecord);

      const [offerResult, productResult] = await Promise.all([
        apiGet<{ offer: OfferRecord }>(
          `/api/v1/catalog/offers/${encodeURIComponent(checkoutRecord.offer_id)}`
        ),
        apiGet<{ product: ProductRecord }>(
          `/api/v1/catalog/products/${encodeURIComponent(checkoutRecord.product_id)}`
        ),
      ]);
      if (cancelledRef.current) return;

      if (offerResult.ok && offerResult.data?.offer) setOffer(offerResult.data.offer);
      if (productResult.ok && productResult.data?.product) setProduct(productResult.data.product);
      if (!offerResult.ok || !productResult.ok) {
        setDetailNotice(
          "Delivery and return details could not be read. The amounts and the policy decision below are still the bound values."
        );
      }
    } else {
      setCheckout(null);
      setDetailNotice(
        "The itemised breakdown for this checkout could not be read, so only the authorised ceiling is shown below."
      );
    }

    setLoading(false);
  }, [authorizationId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Countdown, recomputed from the server-anchored present once a second.
  useEffect(() => {
    if (!auth) {
      setRemainingSeconds(null);
      return;
    }
    const update = () => setRemainingSeconds(calculateRemainingSeconds(auth.valid_until, offsetMs));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [auth, offsetMs]);

  const decide = useCallback(
    async (choice: "approve" | "reject") => {
      if (!auth || submitting) return;
      setSubmitting(choice);
      setActionError(null);

      const result = await apiPost<{ authorization: AuthorizationRecord }>(
        `/api/v1/authorization/${encodeURIComponent(auth.authorization_id)}/${choice}`
      );
      if (cancelledRef.current) return;

      if (!result.ok) {
        setActionError(result.error);
        setSubmitting(null);
        // The gateway is the authority on expiry and on state. Re-read so the
        // screen reflects whatever it now says rather than our stale copy.
        void load();
        return;
      }

      const updated = result.data?.authorization;
      if (updated) setAuth(updated);
      setSubmitting(null);
    },
    [auth, submitting, load]
  );

  const startPayment = useCallback(async () => {
    if (!auth || creatingPayment) return;
    setCreatingPayment(true);
    setActionError(null);

    const result = await apiPost<{ payment: { payment_id: string } }>("/api/v1/payments", {
      checkout_id: auth.checkout_id,
      authorization_id: auth.authorization_id,
    });
    if (cancelledRef.current) return;

    if (!result.ok) {
      setActionError(result.error);
      setCreatingPayment(false);
      return;
    }
    const paymentId = result.data?.payment?.payment_id;
    if (!paymentId) {
      setActionError({
        code: "CLIENT_MALFORMED_RESPONSE",
        message: "The payment was accepted but the gateway did not return its reference.",
        retryable: false,
        details: {},
        nextActions: [],
        status: null,
        requestId: null,
      });
      setCreatingPayment(false);
      return;
    }
    router.push(`/payment/${paymentId}`);
  }, [auth, creatingPayment, router]);

  // ---- Empty state ---------------------------------------------------------
  if (!authorizationId) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 text-center space-y-3">
          <h1 className="text-2xl font-black text-slate-900">No authorization referenced</h1>
          <p className="text-sm text-slate-500">
            This address does not name an authorization, so there is nothing to approve.
          </p>
          <Link
            href="/cart"
            className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl transition-all"
          >
            Back to cart
          </Link>
        </div>
      </div>
    );
  }

  // ---- Loading state ------------------------------------------------------
  if (loading) {
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-10 text-center space-y-3" aria-live="polite">
          <div className="w-10 h-10 border-3 border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm font-semibold text-slate-700">Reading the authorization&hellip;</p>
          <p className="text-[11px] text-slate-400 font-mono">{authorizationId}</p>
        </div>
      </div>
    );
  }

  // ---- Error state --------------------------------------------------------
  if (loadError || !auth) {
    const notFound = loadError?.code === "NOT_FOUND";
    return (
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-5">
          <div className="text-center space-y-2">
            <div className="w-14 h-14 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">
              !
            </div>
            <h1 className="text-2xl font-black text-slate-900">
              {notFound ? "That authorization could not be found" : "We could not load this authorization"}
            </h1>
            <p className="text-sm text-slate-500">
              {notFound
                ? "No authorization with this reference exists for your account. Nothing has been approved and nothing has been charged."
                : loadError?.message ?? "The gateway did not return a usable authorization."}
            </p>
          </div>
          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 text-xs space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-500">Reference:</span>
              <span className="font-mono text-slate-700">{authorizationId}</span>
            </div>
            {loadError ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Error code:</span>
                <span className="font-mono text-slate-700">{loadError.code}</span>
              </div>
            ) : null}
            {loadError?.requestId ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Request ID:</span>
                <span className="font-mono text-slate-700">{loadError.requestId}</span>
              </div>
            ) : null}
          </div>
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <button
              type="button"
              onClick={() => void load()}
              className="px-6 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl transition-all"
            >
              Try again
            </button>
            <Link
              href="/cart"
              className="px-6 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all text-center"
            >
              Back to cart
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const pricing = checkout?.pricing ?? null;
  const currency = pricing?.currency ?? auth.currency;
  const totalMinor = pricing?.total_minor ?? auth.amount_ceiling_minor;
  const quantity = pricing?.quantity ?? null;
  const productTitle = product?.title ?? null;

  const expired = hasExpired(auth.valid_until, offsetMs) || auth.status === "expired";
  const isPending = auth.status === "pending";
  const canDecide = isPending && !expired;
  const busy = submitting !== null;

  const approveLabel = `Approve ${formatMinorToMajor(totalMinor, currency)} to merchant ${auth.merchant_id}${
    productTitle ? ` for ${productTitle}` : ""
  }`;
  const rejectLabel = `Reject ${formatMinorToMajor(totalMinor, currency)} to merchant ${auth.merchant_id}${
    productTitle ? ` for ${productTitle}` : ""
  }`;

  const equalButton =
    "py-3 px-4 rounded-xl border-2 bg-white font-bold text-sm transition-all text-center focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div className="text-center space-y-1">
        <h1 className="text-2xl font-black text-slate-900">Human Purchase Authorization</h1>
        <p className="text-sm text-slate-500">
          These are the values the gateway has bound to this checkout. Approving authorises exactly
          this amount, to this merchant, once.
        </p>
      </div>

      {/* Expiry countdown, anchored to server time */}
      <div
        className={`rounded-xl p-3.5 flex items-center justify-between border ${
          expired ? "bg-rose-50 border-rose-200" : "bg-amber-50 border-amber-200"
        }`}
        aria-live="polite"
      >
        <span
          className={`text-xs font-semibold flex items-center gap-2 ${
            expired ? "text-rose-900" : "text-amber-900"
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full ${expired ? "bg-rose-500" : "bg-amber-500 animate-ping"}`}
          />
          {expired ? "This authorization has expired" : "Price hold expires in:"}
        </span>
        <span
          className={`font-mono font-bold text-sm ${expired ? "text-rose-700" : "text-amber-700"}`}
        >
          {expired ? "00:00" : formatCountdown(remainingSeconds ?? 0)}
        </span>
      </div>

      {detailNotice ? (
        <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 text-[11px] text-slate-600">
          {detailNotice}
        </div>
      ) : null}

      {/* Main authorization card */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-6">
        {/* Merchant and product */}
        <div className="space-y-2 border-b border-slate-100 pb-4">
          <span className="text-xs font-bold text-slate-400 uppercase tracking-wider block">
            Merchant ID: {auth.merchant_id}
          </span>
          <h3 className="text-lg font-bold text-slate-900">
            {productTitle ?? (checkout ? `Product ${checkout.product_id}` : "Product details unavailable")}
          </h3>
          <span className="text-xs text-slate-500">
            {quantity !== null ? `Quantity: ${quantity}` : "Quantity: not available"}
            {" \u00b7 "}
            Category: {auth.category}
          </span>
        </div>

        {/* Policy decision, machine-readable reason code included */}
        <div className="bg-slate-50 p-3.5 rounded-xl border border-slate-200 text-xs space-y-1">
          <div className="flex justify-between font-semibold text-slate-700">
            <span>Policy decision:</span>
            <span className="text-indigo-600">
              {auth.policy.decision}
              <span className="text-slate-500 font-normal">
                {" "}
                &mdash; {DECISION_LABEL[auth.policy.decision] ?? "Recorded decision"}
              </span>
            </span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>Reason code:</span>
            <span className="font-mono">{auth.policy.reason_code}</span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>Policy version:</span>
            <span className="font-mono">{auth.policy.policy_version}</span>
          </div>
          <div className="flex justify-between text-slate-500">
            <span>Authorization status:</span>
            <span className="font-mono">{STATUS_LABEL[auth.status] ?? auth.status}</span>
          </div>
        </div>

        {/* Itemised amounts, from the checkout record */}
        {pricing ? (
          <div className="space-y-2.5 text-sm">
            <div className="flex justify-between text-slate-600">
              <span>
                Unit price {pricing.quantity > 1 ? `\u00d7 ${pricing.quantity}` : ""}:
              </span>
              <span
                className="font-medium text-slate-900"
                data-amount-minor={pricing.unit_price_minor}
                data-currency={pricing.currency}
              >
                {formatMinorToMajor(pricing.unit_price_minor, pricing.currency)}
              </span>
            </div>
            <div className="flex justify-between text-slate-600">
              <span>Item subtotal:</span>
              <span
                className="font-medium text-slate-900"
                data-amount-minor={pricing.subtotal_minor}
                data-currency={pricing.currency}
              >
                {formatMinorToMajor(pricing.subtotal_minor, pricing.currency)}
              </span>
            </div>
            <div className="flex justify-between text-slate-600">
              <span>Shipping &amp; handling:</span>
              <span
                className="font-medium text-slate-900"
                data-amount-minor={pricing.shipping_minor}
                data-currency={pricing.currency}
              >
                {formatMinorToMajor(pricing.shipping_minor, pricing.currency)}
              </span>
            </div>
            <div className="flex justify-between text-slate-600">
              <span>Taxes:</span>
              <span
                className="font-medium text-slate-900"
                data-amount-minor={pricing.tax_minor}
                data-currency={pricing.currency}
              >
                {formatMinorToMajor(pricing.tax_minor, pricing.currency)}
              </span>
            </div>
            <div className="flex justify-between text-slate-600">
              <span>Discount:</span>
              <span
                className="font-medium text-slate-900"
                data-amount-minor={pricing.discount_minor}
                data-currency={pricing.currency}
              >
                {formatMinorToMajor(pricing.discount_minor, pricing.currency)}
              </span>
            </div>
            <div className="flex justify-between text-base font-black text-slate-900 pt-3 border-t border-slate-100">
              <span>Total bound amount:</span>
              <span
                className="text-indigo-600"
                data-amount-minor={pricing.total_minor}
                data-currency={pricing.currency}
              >
                {formatMinorToMajor(pricing.total_minor, pricing.currency)}
              </span>
            </div>
          </div>
        ) : (
          <div className="space-y-2.5 text-sm">
            <div className="flex justify-between text-base font-black text-slate-900">
              <span>Authorised ceiling:</span>
              <span
                className="text-indigo-600"
                data-amount-minor={auth.amount_ceiling_minor}
                data-currency={auth.currency}
              >
                {formatMinorToMajor(auth.amount_ceiling_minor, auth.currency)}
              </span>
            </div>
          </div>
        )}

        {/* Ceiling, delivery, returns */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
          <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
            <span className="text-slate-400 font-bold block text-[10px] uppercase">Amount ceiling</span>
            <span
              className="font-bold text-slate-900"
              data-amount-minor={auth.amount_ceiling_minor}
              data-currency={auth.currency}
            >
              {formatMinorToMajor(auth.amount_ceiling_minor, auth.currency)}
            </span>
          </div>
          <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
            <span className="text-slate-400 font-bold block text-[10px] uppercase">Delivery estimate</span>
            <span className="font-bold text-slate-900">
              {offer ? `${offer.delivery_days} day${offer.delivery_days === 1 ? "" : "s"}` : "Not available"}
            </span>
          </div>
          <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
            <span className="text-slate-400 font-bold block text-[10px] uppercase">Return policy</span>
            <span className="font-bold text-slate-900">
              {offer
                ? `${offer.return_period_days} day${offer.return_period_days === 1 ? "" : "s"} to return`
                : "Not available"}
            </span>
          </div>
        </div>

        {/* Price hash the consent is bound to */}
        <div className="bg-slate-50 p-2.5 rounded-lg text-[11px] font-mono text-slate-500 break-all">
          <span className="font-bold text-slate-700">Bound price hash: </span>
          {auth.price_hash}
        </div>

        {actionError ? (
          <div className="bg-rose-50 border border-rose-200 rounded-xl p-3.5 text-xs text-rose-900 space-y-1" aria-live="assertive">
            <p className="font-bold">Your decision was not recorded.</p>
            <p>{actionError.message}</p>
            <p className="font-mono text-[11px] text-rose-700">
              {actionError.code}
              {actionError.retryable ? " \u00b7 safe to try again" : " \u00b7 this will not succeed on retry"}
            </p>
          </div>
        ) : null}

        {/* Expired: approval is withdrawn and revalidation is offered */}
        {expired && isPending ? (
          <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 space-y-2 text-xs text-rose-950">
            <p className="font-black text-sm">The price hold has run out</p>
            <p className="leading-relaxed">
              Approval is disabled because this authorization is no longer valid. Nothing has been
              charged. The gateway enforces this too, so an approval sent now would be refused.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                onClick={() => void load()}
                className="px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold rounded-xl transition-all"
              >
                Revalidate with the gateway
              </button>
              <Link
                href="/cart"
                className="px-4 py-2 bg-white hover:bg-slate-50 border border-rose-200 text-slate-800 font-bold rounded-xl transition-all"
              >
                Start a fresh checkout
              </Link>
            </div>
          </div>
        ) : null}

        {/* The decision. Both controls are the same size, weight, and prominence;
            neither is pre-selected and neither takes focus on mount. */}
        {canDecide ? (
          <div className="grid grid-cols-2 gap-4 pt-2">
            <button
              type="button"
              onClick={() => void decide("reject")}
              disabled={busy}
              aria-label={rejectLabel}
              className={`${equalButton} border-slate-400 text-slate-800 hover:bg-slate-50 focus-visible:ring-slate-400`}
            >
              {submitting === "reject" ? "Recording rejection\u2026" : "Reject purchase"}
            </button>
            <button
              type="button"
              onClick={() => void decide("approve")}
              disabled={busy}
              aria-label={approveLabel}
              className={`${equalButton} border-indigo-600 text-indigo-700 hover:bg-indigo-50 focus-visible:ring-indigo-500`}
            >
              {submitting === "approve" ? "Recording approval\u2026" : "Approve purchase"}
            </button>
          </div>
        ) : null}

        {/* Settled outcomes, read back from the gateway */}
        {auth.status === "approved" ? (
          <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 space-y-2 text-xs text-emerald-950">
            <p className="font-black text-sm">Approved</p>
            <p className="leading-relaxed">
              The gateway has recorded your approval for{" "}
              <span
                className="font-bold"
                data-amount-minor={totalMinor}
                data-currency={currency}
              >
                {formatMinorToMajor(totalMinor, currency)}
              </span>
              . No money has moved yet. Starting the payment is a separate, explicit step.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                onClick={() => void startPayment()}
                disabled={creatingPayment}
                aria-label={`Start payment of ${formatMinorToMajor(totalMinor, currency)} to merchant ${auth.merchant_id}`}
                className="px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold rounded-xl transition-all"
              >
                {creatingPayment ? "Starting payment\u2026" : "Start payment"}
              </button>
              <Link
                href="/cart"
                className="px-4 py-2 bg-white hover:bg-slate-50 border border-emerald-200 text-slate-800 font-bold rounded-xl transition-all"
              >
                Back to cart
              </Link>
            </div>
          </div>
        ) : null}

        {auth.status === "rejected" || auth.status === "revoked" ? (
          <div className="bg-slate-100 border border-slate-200 rounded-xl p-4 space-y-2 text-xs text-slate-800">
            <p className="font-black text-sm">
              {auth.status === "rejected" ? "Rejected" : "Revoked"}
            </p>
            <p className="leading-relaxed">
              Nothing was charged and the checkout has been cancelled. Any reserved stock has been
              released.
            </p>
            <Link
              href="/search"
              className="inline-block px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold rounded-xl transition-all"
            >
              Continue shopping
            </Link>
          </div>
        ) : null}

        {auth.status === "consumed" ? (
          <div className="bg-slate-100 border border-slate-200 rounded-xl p-4 space-y-2 text-xs text-slate-800">
            <p className="font-black text-sm">Already used</p>
            <p className="leading-relaxed">
              This authorization has already been spent on a payment. It cannot authorise a second
              charge.
            </p>
            <Link
              href="/orders"
              className="inline-block px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold rounded-xl transition-all"
            >
              View My Orders
            </Link>
          </div>
        ) : null}
      </div>
    </div>
  );
}
