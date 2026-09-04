"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  X,
  ShoppingBag,
  Plus,
  Minus,
  Trash2,
  ArrowRight,
  ShieldCheck,
  Truck,
  Sparkles,
} from "lucide-react";
import { useStore } from "@/context/StoreContext";
import { formatMinorToMajor } from "@/lib/money";
import { categoryTitleForSlug } from "@/catalog/present";
import { defaultImageForCategory } from "@/catalog/adapt";

export function CartDrawer() {
  const router = useRouter();
  const {
    cart,
    addToCart,
    isCartDrawerOpen,
    closeCartDrawer,
    updateCartQuantity,
    removeFromCart,
    openAiDrawer,
  } = useStore();

  const drawerRef = useRef<HTMLDivElement>(null);
  const [companion, setCompanion] = useState<any>(null);
  const [companionLoading, setCompanionLoading] = useState(false);

  // Fetch AI cross-sell companion recommendation for latest cart item
  useEffect(() => {
    if (!isCartDrawerOpen || cart.length === 0) {
      setCompanion(null);
      return;
    }

    const latest = cart[cart.length - 1]?.product;
    if (!latest) return;

    let cancelled = false;
    setCompanionLoading(true);

    fetch("/api/v1/recommendations/cross-sell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_product_id: latest.id }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        const recs = data?.data?.recommendations || [];
        const candidate = recs.find(
          (r: any) => !cart.some((ci) => ci.product.id === r.id || ci.product.id === r.product_id)
        );
        setCompanion(candidate || null);
      })
      .catch(() => {
        if (!cancelled) setCompanion(null);
      })
      .finally(() => {
        if (!cancelled) setCompanionLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isCartDrawerOpen, cart]);

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isCartDrawerOpen) {
        closeCartDrawer();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isCartDrawerOpen, closeCartDrawer]);

  // Prevent body scroll when drawer is open
  useEffect(() => {
    if (isCartDrawerOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isCartDrawerOpen]);

  if (!isCartDrawerOpen) return null;

  const totalItems = cart.reduce((sum, item) => sum + item.quantity, 0);
  const subtotalMinor = cart.reduce(
    (sum, item) => sum + item.product.priceMinor * item.quantity,
    0
  );
  const currency = cart[0]?.product.currency || "INR";

  const handleCheckout = () => {
    closeCartDrawer();
    router.push("/checkout");
  };

  const handleViewCart = () => {
    closeCartDrawer();
    router.push("/cart");
  };

  const handleAddCompanion = () => {
    if (!companion) return;
    const companionProduct: any = {
      id: companion.id || companion.product_id,
      slug: companion.id || companion.product_id,
      title: companion.title,
      priceMinor: companion.price_minor,
      originalPriceMinor: companion.original_price_minor || companion.price_minor + (companion.savings_minor || 30000),
      currency: "INR",
      rating: 4.8,
      reviewCount: 320,
      stock: 15,
      deliveryDays: 1,
      returnDays: 14,
      imageUrl: companion.image_url,
      category: companion.category || "accessory",
      categoryLabel: "Accessories",
      brand: "Certified Companion",
      aiBadge: "✦ AI Cross-Sell",
      shortSpecs: companion.compatibility_reason || "Compatible accessory",
      whyFitsYou: {
        summary: companion.compatibility_reason || "Engineered to complement your setup.",
        pros: ["100% verified compatibility", "Exclusive bundle discount applied"],
        warnings: [],
      },
      specsGrouped: {
        performance: { "Type": "Accessory", "Compatibility": "Certified" },
      },
      sentiment: {
        performancePct: 96,
        batteryPct: 92,
        buildQualityPct: 95,
        valuePct: 98,
        customerLikes: ["High quality", "Great bundle price"],
        customerConcerns: [],
      },
      reviews: [],
      qa: [],
      merchant: {
        id: "mer_agentpay_flagship",
        name: "AgentPay Verified",
        verified: true,
        rating: 4.9,
      },
    };
    addToCart(companionProduct, 1, false);
  };

  return (
    <div className="fixed inset-0 z-50 overflow-hidden" aria-labelledby="slide-over-title" role="dialog" aria-modal="true">
      {/* Background backdrop */}
      <div
        className="fixed inset-0 bg-slate-900/50 backdrop-blur-xs transition-opacity duration-300 animate-fade-in"
        onClick={closeCartDrawer}
      />

      <div className="fixed inset-y-0 right-0 max-w-full flex pl-10">
        <div
          ref={drawerRef}
          className="w-screen max-w-md bg-white shadow-2xl flex flex-col transform transition-transform duration-300 ease-in-out animate-slide-in-right"
        >
          {/* Header */}
          <div className="p-5 border-b border-slate-100 flex items-center justify-between bg-[#f7f7f2]">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-xl bg-[#174c3c] text-white flex items-center justify-center shadow-xs">
                <ShoppingBag className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-black text-slate-900">Your Shopping Bag</h2>
                <p className="text-[11px] text-slate-500 font-medium">
                  {totalItems} {totalItems === 1 ? "item" : "items"} &middot; Free express delivery
                </p>
              </div>
            </div>
            <button
              onClick={closeCartDrawer}
              className="w-8 h-8 rounded-full bg-white border border-slate-200 hover:bg-slate-100 text-slate-500 hover:text-slate-800 flex items-center justify-center transition-colors shadow-2xs"
              aria-label="Close cart"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Delivery perk meter */}
          <div className="bg-emerald-50 px-5 py-2.5 border-b border-emerald-100 flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs font-semibold text-emerald-800">
              <Truck className="w-3.5 h-3.5 text-emerald-600" />
              <span>You unlocked <strong>Free 2-Day Express Delivery</strong></span>
            </div>
            <span className="text-[10px] font-bold uppercase bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-md">
              Applied
            </span>
          </div>

          {/* Cart items list */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4 divide-y divide-slate-100">
            {cart.length === 0 ? (
              <div className="py-16 text-center space-y-4">
                <div className="w-14 h-14 rounded-2xl bg-slate-100 text-slate-400 mx-auto flex items-center justify-center">
                  <ShoppingBag className="w-6 h-6" />
                </div>
                <div className="space-y-1">
                  <h3 className="text-sm font-black text-slate-900">Your bag is empty</h3>
                  <p className="text-xs text-slate-500 max-w-xs mx-auto">
                    Explore high-performance laptops, smartphones, headphones and accessories.
                  </p>
                </div>
                <button
                  onClick={() => {
                    closeCartDrawer();
                    router.push("/search");
                  }}
                  className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs transition-all inline-flex items-center gap-1.5"
                >
                  <span>Explore catalog</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              cart.map((item) => (
                <div key={item.product.id} className="pt-4 first:pt-0 flex gap-3.5 items-start">
                  <div className="w-18 h-18 rounded-xl bg-slate-100 overflow-hidden border border-slate-200 shrink-0">
                    <img
                      src={item.product.imageUrl}
                      alt={item.product.title}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).src = defaultImageForCategory(item.product.category, item.product.title, item.product.brand);
                      }}
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <Link
                        href={`/product/${item.product.id}`}
                        onClick={closeCartDrawer}
                        className="text-xs font-bold text-slate-900 line-clamp-2 hover:text-[#174c3c] transition-colors leading-snug"
                      >
                        {item.product.title}
                      </Link>
                      <button
                        onClick={() => removeFromCart(item.product.id)}
                        className="text-slate-400 hover:text-rose-600 p-1 transition-colors shrink-0"
                        title="Remove item"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    <div className="text-[11px] font-medium text-slate-500 mt-0.5">
                      <span className="font-bold text-[#174c3c] uppercase tracking-wider">{item.product.brand}</span>
                      {item.product.category && item.product.category.toLowerCase() !== item.product.brand.toLowerCase() && (
                        <span> &middot; {categoryTitleForSlug(item.product.category)}</span>
                      )}
                    </div>

                    <div className="mt-2.5 flex items-center justify-between">
                      <div className="flex items-center border border-slate-200 rounded-lg bg-slate-50 shadow-2xs">
                        <button
                          onClick={() => updateCartQuantity(item.product.id, Math.max(1, item.quantity - 1))}
                          className="w-7 h-7 flex items-center justify-center text-slate-600 hover:text-slate-900 hover:bg-slate-200/60 rounded-l-lg transition-colors"
                          aria-label="Decrease quantity"
                        >
                          <Minus className="w-3 h-3" />
                        </button>
                        <span className="w-7 text-center font-bold text-xs text-slate-800">
                          {item.quantity}
                        </span>
                        <button
                          onClick={() => updateCartQuantity(item.product.id, item.quantity + 1)}
                          className="w-7 h-7 flex items-center justify-center text-slate-600 hover:text-slate-900 hover:bg-slate-200/60 rounded-r-lg transition-colors"
                          aria-label="Increase quantity"
                        >
                          <Plus className="w-3 h-3" />
                        </button>
                      </div>

                      <div className="text-right">
                        <span className="text-xs font-black text-slate-900">
                          {formatMinorToMajor(item.product.priceMinor * item.quantity, currency)}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* AI Upsell & Cross-Sell Companion */}
          {cart.length > 0 && companion && (
            <div className="mx-5 mb-3 p-3.5 rounded-2xl bg-gradient-to-br from-emerald-50/80 via-[#f0f7f3] to-white border border-emerald-200/80 shadow-2xs">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5 text-emerald-900 font-bold text-[11px]">
                  <Sparkles className="w-3.5 h-3.5 text-emerald-600 animate-pulse" />
                  <span className="uppercase tracking-wider">AI Cross-Sell Companion</span>
                </div>
                {companion.savings_minor > 0 && (
                  <span className="text-[10px] font-black bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-md">
                    Bundle Save {formatMinorToMajor(companion.savings_minor, currency)}
                  </span>
                )}
              </div>

              <div className="flex items-center gap-3">
                <div className="w-14 h-14 rounded-xl bg-white border border-emerald-100 overflow-hidden shrink-0 shadow-2xs">
                  <img
                    src={companion.image_url}
                    alt={companion.title}
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).src = defaultImageForCategory("accessories", companion.title, "Certified Partner");
                    }}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <h4 className="text-xs font-black text-slate-900 truncate leading-snug">
                    {companion.title}
                  </h4>
                  <p className="text-[10px] text-slate-500 line-clamp-2 mt-0.5 leading-tight font-medium">
                    {companion.compatibility_reason}
                  </p>
                  <div className="mt-1.5 flex items-center justify-between">
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-xs font-black text-slate-900">
                        {formatMinorToMajor(companion.price_minor, currency)}
                      </span>
                      {companion.original_price_minor && (
                        <span className="text-[10px] text-slate-400 line-through">
                          {formatMinorToMajor(companion.original_price_minor, currency)}
                        </span>
                      )}
                    </div>
                    <button
                      onClick={handleAddCompanion}
                      className="px-3 py-1 bg-[#174c3c] hover:bg-[#103c2f] active:scale-95 text-white font-bold text-[11px] rounded-lg shadow-2xs transition-all flex items-center gap-1"
                    >
                      <Plus className="w-3 h-3" />
                      <span>Add to Bag</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Footer & Checkout CTA */}
          {cart.length > 0 && (
            <div className="p-5 border-t border-slate-200 bg-white space-y-4 shadow-lg">
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between text-slate-500">
                  <span>Subtotal</span>
                  <span className="font-semibold text-slate-800">
                    {formatMinorToMajor(subtotalMinor, currency)}
                  </span>
                </div>
                <div className="flex justify-between text-slate-500">
                  <span>Express Shipping</span>
                  <span className="font-bold text-emerald-700 uppercase text-[11px]">FREE</span>
                </div>
                <div className="pt-2 border-t border-slate-100 flex justify-between items-baseline">
                  <span className="text-sm font-black text-slate-900">Estimated Total</span>
                  <span className="text-base font-black text-[#174c3c]">
                    {formatMinorToMajor(subtotalMinor, currency)}
                  </span>
                </div>
              </div>

              <div className="space-y-2">
                <button
                  onClick={handleCheckout}
                  className="w-full py-3.5 bg-[#174c3c] hover:bg-[#103c2f] active:scale-[0.99] text-white font-bold text-sm rounded-2xl shadow-sm transition-all flex items-center justify-center gap-2"
                >
                  <ShieldCheck className="w-4 h-4" />
                  <span>Proceed to Gated Checkout</span>
                  <ArrowRight className="w-4 h-4" />
                </button>

                <button
                  onClick={handleViewCart}
                  className="w-full py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
                >
                  View Full Bag &amp; Apply Promo
                </button>
              </div>

              <div className="flex items-center justify-center gap-2 text-[10px] text-slate-400 font-medium">
                <ShieldCheck className="w-3 h-3 text-[#174c3c]" />
                <span>Protected by Razorpay &amp; Autonomous Spending Policy</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
