"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  ImageOff,
  Loader2,
  RefreshCw,
  Scale,
  ShoppingBag,
  Star,
  Tag,
  Truck,
  Undo2,
} from "lucide-react";
import { formatMinorToMajor } from "@/lib/money";
import type { ApiError } from "@/lib/api";
import { useStore } from "@/context/StoreContext";
import {
  CREDENTIAL_GAP_NOTE,
  askProductQuestion,
  isCredentialGap,
  isMissing,
  getCatalogProduct,
  lookupOfferInCatalog,
  validateOffer,
} from "@/catalog/client";
import { exploreOfferToProductItem } from "@/catalog/adapt";
import {
  catalogSourceDetail,
  catalogSourceLabel,
  descriptionParagraphs,
  isExpired,
  pricingSourceDetail,
  pricingSourceLabel,
  productImageUrls,
  readableInstant,
  specRows,
  stockLabel,
} from "@/catalog/present";
import type {
  CatalogProduct,
  CatalogSourceName,
  ExploreOffer,
  ResearchAnswer,
} from "@/catalog/types";

/**
 * Product detail.
 *
 * Two reads, because the API splits the record in two and offers no join:
 *
 * * `GET /api/v1/catalog/products/{id}` -- title, description, specifications,
 *   images, aggregate rating. Scope-gated.
 * * the offer -- price, stock, delivery, return window, expiry, pricing source.
 *   There is no "offers for this product" endpoint: `GET /api/v1/catalog/offers/{id}`
 *   needs an offer id, and `POST /api/v1/catalog/search` accepts no product filter.
 *   So the open endpoint (`POST /api/explore`) is asked for a page of offers and
 *   the record is matched by `product_id`. When it is not inside that page, the
 *   screen says the offer could not be located rather than that it does not exist.
 *
 * The question box is `POST /api/v1/research/ask`. Its answer is rendered with
 * whatever the orchestrator attached: a source label, a URL, a confidence, and
 * citation items. When it attaches none -- its `unresolved` route returns an empty
 * evidence list -- the screen says the answer carries no evidence. It does not
 * name a source that was not returned.
 *
 * Removed rather than kept: the editorial "why this fits you" summary, the
 * sentiment percentages, and the individual customer reviews. No endpoint produces
 * any of them, and the sections that displayed them are replaced by a statement of
 * what the catalog does hold.
 */

const OFFER_LOOKUP_LIMIT = 50;

type Phase = "loading" | "ready" | "failed";

interface AskEntry {
  question: string;
  answer: ResearchAnswer | null;
  error: ApiError | null;
}

export default function ProductDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const productId = (params?.id ?? "").toString();

  const { addToCart, wishlist, toggleWishlist, compareList, toggleCompare } = useStore();

  const [phase, setPhase] = useState<Phase>("loading");
  const [product, setProduct] = useState<CatalogProduct | null>(null);
  const [offer, setOffer] = useState<ExploreOffer | null>(null);
  const [catalogSource, setCatalogSource] = useState<CatalogSourceName | null>(null);
  const [productError, setProductError] = useState<ApiError | null>(null);
  const [offerError, setOfferError] = useState<ApiError | null>(null);
  const [offerTruncated, setOfferTruncated] = useState(false);
  const [credentialGap, setCredentialGap] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [reloadToken, setReloadToken] = useState(0);

  const [activeImage, setActiveImage] = useState<string | null>(null);
  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({});
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);

  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [asked, setAsked] = useState<AskEntry[]>([]);

  const [revalidating, setRevalidating] = useState(false);
  const [revalidation, setRevalidation] = useState<
    { kind: "ok"; priceMinor: number; currency: string } | { kind: "failed"; error: ApiError } | null
  >(null);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  useEffect(() => {
    if (!productId) {
      setPhase("failed");
      return;
    }
    let cancelled = false;
    setPhase("loading");
    setProductError(null);
    setOfferError(null);
    setRevalidation(null);

    (async () => {
      const [productResult, offerResult] = await Promise.all([
        getCatalogProduct(productId),
        lookupOfferInCatalog({ productId }, { limit: OFFER_LOOKUP_LIMIT }),
      ]);
      if (cancelled) return;

      let resolvedProduct: CatalogProduct | null = null;
      if (productResult.ok) {
        resolvedProduct = productResult.data?.product ?? null;
      } else {
        setProductError(productResult.error);
        if (isCredentialGap(productResult.error)) setCredentialGap(true);
      }

      let resolvedOffer: ExploreOffer | null = null;
      if (offerResult.ok) {
        resolvedOffer = offerResult.data.found;
        setCatalogSource(offerResult.data.catalogSource);
        setOfferTruncated(offerResult.data.truncated && resolvedOffer === null);
        setWarnings(offerResult.data.warnings);
      } else {
        setOfferError(offerResult.error);
      }

      setProduct(resolvedProduct);
      setOffer(resolvedOffer);

      // The gallery prefers the product record's images and falls back to the
      // single image the offer projection carries.
      const gallery = productImageUrls(resolvedProduct);
      const fallback =
        typeof resolvedOffer?.image_url === "string" ? resolvedOffer.image_url.trim() : "";
      const first = gallery[0] ?? (fallback || null);
      setActiveImage(first);

      setPhase(resolvedProduct || resolvedOffer ? "ready" : "failed");
    })();

    return () => {
      cancelled = true;
    };
  }, [productId, reloadToken]);

  // ---- Derived record facts. Every one of these is read, never computed. ----
  const title = product?.title || offer?.title || productId;
  const categoryId = product?.category_id || offer?.category || null;
  const rating = product?.average_rating ?? offer?.rating ?? null;
  const ratingCount = product?.rating_number ?? offer?.reviews_count ?? null;
  const specs = (product?.specifications ?? offer?.specs ?? null) as
    | Record<string, unknown>
    | null;
  const rows = specRows(specs);
  const paragraphs = descriptionParagraphs(product?.description);

  const galleryUrls = (() => {
    const urls = productImageUrls(product);
    const fallback = typeof offer?.image_url === "string" ? offer.image_url.trim() : "";
    if (urls.length === 0 && fallback) return [fallback];
    return urls;
  })();
  const usableGallery = galleryUrls.filter((url) => !brokenImages[url]);
  const shownImage = activeImage && !brokenImages[activeImage] ? activeImage : usableGallery[0] ?? null;

  const expired = offer ? isExpired(offer.expires_at, Date.now()) : false;
  const isSaved = wishlist.includes(productId);
  const isCompared = compareList.includes(productId);

  const handleAsk = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text || asking) return;
    setAsking(true);
    setQuestion("");

    const result = await askProductQuestion({
      product_id: productId,
      question: text,
      product_title: title,
      // Only what the catalog holds is sent as context. Nothing is invented to
      // make the answer look better sourced than it is.
      catalog_specs: specs,
      reviews_summary:
        rating != null || ratingCount != null
          ? { average_rating: rating, rating_number: ratingCount }
          : null,
      offer_data: offer
        ? {
            unit_price_minor: offer.unit_price_minor,
            currency: offer.currency,
            available_stock: offer.available_stock,
            delivery_days: offer.delivery_days,
            return_period_days: offer.return_period_days,
          }
        : null,
    });

    setAsked((previous) => [
      result.ok
        ? { question: text, answer: result.data, error: null }
        : { question: text, answer: null, error: result.error },
      ...previous,
    ]);
    setAsking(false);
  };

  const handleRevalidate = async () => {
    if (!offer || revalidating) return;
    setRevalidating(true);
    setRevalidation(null);
    const result = await validateOffer(offer.offer_id, {
      expected_price_minor: offer.unit_price_minor,
      expected_offer_version: offer.offer_version,
    });
    if (result.ok) {
      const fresh = result.data?.offer;
      setRevalidation(
        fresh
          ? { kind: "ok", priceMinor: fresh.unit_price_minor, currency: fresh.currency }
          : {
              kind: "failed",
              error: {
                code: "CLIENT_MALFORMED_RESPONSE",
                message: "The gateway confirmed the offer without returning it.",
                retryable: false,
                details: {},
                nextActions: [],
                status: null,
                requestId: null,
              },
            }
      );
    } else {
      setRevalidation({ kind: "failed", error: result.error });
    }
    setRevalidating(false);
  };

  const handleAddToCart = (thenCheckout: boolean) => {
    if (!offer) return;
    addToCart(exploreOfferToProductItem(offer, catalogSource), 1);
    if (thenCheckout) router.push("/checkout");
  };

  // ---- Loading -------------------------------------------------------------
  if (phase === "loading") {
    return (
      <div className="max-w-7xl mx-auto py-20 text-center space-y-3" aria-live="polite">
        <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#174c3c]" />
        <p className="text-sm font-bold text-slate-700">Reading this product from the catalog&hellip;</p>
        <p className="text-[11px] text-slate-400 font-mono">{productId}</p>
      </div>
    );
  }

  // ---- Nothing could be read ----------------------------------------------
  if (phase === "failed") {
    const notFound = productError != null && isMissing(productError);
    return (
      <div className="max-w-2xl mx-auto py-16 space-y-5 text-center">
        <h1 className="text-2xl font-black text-slate-900">
          {notFound ? "This product is not in the catalog" : "This product could not be read"}
        </h1>
        <p className="text-sm text-slate-500">
          {notFound
            ? "No product with this identifier exists for this merchant."
            : productError?.message ||
              offerError?.message ||
              "Neither the product record nor an offer for it could be read."}
        </p>
        <div className="bg-slate-50 border border-slate-200 rounded-2xl p-4 text-xs font-mono space-y-1.5 text-left">
          <div className="flex justify-between">
            <span className="text-slate-500">Product identifier</span>
            <span className="text-slate-800">{productId || "(none in the address)"}</span>
          </div>
          {productError ? (
            <div className="flex justify-between">
              <span className="text-slate-500">Product read</span>
              <span className="text-slate-800">{productError.code}</span>
            </div>
          ) : null}
          {offerError ? (
            <div className="flex justify-between">
              <span className="text-slate-500">Offer lookup</span>
              <span className="text-slate-800">{offerError.code}</span>
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={reload}
            className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs"
          >
            Try again
          </button>
          <Link
            href="/search"
            className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl"
          >
            Search the catalog
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-12 pb-16 max-w-7xl mx-auto">
      {/* Breadcrumbs */}
      <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
        <Link href="/" className="hover:text-[#174c3c]">
          Home
        </Link>
        <span>/</span>
        {categoryId ? (
          <Link href={`/search?q=${encodeURIComponent(categoryId)}`} className="hover:text-[#174c3c]">
            {categoryId}
          </Link>
        ) : (
          <span>Uncategorised</span>
        )}
        <span>/</span>
        <span className="text-slate-900 truncate max-w-sm">{title}</span>
      </nav>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-12 items-start">
        {/* Gallery */}
        <div className="lg:col-span-7 flex flex-col-reverse sm:flex-row gap-4 items-start">
          <div className="flex sm:flex-col gap-3 overflow-x-auto sm:overflow-visible w-full sm:w-20 shrink-0">
            {usableGallery.map((url, index) => (
              <button
                key={url}
                type="button"
                onClick={() => setActiveImage(url)}
                className={`w-16 h-16 sm:w-20 sm:h-20 rounded-2xl overflow-hidden border-2 transition-all shrink-0 ${
                  shownImage === url
                    ? "border-[#174c3c] ring-2 ring-[#174c3c]/20"
                    : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <img
                  src={url}
                  alt={`${title} view ${index + 1}`}
                  className="w-full h-full object-cover"
                  onError={() => setBrokenImages((previous) => ({ ...previous, [url]: true }))}
                />
              </button>
            ))}
          </div>

          <div
            onClick={() => shownImage && setIsLightboxOpen(true)}
            className={`flex-1 w-full bg-slate-100 rounded-3xl overflow-hidden border border-slate-200 relative aspect-4/3 sm:aspect-square flex items-center justify-center group ${
              shownImage ? "cursor-zoom-in" : ""
            }`}
          >
            {shownImage ? (
              <img
                src={shownImage}
                alt={title}
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                onError={() =>
                  setBrokenImages((previous) => ({ ...previous, [shownImage as string]: true }))
                }
              />
            ) : (
              <div className="flex flex-col items-center gap-2 p-8 text-center text-slate-500">
                <ImageOff className="h-8 w-8" />
                <p className="text-sm font-bold text-slate-700">No usable image for this product</p>
                <p className="text-xs max-w-xs">
                  {galleryUrls.length === 0
                    ? "The catalog record carries no image."
                    : "Every image URL on this record failed to load in this browser."}
                </p>
              </div>
            )}

            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                toggleWishlist(productId);
              }}
              className={`absolute top-4 right-4 w-10 h-10 rounded-full flex items-center justify-center shadow-sm transition-all ${
                isSaved ? "bg-rose-50 text-rose-600" : "bg-white/90 text-slate-500 hover:text-rose-600"
              }`}
              aria-label={isSaved ? "Remove from saved products" : "Save product"}
            >
              <span className="text-base">{isSaved ? "\u2665" : "\u2661"}</span>
            </button>
          </div>
        </div>

        {/* Purchase box */}
        <div className="lg:col-span-5 bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6">
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-bold text-[#174c3c] uppercase tracking-wider">
                {categoryId || "Uncategorised"}
              </span>
              <span className="font-mono text-slate-400">
                {offer ? `Merchant ${offer.merchant_id}` : "No active offer"}
              </span>
            </div>
            <h1 className="text-xl sm:text-2xl font-black text-slate-900 leading-snug">{title}</h1>
            <div className="flex flex-wrap items-center gap-3 text-xs pt-1">
              {ratingCount != null && ratingCount > 0 && rating != null ? (
                <div className="flex items-center gap-1 font-bold text-slate-900 bg-amber-50 px-2 py-0.5 rounded-lg">
                  <Star className="h-3 w-3 fill-amber-500 text-amber-500" />
                  <span>{rating}</span>
                  <span className="text-slate-400 font-normal">
                    ({ratingCount} {ratingCount === 1 ? "rating" : "ratings"})
                  </span>
                </div>
              ) : (
                <span className="text-slate-500 bg-slate-50 px-2 py-0.5 rounded-lg font-semibold">
                  No ratings recorded
                </span>
              )}
              {offer ? (
                <span
                  className={`px-2 py-0.5 rounded-lg font-bold ${
                    offer.available_stock > 0
                      ? "text-emerald-700 bg-emerald-50"
                      : "text-rose-700 bg-rose-50"
                  }`}
                >
                  {stockLabel(offer.available_stock)}
                </span>
              ) : null}
            </div>
          </div>

          {/* Price, or a stated absence of one */}
          {offer ? (
            <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-3">
              <div className="flex items-baseline gap-3">
                <span
                  className="text-3xl font-black text-slate-900"
                  data-amount-minor={offer.unit_price_minor}
                  data-currency={offer.currency}
                >
                  {formatMinorToMajor(offer.unit_price_minor, offer.currency)}
                </span>
                <span className="text-xs text-slate-500">per unit</span>
              </div>

              <div
                className="inline-flex items-center gap-1.5 rounded-full bg-[#e5f0e9] px-2.5 py-1 text-[10px] font-semibold text-[#174c3c]"
                title={pricingSourceDetail(offer.pricing_source)}
              >
                <Tag className="h-3 w-3" />
                {pricingSourceLabel(offer.pricing_source)}
              </div>
              <p className="text-[11px] text-slate-500 leading-relaxed">
                {pricingSourceDetail(offer.pricing_source)} The amount you pay is computed by the
                gateway at checkout, not on this page.
              </p>

              <div className="text-xs text-slate-700 space-y-1 font-medium pt-1">
                <div className="flex items-center gap-1.5">
                  <Truck className="h-3.5 w-3.5 text-[#174c3c]" />
                  <span>
                    Delivery in{" "}
                    <strong>
                      {offer.delivery_days} {offer.delivery_days === 1 ? "day" : "days"}
                    </strong>
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Undo2 className="h-3.5 w-3.5 text-[#174c3c]" />
                  <span>
                    <strong>{offer.return_period_days}-day</strong> return window
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 font-mono pt-1">
                  offer {offer.offer_id} &middot; v{offer.offer_version}
                  {readableInstant(offer.expires_at)
                    ? ` \u00b7 valid until ${readableInstant(offer.expires_at)}`
                    : ""}
                </div>
              </div>

              {expired ? (
                <p className="rounded-xl bg-rose-50 border border-rose-200 p-2.5 text-[11px] font-semibold text-rose-800">
                  This offer&rsquo;s validity window has passed. The gateway will refuse a checkout
                  against it.
                </p>
              ) : null}
            </div>
          ) : (
            <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-2 text-xs text-slate-700">
              <p className="font-black text-sm text-slate-900">No price to show</p>
              <p className="leading-relaxed">
                {offerError
                  ? offerError.message
                  : offerTruncated
                  ? `No offer for this product was inside the page of ${OFFER_LOOKUP_LIMIT} the catalog returned. There is no product-scoped offer endpoint, so this page cannot say whether one exists further down the ranking.`
                  : "The catalog returned no active, in-stock, unexpired offer for this product."}
              </p>
              <button
                type="button"
                onClick={reload}
                className="mt-1 px-4 py-2 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold rounded-xl"
              >
                Look again
              </button>
            </div>
          )}

          {/* Actions */}
          <div className="space-y-3 pt-2">
            <button
              type="button"
              disabled={!offer || expired || offer.available_stock <= 0}
              onClick={() => handleAddToCart(true)}
              className="w-full py-3.5 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-sm rounded-2xl shadow-sm transition-all flex items-center justify-center gap-2"
            >
              <ShoppingBag className="h-4 w-4" />
              <span>Buy now</span>
            </button>

            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={!offer || expired || offer.available_stock <= 0}
                onClick={() => handleAddToCart(false)}
                className="flex-1 py-3 bg-slate-900 hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded-2xl shadow-xs transition-all"
              >
                Add to bag
              </button>
              <button
                type="button"
                onClick={() => toggleCompare(productId)}
                className={`py-3 px-4 rounded-2xl text-xs font-bold border transition-all inline-flex items-center gap-1.5 ${
                  isCompared
                    ? "bg-[#e5f0e9] border-[#174c3c] text-[#174c3c]"
                    : "bg-white hover:bg-slate-50 border-slate-200 text-slate-700"
                }`}
              >
                <Scale className="h-3.5 w-3.5" />
                {isCompared ? "Comparing" : "Compare"}
              </button>
            </div>

            {/* Revalidation: the gateway's own answer about this price */}
            {offer ? (
              <div className="space-y-2 pt-1">
                <button
                  type="button"
                  onClick={handleRevalidate}
                  disabled={revalidating}
                  className="w-full py-2 bg-slate-50 hover:bg-slate-100 disabled:opacity-60 text-slate-800 text-xs font-bold rounded-xl transition-all flex items-center justify-center gap-2 border border-slate-200"
                >
                  {revalidating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                  {revalidating ? "Re-checking with the gateway\u2026" : "Re-check this price and stock"}
                </button>
                {revalidation?.kind === "ok" ? (
                  <p
                    className="rounded-xl bg-emerald-50 border border-emerald-200 p-2.5 text-[11px] font-semibold text-emerald-900"
                    data-amount-minor={revalidation.priceMinor}
                    data-currency={revalidation.currency}
                  >
                    The gateway confirmed this offer at{" "}
                    {formatMinorToMajor(revalidation.priceMinor, revalidation.currency)}.
                  </p>
                ) : null}
                {revalidation?.kind === "failed" ? (
                  <p className="rounded-xl bg-amber-50 border border-amber-200 p-2.5 text-[11px] text-amber-900">
                    <span className="font-bold">{revalidation.error.code}. </span>
                    {isCredentialGap(revalidation.error)
                      ? "Offer revalidation is scope-gated and this browser holds no credential for it, so this price could not be re-confirmed here. The gateway revalidates it again at checkout regardless."
                      : revalidation.error.message}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>

          <p className="text-[11px] text-slate-400 leading-relaxed">
            Every figure in this box is read from the catalog record shown above. This page performs no
            arithmetic on an amount.
          </p>
        </div>
      </div>

      {/* Description */}
      <section className="bg-white rounded-3xl border border-slate-200 p-6 sm:p-8 space-y-4">
        <h2 className="text-xl font-black text-slate-900">About this product</h2>
        {paragraphs.length > 0 ? (
          <div className="space-y-3 text-sm leading-6 text-slate-700">
            {paragraphs.map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
          </div>
        ) : (
          <p className="text-xs text-slate-500 leading-relaxed">
            {product
              ? "This catalog record carries no description."
              : "The product record could not be read on this page, and the offer projection carries no description."}
          </p>
        )}
      </section>

      {/* Specifications */}
      <section className="bg-white rounded-3xl border border-slate-200 p-6 sm:p-8 space-y-6">
        <div className="flex items-center justify-between border-b border-slate-100 pb-4">
          <h2 className="text-xl font-black text-slate-900">Specifications</h2>
          <span className="text-[11px] font-mono text-slate-400">
            {rows.length} {rows.length === 1 ? "specification" : "specifications"} on this record
          </span>
        </div>
        {rows.length > 0 ? (
          <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-10 text-xs">
            {rows.map((row) => (
              <div key={row.key} className="py-2.5 flex justify-between gap-4 border-b border-slate-100">
                <dt className="text-slate-500 font-medium">{row.label}</dt>
                <dd className="font-bold text-slate-900 text-right">{row.value}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-xs text-slate-500">
            The catalog holds no specifications for this product. A specification the record does not
            hold is left out rather than shown as zero, which is also why a memory or storage filter
            excludes this product instead of matching it.
          </p>
        )}
      </section>

      {/* Question box: POST /api/v1/research/ask */}
      <section className="bg-white rounded-3xl border border-slate-200 p-6 sm:p-8 space-y-6">
        <div>
          <h2 className="text-xl font-black text-slate-900">Ask about this product</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Answered by the research service, which checks the catalog record, the offer record, the
            review aggregate, and then external documentation. Each answer shows what it was based on.
          </p>
        </div>

        <form onSubmit={handleAsk} className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={asking}
            placeholder="e.g. how much memory does it have, or what is the return window"
            className="flex-1 px-4 py-3 text-xs sm:text-sm border border-slate-200 rounded-2xl bg-slate-50 focus:border-[#174c3c] focus:outline-none disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={asking || !question.trim()}
            className="px-5 py-3 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-50 text-white font-bold text-xs rounded-2xl shadow-xs transition-all inline-flex items-center justify-center gap-2"
          >
            {asking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {asking ? "Researching\u2026" : "Ask"}
          </button>
        </form>

        {asked.length === 0 && !asking ? (
          <p className="text-xs text-slate-400">
            No questions asked yet. Answers are not stored on this page between visits, because no
            endpoint publishes a question history for a product.
          </p>
        ) : null}

        <div className="space-y-4">
          {asked.map((entry, index) => (
            <div
              key={`${index}-${entry.question}`}
              className="p-4 bg-slate-50 rounded-2xl border border-slate-200/70 space-y-2 text-xs"
            >
              <div className="font-bold text-slate-900 text-sm flex items-start gap-2">
                <span className="text-[#174c3c]">Q:</span>
                <span>{entry.question}</span>
              </div>

              {entry.error ? (
                <div className="pl-4 space-y-1">
                  <p className="text-rose-800 font-semibold">
                    This question could not be answered. {entry.error.message}
                  </p>
                  <p className="font-mono text-[10px] text-slate-400">
                    {entry.error.code}
                    {entry.error.status != null ? ` \u00b7 HTTP ${entry.error.status}` : ""}
                  </p>
                </div>
              ) : entry.answer ? (
                <div className="pl-4 space-y-2">
                  <p className="text-slate-700 leading-relaxed font-medium">{entry.answer.answer}</p>

                  {/* Evidence, or a plain statement that there is none */}
                  {entry.answer.evidence_items && entry.answer.evidence_items.length > 0 ? (
                    <ul className="space-y-1.5">
                      {entry.answer.evidence_items.map((item, itemIndex) => (
                        <li
                          key={itemIndex}
                          className="rounded-xl border border-slate-200 bg-white p-2.5 space-y-0.5"
                        >
                          {item.claim ? <p className="text-slate-700">{item.claim}</p> : null}
                          <p className="font-mono text-[10px] text-slate-400">
                            {item.citation_type || item.source_type || "citation"}
                            {item.confidence_level ? ` \u00b7 ${item.confidence_level}` : ""}
                          </p>
                          {item.source_url ? (
                            <a
                              href={item.source_url}
                              target="_blank"
                              rel="noreferrer noopener"
                              className="text-[10px] font-bold text-[#174c3c] hover:underline break-all"
                            >
                              {item.source_url}
                            </a>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="rounded-xl border border-amber-200 bg-amber-50 p-2.5 text-[11px] text-amber-900">
                      This answer came back with no evidence attached, so there is no citation to show.
                      Treat it as unverified.
                      {entry.answer.reason_for_web_search
                        ? ` ${entry.answer.reason_for_web_search}`
                        : ""}
                    </p>
                  )}

                  <div className="font-mono text-[10px] text-slate-400 space-y-0.5">
                    <div>
                      Source: {entry.answer.source_label || entry.answer.source_type || "not stated"}
                      {entry.answer.confidence_level
                        ? ` \u00b7 confidence ${entry.answer.confidence_level}`
                        : ""}
                      {entry.answer.from_cache ? " \u00b7 from cache" : ""}
                    </div>
                    {entry.answer.transparency_steps &&
                    entry.answer.transparency_steps.length > 0 ? (
                      <div className="text-slate-400">
                        {entry.answer.transparency_steps.join(" \u2192 ")}
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      {/* What the catalog holds about reception, and what it does not */}
      <section className="bg-white rounded-3xl border border-slate-200 p-6 sm:p-8 space-y-4">
        <h2 className="text-xl font-black text-slate-900">Customer reception</h2>
        {ratingCount != null && ratingCount > 0 && rating != null ? (
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-black text-slate-900">{rating}</span>
            <span className="text-xs text-slate-500">
              out of 5, from {ratingCount} {ratingCount === 1 ? "rating" : "ratings"} recorded on the
              product record
            </span>
          </div>
        ) : (
          <p className="text-xs text-slate-500">This product record carries no rating.</p>
        )}
        <p className="text-xs text-slate-500 leading-relaxed">
          <strong>Not yet connected:</strong> individual review text, review filtering, and per-topic
          sentiment. The catalog stores an aggregate rating and a rating count, and no endpoint
          publishes the reviews behind them, so nothing is shown in their place.
        </p>
      </section>

      {/* Provenance footer */}
      <section className="rounded-3xl border border-slate-200 bg-slate-50 p-5 text-[11px] text-slate-500 space-y-1">
        <p>
          <span className="font-bold text-slate-700">Where this page came from: </span>
          product record via <span className="font-mono">GET /api/v1/catalog/products/{"{id}"}</span>
          {product ? " (read)" : " (unavailable)"}; offer via{" "}
          <span className="font-mono">POST /api/explore</span>
          {offer ? " (matched by product_id)" : " (no match)"}.
        </p>
        <p title={catalogSourceDetail(catalogSource)}>
          <span className="font-bold text-slate-700">Catalog that answered: </span>
          {catalogSourceLabel(catalogSource)}. {catalogSourceDetail(catalogSource)}
        </p>
      </section>

      {/* Lightbox */}
      {isLightboxOpen && shownImage ? (
        <div
          onClick={() => setIsLightboxOpen(false)}
          className="fixed inset-0 z-50 bg-black/90 backdrop-blur-md flex items-center justify-center p-4"
        >
          <div className="relative max-w-4xl max-h-[90vh] w-full flex items-center justify-center">
            <img
              src={shownImage}
              alt={title}
              className="max-h-[85vh] max-w-full object-contain rounded-2xl"
            />
            <button
              onClick={() => setIsLightboxOpen(false)}
              className="absolute top-4 right-4 w-10 h-10 bg-white/20 hover:bg-white/40 text-white rounded-full flex items-center justify-center font-bold text-lg"
              aria-label="Close image"
            >
              &times;
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
