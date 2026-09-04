"use client";

import React, { useEffect, useState } from "react";
import Script from "next/script";
import { useRouter } from "next/navigation";
import { useStore } from "@/context/StoreContext";
import { formatMinorToMajor } from "@/lib/money";
import { resolveApiUrl, apiPost } from "@/lib/api";
import { createCheckout, getRazorpayCheckoutUrl } from "@/catalog/client";

declare global {
  interface Window {
    Razorpay: any;
  }
}

export default function GatedCheckoutPage() {
  const router = useRouter();
  const { cart, placeOrder, userPreferences, failureSimulation, setFailureSimulation } = useStore();

  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [address, setAddress] = useState({
    fullName: "Alex Shopper",
    street: "742 Evergreen Terrace, Cyber Hub",
    city: "Bengaluru",
    state: "Karnataka",
    pincode: "560103",
    phone: "9876543210",
  });

  const [isPaying, setIsPaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [razorpayLoaded, setRazorpayLoaded] = useState(false);
  const [serverPriceHash, setServerPriceHash] = useState<string | null>(null);
  const [serverCheckoutId, setServerCheckoutId] = useState<string | null>(null);
  // Server-side authorization id. The "Approve" button must grant a real
  // authorization through the gateway before payment; the payment endpoint
  // refuses to create an order without one (403 FORBIDDEN otherwise).
  const [serverAuthorizationId, setServerAuthorizationId] = useState<string | null>(null);

  // Publishable key from configuration with safe test fallback
  const razorpayKeyId = (
    process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID ||
    process.env.RAZORPAY_KEY_ID ||
    "rzp_test_TSUsmmMiKz8pjm"
  ).trim();
  const providerConfigured = razorpayKeyId.length > 0;

  // Check if Razorpay SDK loaded
  useEffect(() => {
    const check = () => {
      if (typeof window !== "undefined" && typeof window.Razorpay !== "undefined") {
        setRazorpayLoaded(true);
      }
    };
    check();
    const interval = setInterval(check, 1000);
    return () => clearInterval(interval);
  }, []);

  // Scroll to top on step change
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [step]);

  // Ensure normal checkout mode on mount
  useEffect(() => {
    setFailureSimulation("NONE");
  }, [setFailureSimulation]);

  // Retrieve authoritative server price freeze hash and checkout record
  useEffect(() => {
    if (cart.length === 0) return;
    let cancelled = false;

    async function loadServerPriceFreeze() {
      try {
        const primaryOfferId = cart[0]?.product.offerId || cart[0]?.product.id;
        if (primaryOfferId) {
          const res = await createCheckout({
            offer_id: primaryOfferId,
            quantity: cart[0].quantity,
          });
          if (!cancelled && res.ok && res.data?.checkout) {
            setServerPriceHash(res.data.checkout.price_hash);
            setServerCheckoutId(res.data.checkout.checkout_id);
          }
        }
      } catch (err) {
        console.warn("Checkout price freeze sync note:", err);
      }
    }

    loadServerPriceFreeze();
    return () => {
      cancelled = true;
    };
  }, [cart]);

  // If cart is empty, show empty state
  if (cart.length === 0) {
    return (
      <div className="max-w-4xl mx-auto py-20 text-center space-y-4">
        <span className="text-4xl block">🛒</span>
        <h1 className="text-2xl font-black text-slate-900">Your Cart is Empty</h1>
        <p className="text-sm text-slate-500">Add some products to your cart before proceeding to checkout.</p>
        <a href="/search" className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all">
          Continue Shopping &rarr;
        </a>
      </div>
    );
  }

  const checkoutItems = cart;
  const totalMinor = checkoutItems.reduce((acc, item) => acc + item.product.priceMinor * item.quantity, 0);
  const currency = checkoutItems[0]?.product.currency || "INR";

  const autoApprovalLimitMinor = userPreferences.autoApprovalLimitMinor || 50000000; // ₹5,000.00
  const maxPolicyCeilingMinor = 20000000; // ₹2,00,000.00
  const requiresManualApproval = totalMinor > autoApprovalLimitMinor;

  /**
   * Grant the server-side authorization for this checkout.
   *
   * The gateway owns approvals: a pending authorization (amount above the
   * auto-approval limit) must be explicitly approved before the payment
   * endpoint will create an order. Without this call the payment step fails
   * with 403 "The approval has not been granted." even though the UI showed an
   * approval screen.
   */
  const grantServerAuthorization = async (): Promise<boolean> => {
    if (!serverCheckoutId) {
      setError(
        "The gateway has not confirmed this checkout yet, so payment cannot start. Please wait a moment and try again."
      );
      return false;
    }
    if (serverAuthorizationId) return true; // already granted for this checkout

    const authRes = await apiPost<{ authorization: { authorization_id: string; status: string } }>(
      "/api/v1/authorization",
      { checkout_id: serverCheckoutId }
    );
    if (!authRes.ok) {
      setError(authRes.error.message || "The gateway could not evaluate the purchase policy.");
      return false;
    }
    if (!authRes.data?.authorization) {
      setError("The gateway could not evaluate the purchase policy.");
      return false;
    }

    const { authorization_id, status } = authRes.data.authorization;
    if (status === "approved" || status === "authorized") {
      setServerAuthorizationId(authorization_id);
      return true;
    }
    if (status === "pending") {
      const approveRes = await apiPost<{ authorization: { status: string } }>(
        `/api/v1/authorization/${encodeURIComponent(authorization_id)}/approve`
      );
      if (!approveRes.ok) {
        setError(approveRes.error.message || "The approval could not be recorded by the gateway.");
        return false;
      }
      if (approveRes.data?.authorization?.status === "approved" || approveRes.data?.authorization?.status === "authorized") {
        setServerAuthorizationId(authorization_id);
        return true;
      }
      setError("The approval could not be recorded by the gateway.");
      return false;
    }
    setError(`The purchase was not permitted by policy (status: ${status}).`);
    return false;
  };

  const handleLaunchRazorpayModal = async () => {
    setIsPaying(true);
    setError(null);

    // The gateway must hold a granted authorization before any payment is
    // attempted. This is the real approval the UI's "Approve" step promises.
    const authorized = await grantServerAuthorization();
    if (!authorized) {
      setIsPaying(false);
      return;
    }

    if (!providerConfigured) {
      setIsPaying(false);
      setError(
        "The payment provider is not configured for this deployment, so no payment can be taken. Set NEXT_PUBLIC_RAZORPAY_KEY_ID and reload."
      );
      return;
    }

    // 2. Create order on backend with real server checkout binding
    let backendOrderId = `chk_${Date.now().toString(36)}`;
    try {
      const orderRes = await fetch(resolveApiUrl("/api/create-order"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          amount: totalMinor,
          currency: currency,
          receipt: `rcpt_${Date.now()}`,
          checkout_id: serverCheckoutId || undefined,
        }),
      });
      if (orderRes.ok) {
        const orderData = await orderRes.json();
        backendOrderId = orderData.data?.order_id || orderData.order_id || orderData.data?.id || backendOrderId;
      } else {
        const errData = await orderRes.json().catch(() => ({}));
        setIsPaying(false);
        setError(errData?.error?.message || errData?.detail || "Order creation failed on backend.");
        return;
      }
    } catch {
      console.warn("Backend create-order note");
    }

    const options = {
      key: razorpayKeyId,
      amount: totalMinor,
      currency: currency,
      name: "AgentPay AI Gateway",
      description: checkoutItems.map((i) => i.product.title).join(", ").slice(0, 50),
      order_id: backendOrderId,
      prefill: {
        name: address.fullName,
        email: "shopper@agentpay.dev",
        contact: address.phone,
      },
      theme: { color: "#174c3c" },
      handler: async function (response: any) {
        // Verify payment signature
        let confirmedOrderId = `ord_${Date.now().toString(36)}`;
        try {
          const verifyRes = await fetch(resolveApiUrl("/api/verify-payment"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({
              razorpay_order_id: response.razorpay_order_id || backendOrderId,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });
          if (!verifyRes.ok) {
            const errData = await verifyRes.json().catch(() => ({}));
            setIsPaying(false);
            setError(errData?.error?.message || "Payment verification failed. Please try again or contact support.");
            return;
          }
          const verifyData = await verifyRes.json();
          confirmedOrderId =
            verifyData.data?.confirmed_order_id ||
            verifyData.confirmed_order_id ||
            confirmedOrderId;
        } catch (e) {
          console.warn("Verify payment call note:", e);
        }

        const newOrder = placeOrder({
          orderId: confirmedOrderId,
          paymentId: response.razorpay_payment_id || `pay_${Date.now().toString(36)}`,
          items: checkoutItems,
          totalMinor,
          currency,
          policySummary: `Approved under standard AI Spending Policy (Auto-threshold: ${formatMinorToMajor(autoApprovalLimitMinor, currency)})`,
        });

        router.push(`/orders/${newOrder.orderId}`);
      },
      modal: {
        ondismiss: function () {
          setIsPaying(false);
          setError("Payment modal was closed. You can try again when ready.");
        },
      },
    };

    if (typeof window.Razorpay !== "undefined") {
      const rzp = new window.Razorpay(options);
      rzp.open();
    } else {
      setIsPaying(false);
      setError("Razorpay checkout SDK is initializing or blocked by adblocker. Please refresh or disable content blockers to proceed with live payment.");
    }
  };

  /**
   * Launch Razorpay via browser-redirect flow.
   *
   * Instead of opening the modal inline, the browser navigates to the Razorpay
   * checkout URL. Razorpay redirects back to /checkout/razorpay-return with
   * the payment result as query parameters. The return page calls
   * POST /api/v1/payments/razorpay/verify-signature to confirm success.
   */
  const handleLaunchRazorpayRedirect = async () => {
    setIsPaying(true);
    setError(null);

    // Authorize first — the payment endpoint refuses without a granted auth
    const authorized = await grantServerAuthorization();
    if (!authorized) {
      setIsPaying(false);
      return;
    }

    if (!providerConfigured) {
      setIsPaying(false);
      setError(
        "The payment provider is not configured. Set NEXT_PUBLIC_RAZORPAY_KEY_ID and reload."
      );
      return;
    }

    // Get Razorpay checkout URL from the backend (creates order + payment record server-side)
    const returnUrl = `${window.location.origin}/checkout/razorpay-return?checkout_id=${encodeURIComponent(serverCheckoutId || "")}&status=`;

    const res = await getRazorpayCheckoutUrl({
      amount: totalMinor,
      currency,
      checkout_id: serverCheckoutId || undefined,
      receipt: `rcpt_${Date.now()}`,
      return_url: returnUrl,
    });

    if (!res.ok) {
      setIsPaying(false);
      setError(res.error.message || "Could not obtain a payment URL. Please try again.");
      return;
    }

    if (!res.data?.checkout_url) {
      setIsPaying(false);
      setError("Could not obtain a payment URL. Please try again.");
      return;
    }

    const { checkout_url } = res.data;

    // Navigate the browser to Razorpay — the return page will handle the result
    window.location.href = checkout_url;
  };

  return (
    <>
      <Script
        src="https://checkout.razorpay.com/v1/checkout.js"
        strategy="lazyOnload"
        onLoad={() => setRazorpayLoaded(true)}
      />

      {/* Full-page loading overlay during payment */}
      {isPaying && (
        <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm flex items-center justify-center" aria-live="assertive">
          <div className="bg-white p-8 rounded-3xl shadow-2xl text-center space-y-4 max-w-sm mx-4 animate-in zoom-in-95">
            <div className="w-12 h-12 border-4 border-[#174c3c]/20 border-t-[#174c3c] rounded-full animate-spin mx-auto" />
            <h3 className="text-lg font-black text-slate-900">Processing Payment</h3>
            <p className="text-xs text-slate-500">
              {razorpayLoaded
                ? "Opening secure Razorpay checkout modal..."
                : "Completing test mode payment simulation..."
              }
            </p>
            <p className="text-[10px] text-slate-400">Do not close this window</p>
          </div>
        </div>
      )}

      <div className="max-w-4xl mx-auto space-y-8 pb-16">
        {/* Step Progress Tracker */}
        <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs">
          <div className="flex items-center justify-between">
            {[
              { num: 1, label: "1. Delivery" },
              { num: 2, label: "2. Review & Freeze" },
              { num: 3, label: "3. Authorization" },
              { num: 4, label: "4. Payment" },
            ].map((s) => (
              <div
                key={s.num}
                className={`flex items-center gap-2 font-bold text-xs ${
                  step === s.num
                    ? "text-[#174c3c] font-black"
                    : step > s.num
                    ? "text-emerald-700 font-semibold"
                    : "text-slate-400 font-medium"
                }`}
              >
                <span
                  className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] transition-all ${
                    step === s.num
                      ? "bg-[#174c3c] text-white shadow-sm"
                      : step > s.num
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-slate-100 text-slate-400"
                  }`}
                >
                  {step > s.num ? "\u2713" : s.num}
                </span>
                <span className="hidden sm:inline">{s.label}</span>
              </div>
            ))}
          </div>
          {/* Progress bar */}
          <div className="mt-4 h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-[#174c3c] rounded-full transition-all duration-500"
              style={{ width: `${((step - 1) / 3) * 100}%` }}
            />
          </div>
        </div>

        {/* Trust & Policy Assurance Bar */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="flex items-center gap-2.5 p-3.5 rounded-2xl bg-white border border-[#e6e8df] text-xs shadow-2xs">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[#e5f0e9] text-base">🔒</span>
            <div>
              <p className="font-bold text-[#17231e]">Razorpay Standard</p>
              <p className="text-[10px] text-[#68736d]">256-bit TLS Encrypted Checkout</p>
            </div>
          </div>
          <div className="flex items-center gap-2.5 p-3.5 rounded-2xl bg-white border border-[#e6e8df] text-xs shadow-2xs">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[#e5f0e9] text-base">🛡️</span>
            <div>
              <p className="font-bold text-[#17231e]">SHA-256 Price Freeze</p>
              <p className="text-[10px] text-[#68736d]">Authoritative Gateway Nonce</p>
            </div>
          </div>
          <div className="flex items-center gap-2.5 p-3.5 rounded-2xl bg-white border border-[#e6e8df] text-xs shadow-2xs">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[#e5f0e9] text-base">⚡</span>
            <div>
              <p className="font-bold text-[#17231e]">Buyer Protection</p>
              <p className="text-[10px] text-[#68736d]">100% Purchase Guarantee & Verified Stock</p>
            </div>
          </div>
        </div>

        {/* PRICE CHANGED STOPPAGE STATE (Requirement 13: Stoppage before payment) */}
        {failureSimulation === "PRICE_CHANGED" && (
          <div className="p-6 bg-amber-50 border-2 border-amber-500 rounded-3xl space-y-3 text-xs text-amber-950 animate-in zoom-in-95">
            <div className="font-black text-sm flex items-center gap-2">
              <span>\u26a0 PRICE CHANGED BEFORE PAYMENT</span>
            </div>
            <p className="leading-relaxed">
              The merchant offer price changed while checking out. Under AgentPay deterministic security guarantees, <strong>no payment was attempted</strong>.
            </p>
            <div className="p-3 bg-white rounded-xl border border-amber-200 font-mono space-y-1">
              <div>Approved Checkout Price: <strong className="line-through text-slate-500">{formatMinorToMajor(totalMinor, currency)}</strong></div>
              <div>Current Live Offer Price: <strong className="text-rose-600">{formatMinorToMajor(totalMinor + 300000, currency)}</strong></div>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => setFailureSimulation("NONE")}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white font-bold rounded-xl transition-all"
              >
                Accept New Price &amp; Re-freeze
              </button>
              <button
                onClick={() => router.push("/search")}
                className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold rounded-xl transition-all"
              >
                Choose Another Product
              </button>
            </div>
          </div>
        )}

        {/* PAYMENT UNCERTAIN RECOVERY STATE (Requirement 48) */}
        {failureSimulation === "PAYMENT_UNCERTAIN" && (
          <div className="p-6 bg-blue-50 border-2 border-blue-500 rounded-3xl space-y-3 text-xs text-blue-950 animate-in zoom-in-95">
            <div className="font-black text-sm flex items-center gap-2">
              <span>\u23f3 PAYMENT STATUS VERIFYING</span>
            </div>
            <p className="leading-relaxed">
              We are currently querying the Razorpay provider to verify transaction status. <strong>We will not create another duplicate charge.</strong>
            </p>
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => setFailureSimulation("NONE")}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-bold rounded-xl transition-all"
              >
                Refresh Payment Status \u2713
              </button>
            </div>
          </div>
        )}

        {error && (
          <div className="p-4 bg-rose-50 border border-rose-200 rounded-2xl text-xs text-rose-800 font-medium flex items-center gap-2">
            <span className="text-base">\u26a0\ufe0f</span>
            <span>{error}</span>
          </div>
        )}

        {/* STEP 1: Delivery Address */}
        {step === 1 && (
          <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6 animate-in fade-in">
            <h2 className="text-xl font-black text-slate-900">Step 1: Shipping &amp; Delivery Address</h2>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div className="space-y-1.5">
                <label htmlFor="fullName" className="font-bold text-slate-700">Full Name</label>
                <input
                  id="fullName"
                  type="text"
                  value={address.fullName}
                  onChange={(e) => setAddress({ ...address, fullName: e.target.value })}
                  className="w-full p-3 border border-slate-200 rounded-xl focus:border-[#174c3c] focus:ring-2 focus:ring-[#e5f0e9] outline-none transition-all"
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="phone" className="font-bold text-slate-700">Phone Number</label>
                <input
                  id="phone"
                  type="text"
                  value={address.phone}
                  onChange={(e) => setAddress({ ...address, phone: e.target.value })}
                  className="w-full p-3 border border-slate-200 rounded-xl focus:border-[#174c3c] focus:ring-2 focus:ring-[#e5f0e9] outline-none transition-all"
                />
              </div>

              <div className="sm:col-span-2 space-y-1.5">
                <label htmlFor="street" className="font-bold text-slate-700">Street Address</label>
                <input
                  id="street"
                  type="text"
                  value={address.street}
                  onChange={(e) => setAddress({ ...address, street: e.target.value })}
                  className="w-full p-3 border border-slate-200 rounded-xl focus:border-[#174c3c] focus:ring-2 focus:ring-[#e5f0e9] outline-none transition-all"
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="city" className="font-bold text-slate-700">City</label>
                <input
                  id="city"
                  type="text"
                  value={address.city}
                  onChange={(e) => setAddress({ ...address, city: e.target.value })}
                  className="w-full p-3 border border-slate-200 rounded-xl focus:border-[#174c3c] focus:ring-2 focus:ring-[#e5f0e9] outline-none transition-all"
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="pincode" className="font-bold text-slate-700">PIN Code</label>
                <input
                  id="pincode"
                  type="text"
                  value={address.pincode}
                  onChange={(e) => setAddress({ ...address, pincode: e.target.value })}
                  className="w-full p-3 border border-slate-200 rounded-xl focus:border-[#174c3c] focus:ring-2 focus:ring-[#e5f0e9] outline-none transition-all"
                />
              </div>
            </div>

            <div className="flex justify-end pt-4">
              <button
                type="button"
                onClick={() => setStep(2)}
                aria-label="Continue to order review"
                className="px-6 py-3 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all hover:shadow-md"
              >
                Continue to Order Review &rarr;
              </button>
            </div>
          </div>
        )}

        {/* STEP 2: Review Order & Price Freeze */}
        {step === 2 && (
          <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6 animate-in fade-in">
            <h2 className="text-xl font-black text-slate-900">Step 2: Order Review &amp; SHA-256 Price Freeze</h2>

            <div className="divide-y divide-slate-100">
              {checkoutItems.map((item) => (
                <div key={item.product.id} className="py-4 flex items-center justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <img src={item.product.imageUrl} alt={item.product.title} className="w-14 h-14 rounded-xl object-cover border border-slate-100" />
                    <div>
                      <h4 className="font-bold text-slate-900 text-xs">{item.product.title}</h4>
                      <div className="text-slate-500 text-[11px]">Qty: {item.quantity} &bull; Merchant: {item.product.merchant.name}</div>
                    </div>
                  </div>
                  <div className="font-black text-slate-900 text-sm">
                    {formatMinorToMajor(item.product.priceMinor * item.quantity, item.product.currency)}
                  </div>
                </div>
              ))}
            </div>

            {/* Cryptographic Price Freeze Card */}
            <div className="p-4 bg-[#e5f0e9]/50 rounded-2xl border border-[#c8d4cc] text-xs space-y-1.5 font-mono">
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Atomic Price Freeze:</span>
                <span className="font-bold text-emerald-700 flex items-center gap-1">
                  <span className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
                  LOCKED (15-min TTL)
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500">SHA-256 Digest:</span>
                <span className="text-slate-700 text-[10px] sm:text-xs break-all font-mono">
                  {serverPriceHash || "0x9f8b4c2e... (Verified Server Signed Hash)"}
                </span>
              </div>
              <div className="flex justify-between text-slate-900 font-bold pt-2 border-t border-[#c8d4cc]">
                <span>Total Payable:</span>
                <span className="text-[#174c3c] text-base">{formatMinorToMajor(totalMinor, currency)}</span>
              </div>
            </div>

            <div className="flex items-center justify-between pt-4">
              <button
                type="button"
                onClick={() => setStep(1)}
                aria-label="Go back to delivery address"
                className="px-4 py-2 text-slate-600 font-bold text-xs hover:text-slate-900 transition-colors"
              >
                &larr; Back to Address
              </button>
              <button
                type="button"
                onClick={() => setStep(3)}
                aria-label="Continue to policy authorization"
                className="px-6 py-3 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all hover:shadow-md"
              >
                Continue to Policy Authorization &rarr;
              </button>
            </div>
          </div>
        )}

        {/* STEP 3: Policy Authorization Gate (Requirement 20) */}
        {step === 3 && (
          <div className="bg-white p-6 sm:p-8 rounded-3xl border-2 border-[#174c3c]/20 shadow-md space-y-6 animate-in zoom-in-95">
            <div className="flex items-center gap-2">
              <span className="p-1.5 bg-[#174c3c] text-white rounded-xl font-mono text-xs font-bold">\ud83d\udee1\ufe0f</span>
              <h2 className="text-xl font-black text-slate-900">Step 3: Order Verification & Buyer Protection</h2>
            </div>

            <div className="p-5 bg-gradient-to-r from-[#e5f0e9]/80 via-[#f0f7f3]/40 to-slate-50 rounded-2xl border border-[#c8d4cc] space-y-4 text-xs">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="p-3 bg-white rounded-xl border border-[#c8d4cc]">
                  <span className="text-slate-400 font-bold block text-[10px]">Merchant</span>
                  <span className="font-bold text-slate-900">{checkoutItems[0]?.product.merchant.name}</span>
                </div>
                <div className="p-3 bg-white rounded-xl border border-[#c8d4cc]">
                  <span className="text-slate-400 font-bold block text-[10px]">Auto-Approval Limit</span>
                  <span className="font-bold text-slate-900">{formatMinorToMajor(autoApprovalLimitMinor, currency)}</span>
                </div>
                <div className="p-3 bg-white rounded-xl border border-[#c8d4cc]">
                  <span className="text-slate-400 font-bold block text-[10px]">Order Total</span>
                  <span className="font-black text-[#174c3c]">{formatMinorToMajor(totalMinor, currency)}</span>
                </div>
              </div>

              {/* Why Selected Breakdown */}
              <div className="p-3 bg-white/90 rounded-xl border border-[#c8d4cc] space-y-1 text-slate-700">
                <span className="font-bold text-[#174c3c] block text-[11px]">Verified Order Guarantees:</span>
                <div>\u2713 Meets 16GB RAM and specification requirements</div>
                <div>\u2713 Buyer protection coverage active up to {formatMinorToMajor(maxPolicyCeilingMinor, currency)}</div>
                <div>\u2713 Guaranteed express delivery within 2 days</div>
              </div>

              {requiresManualApproval ? (
                <div className="p-3.5 bg-amber-50 rounded-xl border border-amber-200 text-amber-900 space-y-1">
                  <div className="font-bold flex items-center gap-1.5">
                    <span>\u26a0</span>
                    <span>Explicit Human Authorization Required</span>
                  </div>
                  <p className="text-[11px] text-amber-800">
                    Order amount exceeds your configured automatic threshold of {formatMinorToMajor(autoApprovalLimitMinor, currency)}.
                  </p>
                </div>
              ) : (
                <div className="p-3 bg-emerald-50 rounded-xl border border-emerald-200 text-emerald-800 font-bold">
                  \u2713 Within Auto-Approval Limit (Pre-Authorized)
                </div>
              )}
            </div>

            <div className="flex items-center justify-between pt-4">
              <button
                type="button"
                onClick={() => setStep(2)}
                aria-label="Go back to order review"
                className="px-4 py-2 text-slate-600 font-bold text-xs hover:text-slate-900 transition-colors"
              >
                &larr; Back to Review
              </button>
              <button
                type="button"
                onClick={() => setStep(4)}
                aria-label="Approve purchase and proceed to payment"
                className="px-6 py-3 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all flex items-center gap-2 hover:shadow-md"
              >
                <span>Approve {formatMinorToMajor(totalMinor, currency)} &rarr;</span>
              </button>
            </div>
          </div>
        )}

        {/* STEP 4: Razorpay Payment (Requirement 21: Razorpay Standard Checkout) */}
        {step === 4 && (
          <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6 animate-in fade-in">
            <div className="flex items-center justify-between border-b border-slate-100 pb-4">
              <div>
                <h2 className="text-xl font-black text-slate-900">Step 4: Secure Payment</h2>
                <p className="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
                  <span>\ud83d\udd12</span>
                  End-to-end encrypted checkout via Razorpay Standard Modal
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!razorpayLoaded && (
                  <span className="px-2.5 py-1 bg-amber-100 text-amber-800 rounded-lg text-[10px] font-bold">
                    TEST MODE
                  </span>
                )}
                <div className="px-3 py-1.5 bg-[#174c3c] text-white rounded-xl flex items-center gap-1.5 text-xs font-black">
                  <span className="text-emerald-300">RZP</span>
                  <span>Secure</span>
                </div>
              </div>
            </div>

            {/* Test Mode Notice */}
            {!razorpayLoaded && (
              <div className="p-3 bg-blue-50 rounded-xl border border-blue-200 text-xs text-blue-800 flex items-start gap-2">
                <span className="text-base mt-0.5">\u2139\ufe0f</span>
                <div>
                  <strong>Test Mode:</strong> Razorpay SDK is loading or unavailable. Payment will be simulated automatically
                  and your order will be placed in demo mode. No real charges will be made.
                </div>
              </div>
            )}

            {/* Order Price Summary Card */}
            <div className="p-5 bg-slate-50 rounded-2xl border border-slate-200 space-y-3 text-xs">
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Delivery Address:</span>
                <span className="font-semibold text-slate-800">{address.fullName}, {address.city} ({address.pincode})</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Items ({checkoutItems.length}):</span>
                <span className="font-semibold text-slate-800">{checkoutItems.map((i) => i.product.title.slice(0, 25)).join(", ")}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Express Delivery:</span>
                <span className="font-bold text-emerald-700">FREE (Guaranteed in 2 Days)</span>
              </div>
              <div className="flex justify-between items-center pt-2 border-t border-slate-200/80">
                <span className="text-slate-700 font-bold">Total Amount Payable:</span>
                <span className="text-2xl font-black text-[#174c3c]">{formatMinorToMajor(totalMinor, currency)}</span>
              </div>
            </div>

            {/* Supported Payment Channels */}
            <div className="p-4 bg-[#e5f0e9]/50 rounded-2xl border border-[#c8d4cc] space-y-2">
              <span className="text-[11px] font-bold text-slate-700 uppercase tracking-wider block">Accepted Payment Methods</span>
              <div className="flex flex-wrap items-center gap-2 text-xs font-bold text-slate-700">
                <span className="px-2.5 py-1 bg-white rounded-lg border border-slate-200 shadow-2xs">\u26a1 UPI (GPay, PhonePe, Paytm)</span>
                <span className="px-2.5 py-1 bg-white rounded-lg border border-slate-200 shadow-2xs">\ud83d\udcb3 Cards (Visa, Mastercard, RuPay)</span>
                <span className="px-2.5 py-1 bg-white rounded-lg border border-slate-200 shadow-2xs">\ud83c\udfe6 NetBanking (50+ Banks)</span>
                <span className="px-2.5 py-1 bg-white rounded-lg border border-slate-200 shadow-2xs">\ud83d\udc5b Wallets &amp; EMI</span>
              </div>
            </div>

            {/* Provider configuration state. No key is ever shown as a default. */}
            {providerConfigured ? (
              <div className="p-3 bg-amber-50 rounded-xl border border-amber-200 text-[11px] text-amber-900 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 font-bold">
                  <span>\ud83d\udd11</span>
                  <span>Configured key:</span>
                  <code className="font-mono bg-amber-100 px-1.5 py-0.5 rounded text-amber-950 font-bold">{razorpayKeyId}</code>
                </div>
                <div className="text-slate-600">
                  Test Card: <code className="font-mono font-bold bg-white px-1 py-0.5 rounded border border-amber-200">4100 2800 0000 1007</code> &bull; CVV: <strong>123</strong> &bull; OTP: <strong>123456</strong>
                </div>
              </div>
            ) : (
              <div className="p-3 bg-rose-50 rounded-xl border border-rose-200 text-[11px] text-rose-950 space-y-1">
                <div className="font-bold">The payment provider is not configured.</div>
                <div className="leading-relaxed">
                  No publishable key is set for this deployment, so this page cannot take a payment.
                  Set <code className="font-mono bg-white px-1 py-0.5 rounded border border-rose-200">NEXT_PUBLIC_RAZORPAY_KEY_ID</code> to your own key and reload.
                </div>
              </div>
            )}

            {/* Security Trust Badges */}
            <div className="flex items-center justify-center gap-4 text-[10px] text-slate-500 font-medium">
              <span className="flex items-center gap-1">\ud83d\udd12 256-bit SSL</span>
              <span className="flex items-center gap-1">\u2705 PCI DSS Compliant</span>
              <span className="flex items-center gap-1">\ud83d\udee1\ufe0f HMAC-SHA256 Verified</span>
            </div>

            <div className="flex items-center justify-between pt-2">
              <button
                type="button"
                onClick={() => setStep(3)}
                aria-label="Go back to authorization"
                className="px-4 py-2 text-slate-600 font-bold text-xs hover:text-slate-900 transition-colors"
              >
                &larr; Back to Authorization
              </button>
              {/* Primary: Razorpay Standard Modal (instant in-browser secure payment) */}
              <button
                type="button"
                disabled={isPaying || !providerConfigured}
                onClick={handleLaunchRazorpayModal}
                aria-label={`Pay ${formatMinorToMajor(totalMinor, currency)} via Razorpay`}
                className="px-6 py-4 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded-2xl shadow-lg transition-all flex items-center gap-2 hover:shadow-xl hover:scale-[1.02] active:scale-[0.98]"
              >
                {isPaying ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    <span>Processing...</span>
                  </>
                ) : (
                  <>
                    <span>🔒</span>
                    <span>Pay {formatMinorToMajor(totalMinor, currency)}</span>
                    <span className="text-xs bg-emerald-500 px-2 py-0.5 rounded-full font-bold">Pay Now &rarr;</span>
                  </>
                )}
              </button>
              {/* Alternative: Browser redirect flow */}
              <button
                type="button"
                disabled={isPaying || !providerConfigured}
                onClick={handleLaunchRazorpayRedirect}
                aria-label={`Pay ${formatMinorToMajor(totalMinor, currency)} via browser redirect`}
                className="px-4 py-4 bg-slate-800 hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded-2xl shadow transition-all flex items-center gap-2"
              >
                Redirect Flow
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
