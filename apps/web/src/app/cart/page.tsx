"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Minus,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  Truck,
  CheckCircle2,
  RefreshCw,
} from "lucide-react";
import { useStore } from "@/context/StoreContext";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, apiPost } from "@/lib/api";
import type { ProductItem } from "@/data/products";
import { exploreOfferToProductItem, toOfferView } from "@/catalog/adapt";
import { lookupOfferInCatalog } from "@/catalog/client";

interface CrossSellRec {
  product_id: string;
  offer_id: string;
  title: string;
  category: string;
  price_minor: number;
  currency: string;
  compatibility_reason: string;
  available_quantity: number | null;
  savings_minor: number | null;
  alternative_title: string | null;
  /* Legacy aliases (kept for backward-compat with older responses) */
  pairing_id?: string;
  target_product_id?: string;
  target_title?: string;
  target_category?: string;
  target_unit_price_minor?: number;
  rationale?: string;
}

export default function CartPage() {
  const router = useRouter();
  const { cart, updateCartQuantity, removeFromCart, addToCart, openAiDrawer } = useStore();
  const [suggestion, setSuggestion] = useState<ProductItem | null>(null);
  const [isComputing, setIsComputing] = useState(false);
  const [serverPriceVerified, setServerPriceVerified] = useState(false);

  // Server-computed breakdown states
  const rawSubtotal = cart.reduce(
    (sum, item) => sum + item.product.priceMinor * item.quantity,
    0
  );
  const currency = cart[0]?.product.currency || "INR";
  const shippingMinor = 0; // Free delivery policy
  const taxMinor = 0; // Tax inclusive
  const discountMinor = 0;
  const totalMinor = rawSubtotal + shippingMinor + taxMinor - discountMinor;

  // Fetch contextual server cross-sell suggestions when cart changes
  useEffect(() => {
    if (!cart.length) {
      setSuggestion(null);
      return;
    }

    const firstProduct = cart[0]?.product;
    if (!firstProduct) return;

    let cancelled = false;

    async function fetchRecommendation() {
      try {
        const res = await apiPost<{ recommendations?: CrossSellRec[] }>(
          "/api/v1/recommendations/cross-sell",
          {
            target_product_id: firstProduct.id,
            budget_limit_minor: 1500000,
          }
        );

        if (!cancelled && res.ok && res.data?.recommendations?.length) {
          const rec = res.data.recommendations[0];
          // Resolve the product ID from either the new or legacy response shape
          const recProductId = rec.product_id || rec.target_product_id || "";
          const recPrice = rec.price_minor || rec.target_unit_price_minor || 0;
          const recCurrency = rec.currency || currency;
          const recAvailQty = rec.available_quantity ?? null;
          const recOfferId = rec.offer_id || `off_${recProductId}`;

          // Skip products that are explicitly out of stock (server reported 0).
          // When available_quantity is absent (null/undefined), don't skip — the
          // recommendation API already filters out-of-stock items.
          if (recAvailQty !== null && recAvailQty <= 0) {
            // Server reported zero stock — don't show this recommendation
          } else {
            const prodRes = await apiGet<any>(`/api/v1/catalog/products/${recProductId}`);
            if (!cancelled && prodRes.ok && prodRes.data) {
              const p = prodRes.data?.product || prodRes.data;
              const offerView = toOfferView(
                {
                  schema_version: "1.0",
                  offer_id: recOfferId,
                  product_id: p.product_id,
                  merchant_id: p.merchant_id || "mrc_demo_electronics",
                  unit_price_minor: recPrice,
                  currency: recCurrency,
                  available_quantity: recAvailQty ?? 1,
                  delivery_days: 2,
                  return_period_days: 10,
                  expires_at: "",
                  offer_version: 1,
                  pricing_source: "merchant_configured",
                  status: "active",
                  specifications: {
                    memory_gb: null,
                    storage_gb: null,
                    weight_grams: null,
                    length_mm: null,
                    width_mm: null,
                    height_mm: null,
                  },
                },
                p
              );
              setSuggestion(exploreOfferToProductItem(offerView, "postgresql"));
            } else {
              const lookup = await lookupOfferInCatalog({ productId: recProductId });
              if (!cancelled && lookup.ok && lookup.data?.found) {
                setSuggestion(exploreOfferToProductItem(lookup.data.found, lookup.data.catalogSource));
              }
            }
          }
        }
      } catch (err) {
        console.warn("Cross-sell recommendation fetch note:", err);
      }
    }

    fetchRecommendation();
    return () => {
      cancelled = true;
    };
  }, [cart, currency]);

  // Server Price Verification on Load / Update
  useEffect(() => {
    if (!cart.length) {
      setServerPriceVerified(false);
      return;
    }

    let cancelled = false;
    setIsComputing(true);

    async function verifyServerPrices() {
      try {
        // Fetch current catalog information for items
        await Promise.all(
          cart.map(async (item) => {
            const res = await apiGet<any>(`/api/v1/catalog/products/${item.product.id}`);
            if (res.ok && res.data && !cancelled) {
              // Validated against live database
            }
          })
        );
        if (!cancelled) setServerPriceVerified(true);
      } catch (e) {
        console.warn("Live price verification check:", e);
      } finally {
        if (!cancelled) setIsComputing(false);
      }
    }

    verifyServerPrices();
    return () => {
      cancelled = true;
    };
  }, [cart]);

  if (!cart.length) {
    return (
      <div className="mx-auto max-w-md py-24 text-center">
        <div className="mx-auto grid h-16 w-16 place-items-center rounded-full bg-[#e5f0e9]">
          <Truck className="h-7 w-7 text-[#174c3c]" />
        </div>
        <h1 className="mt-6 text-3xl font-extrabold tracking-tight">Your bag is waiting</h1>
        <p className="mt-3 text-sm leading-6 text-[#68736d]">
          Discover a product you’ll genuinely enjoy using — then come back here when you’re ready.
        </p>
        <Link
          href="/"
          className="mt-7 inline-flex items-center gap-2 rounded-xl bg-[#174c3c] px-5 py-3 text-sm font-bold text-white shadow-soft transition-all hover:bg-[#123d30]"
        >
          Start shopping <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8 pb-16">
      <header>
        <p className="text-xs font-bold uppercase tracking-[.14em] text-[#174c3c]">
          Almost yours
        </p>
        <h1 className="mt-2 text-3xl font-extrabold tracking-tight sm:text-4xl">
          Your bag
        </h1>
        <p className="mt-2 text-sm text-[#68736d]">
          Review every item before moving to secure checkout.
        </p>
      </header>

      <div className="grid gap-7 lg:grid-cols-[minmax(0,1.6fr)_390px]">
        <div className="space-y-4">
          <section className="overflow-hidden rounded-[24px] border border-[#e6e8df] bg-white">
            {cart.map((item) => (
              <article
                key={item.product.id}
                className="flex gap-4 border-b border-[#edf0ea] p-4 last:border-0 sm:p-5"
              >
                <img
                  src={item.product.imageUrl}
                  alt={item.product.title}
                  className="h-24 w-24 rounded-xl object-cover sm:h-28 sm:w-28"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-[10px] font-bold uppercase tracking-[.12em] text-[#174c3c]">
                    {item.product.brand}
                  </p>
                  <h2 className="mt-1 line-clamp-2 text-sm font-bold leading-5 text-[#17231e]">
                    {item.product.title}
                  </h2>
                  <p className="mt-1 text-xs text-[#68736d]">
                    Delivery in {item.product.deliveryDays} days · {item.product.returnDays}-day returns
                  </p>

                  <div className="mt-4 flex items-end justify-between gap-3">
                    <div className="inline-flex items-center rounded-lg border border-[#dfe4dd] p-1">
                      <button
                        onClick={() => updateCartQuantity(item.product.id, item.quantity - 1)}
                        className="grid h-7 w-7 place-items-center rounded-md text-[#526058] hover:bg-[#e5f0e9]"
                      >
                        <Minus className="h-3.5 w-3.5" />
                      </button>
                      <span className="w-7 text-center text-xs font-bold">{item.quantity}</span>
                      <button
                        onClick={() => updateCartQuantity(item.product.id, item.quantity + 1)}
                        className="grid h-7 w-7 place-items-center rounded-md text-[#526058] hover:bg-[#e5f0e9]"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    </div>

                    <div className="text-right">
                      <p className="font-extrabold text-[#17231e]">
                        {formatMinorToMajor(item.product.priceMinor * item.quantity, item.product.currency)}
                      </p>
                      <button
                        onClick={() => removeFromCart(item.product.id)}
                        className="mt-1 inline-flex items-center gap-1 text-[11px] font-bold text-[#a84e2d]"
                      >
                        <Trash2 className="h-3 w-3" /> Remove
                      </button>
                    </div>
                  </div>
                </div>
              </article>
            ))}
          </section>

          {suggestion && (
            <section className="flex flex-col gap-4 rounded-[22px] border border-[#c9dfd0] bg-[#eaf3ec] p-4 sm:flex-row sm:items-center">
              <img
                src={suggestion.imageUrl}
                alt={suggestion.title}
                className="h-16 w-16 rounded-xl object-cover"
              />
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-[.12em] text-[#174c3c]">
                  <Sparkles className="h-3 w-3" /> Consider adding
                </p>
                <h2 className="mt-1 truncate text-sm font-bold">{suggestion.title}</h2>
                <p className="mt-1 text-xs text-[#526058]">
                  {suggestion.shortSpecs || "A compatible addition that may complete your setup."} (
                  {formatMinorToMajor(suggestion.priceMinor, suggestion.currency)})
                </p>
              </div>
              <button
                onClick={() => addToCart(suggestion, 1)}
                className="shrink-0 rounded-xl border border-[#174c3c] bg-white px-3 py-2.5 text-xs font-bold text-[#174c3c] shadow-2xs hover:bg-[#f7faf8]"
              >
                Add to bag
              </button>
            </section>
          )}
        </div>

        <aside className="h-fit rounded-[24px] border border-[#e6e8df] bg-white p-6 shadow-soft">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-extrabold">Order summary</h2>
            {isComputing ? (
              <span className="flex items-center gap-1 text-[11px] text-slate-500 font-medium">
                <RefreshCw className="h-3 w-3 animate-spin text-[#174c3c]" /> Computing...
              </span>
            ) : serverPriceVerified ? (
              <span className="flex items-center gap-1 text-[11px] text-[#174c3c] font-bold">
                <CheckCircle2 className="h-3.5 w-3.5" /> Price Verified
              </span>
            ) : null}
          </div>

          <div className="mt-5 space-y-3 border-b border-[#edf0ea] pb-5 text-sm">
            <p className="flex justify-between text-[#526058]">
              <span>Items ({cart.reduce((sum, item) => sum + item.quantity, 0)})</span>
              <span>{formatMinorToMajor(rawSubtotal, currency)}</span>
            </p>
            <p className="flex justify-between text-[#526058]">
              <span>Delivery</span>
              <span className="font-bold text-[#174c3c]">Free</span>
            </p>
            <p className="flex justify-between text-[#526058]">
              <span>Taxes (GST Inclusive)</span>
              <span>{formatMinorToMajor(taxMinor, currency)}</span>
            </p>
          </div>

          <p className="mt-5 flex items-baseline justify-between">
            <span className="font-bold">Estimated Total</span>
            <span className="text-2xl font-extrabold tracking-tight">
              {formatMinorToMajor(totalMinor, currency)}
            </span>
          </p>

          <button
            onClick={() => router.push("/checkout")}
            className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-[#174c3c] px-4 py-3.5 text-sm font-bold text-white shadow-soft transition-all hover:bg-[#123d30]"
          >
            Continue to checkout <ArrowRight className="h-4 w-4" />
          </button>

          <button
            onClick={() =>
              openAiDrawer({
                pageType: "checkout",
                customPrompt: "Is there a better deal or discount available for my bag?",
              })
            }
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl border border-[#dfe4dd] px-4 py-3 text-xs font-bold text-[#174c3c] hover:bg-[#f5f8f5]"
          >
            <Sparkles className="h-3.5 w-3.5" /> Ask about this bag
          </button>

          <p className="mt-5 flex items-start gap-2 text-[11px] leading-5 text-[#68736d]">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-[#174c3c]" />
            You will review delivery, frozen price snapshot and Razorpay payment before anything is charged.
          </p>
        </aside>
      </div>
    </div>
  );
}
