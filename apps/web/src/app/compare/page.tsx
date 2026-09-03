"use client";

import React, { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ImageOff, Loader2, ShoppingBag } from "lucide-react";
import { formatMinorToMajor } from "@/lib/money";
import type { ApiError } from "@/lib/api";
import { useStore } from "@/context/StoreContext";
import {
  CREDENTIAL_GAP_NOTE,
  getCatalogOffer,
  getCatalogProduct,
  isCredentialGap,
  lookupOfferInCatalog,
} from "@/catalog/client";
import { exploreOfferToProductItem, toOfferView } from "@/catalog/adapt";
import {
  catalogSourceDetail,
  catalogSourceLabel,
  isExpired,
  pricingSourceDetail,
  pricingSourceLabel,
  readableInstant,
  specRows,
  stockLabel,
} from "@/catalog/present";
import type { CatalogSourceName, ExploreOffer } from "@/catalog/types";

/**
 * Side-by-side comparison of *offers*.
 *
 * Offers, not products, because an offer is what a buyer can act on: the price,
 * the stock, the delivery window, the return window, and the validity period all
 * live on the offer record, and two offers for the same product can differ in
 * every one of them.
 *
 * The identifiers come from the address, so a comparison is a shareable thing:
 *
 *     /compare?offers=off_a,off_b
 *     /compare?products=prd_a,prd_b
 *
 * With no identifiers in the address, the comparison list held in this browser is
 * used, and the address-shaped equivalent is offered as a link.
 *
 * On rejected candidates: the catalog answers a search with the offers that
 * matched and reports nothing about the ones it eliminated -- there is no
 * per-candidate rejection field anywhere in the API. So what this page can show,
 * and does, is the reason each *requested* identifier failed to resolve, plus the
 * baseline conditions visible on a resolved record (expired, or no stock), which
 * are the conditions the search itself applies before any stated filter. Anything
 * beyond that would be this screen re-deriving the filter semantics locally, which
 * is the divergence `services/offers/constraints.py` exists to prevent.
 */

interface ResolvedCandidate {
  requested: string;
  kind: "offer" | "product";
  offer: ExploreOffer | null;
  error: ApiError | null;
  /** True when the identifier was simply not inside the page the catalog returned. */
  outsidePage: boolean;
}

const LOOKUP_LIMIT = 50;

function parseIdentifiers(raw: string | null): string[] {
  if (!raw) return [];
  const seen: Record<string, true> = {};
  const list: string[] = [];
  raw
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
    .forEach((entry) => {
      if (!seen[entry]) {
        seen[entry] = true;
        list.push(entry);
      }
    });
  return list.slice(0, 4);
}

function CompareContent() {
  const searchParams = useSearchParams();
  const { compareList, toggleCompare, addToCart } = useStore();

  const offerIds = parseIdentifiers(searchParams.get("offers"));
  const productIdsFromUrl = parseIdentifiers(searchParams.get("products"));
  const fromUrl = offerIds.length > 0 || productIdsFromUrl.length > 0;
  const productIds = fromUrl ? productIdsFromUrl : parseIdentifiers(compareList.join(","));

  const [loading, setLoading] = useState(false);
  const [candidates, setCandidates] = useState<ResolvedCandidate[]>([]);
  const [catalogSource, setCatalogSource] = useState<CatalogSourceName | null>(null);
  const [credentialGap, setCredentialGap] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  const offerKey = offerIds.join(",");
  const productKey = productIds.join(",");

  useEffect(() => {
    const wantedOffers = parseIdentifiers(offerKey);
    const wantedProducts = parseIdentifiers(productKey);
    if (wantedOffers.length === 0 && wantedProducts.length === 0) {
      setCandidates([]);
      return;
    }

    let cancelled = false;
    setLoading(true);

    (async () => {
      const resolved: ResolvedCandidate[] = [];
      let source: CatalogSourceName | null = null;
      let sawCredentialGap = false;

      // Offer identifiers: the direct read first, then the open endpoint.
      for (let index = 0; index < wantedOffers.length; index += 1) {
        const offerId = wantedOffers[index];
        const direct = await getCatalogOffer(offerId);
        if (direct.ok && direct.data?.offer) {
          const record = direct.data.offer;
          const productResult = await getCatalogProduct(record.product_id);
          const product = productResult.ok ? productResult.data?.product : undefined;
          const firstImage = (product?.images ?? []).find(
            (image) => typeof image?.source_url === "string" && image.source_url.trim().length > 0
          );
          resolved.push({
            requested: offerId,
            kind: "offer",
            offer: toOfferView(record, {
              title: product?.title,
              category_id: product?.category_id,
              average_rating: product?.average_rating,
              rating_number: product?.rating_number,
              imageUrl: firstImage?.source_url ?? null,
              specifications: product?.specifications ?? null,
            }),
            error: null,
            outsidePage: false,
          });
          continue;
        }
        if (!direct.ok && isCredentialGap(direct.error)) sawCredentialGap = true;

        const lookup = await lookupOfferInCatalog({ offerId }, { limit: LOOKUP_LIMIT });
        if (lookup.ok) {
          source = lookup.data.catalogSource ?? source;
          resolved.push({
            requested: offerId,
            kind: "offer",
            offer: lookup.data.found,
            error: lookup.data.found ? null : direct.ok ? null : direct.error,
            outsidePage: lookup.data.found === null && lookup.data.truncated,
          });
        } else {
          resolved.push({
            requested: offerId,
            kind: "offer",
            offer: null,
            error: lookup.error,
            outsidePage: false,
          });
        }
      }

      // Product identifiers: only the open endpoint can turn one into an offer.
      for (let index = 0; index < wantedProducts.length; index += 1) {
        const id = wantedProducts[index];
        const lookup = await lookupOfferInCatalog({ productId: id }, { limit: LOOKUP_LIMIT });
        if (lookup.ok) {
          source = lookup.data.catalogSource ?? source;
          resolved.push({
            requested: id,
            kind: "product",
            offer: lookup.data.found,
            error: null,
            outsidePage: lookup.data.found === null && lookup.data.truncated,
          });
        } else {
          resolved.push({
            requested: id,
            kind: "product",
            offer: null,
            error: lookup.error,
            outsidePage: false,
          });
        }
      }

      if (cancelled) return;
      setCandidates(resolved);
      setCatalogSource(source);
      setCredentialGap(sawCredentialGap);
      setLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [offerKey, productKey, reloadToken]);

  const compared = candidates.filter((candidate): candidate is ResolvedCandidate & { offer: ExploreOffer } =>
    candidate.offer !== null
  );
  const rejected = candidates.filter((candidate) => candidate.offer === null);

  const shareHref =
    compared.length > 0
      ? `/compare?offers=${encodeURIComponent(compared.map((entry) => entry.offer.offer_id).join(","))}`
      : "/compare";

  const specValue = (offer: ExploreOffer, key: string): string => {
    const row = specRows(offer.specs).find((entry) => entry.key === key);
    return row ? row.value : "Not on this record";
  };

  const header = (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs">
      <div>
        <h1 className="text-2xl sm:text-3xl font-black text-slate-900">Compare offers</h1>
        <p className="text-xs sm:text-sm text-slate-500 mt-1">
          Price, stock, delivery, returns and validity, read from each offer record.
        </p>
      </div>
      {compared.length > 0 ? (
        <Link
          href={shareHref}
          className="self-start px-4 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
        >
          Link to this comparison
        </Link>
      ) : null}
    </div>
  );

  if (candidates.length === 0 && !loading) {
    return (
      <div className="space-y-10 pb-16 max-w-7xl mx-auto">
        {header}
        <div className="bg-white rounded-3xl border border-slate-200 p-12 text-center space-y-4 shadow-xs">
          <h3 className="text-lg font-black text-slate-900">Nothing to compare yet</h3>
          <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
            Name the offers in the address as{" "}
            <span className="font-mono">/compare?offers=off_a,off_b</span>, or products as{" "}
            <span className="font-mono">/compare?products=prd_a,prd_b</span>. The Compare button on any
            offer card adds to the list held in this browser, which is used when the address names
            nothing.
          </p>
          <Link
            href="/search"
            className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs"
          >
            Find offers to compare &rarr;
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-10 pb-16 max-w-7xl mx-auto">
      {header}

      {credentialGap ? (
        <div className="bg-amber-50 border border-amber-200 rounded-3xl p-5 text-xs text-amber-950 space-y-1.5">
          <p className="font-black text-sm">Direct offer read unavailable to this browser</p>
          <p className="leading-relaxed">{CREDENTIAL_GAP_NOTE}</p>
        </div>
      ) : null}

      {loading ? (
        <div className="bg-white rounded-3xl border border-slate-200 p-12 text-center space-y-3 shadow-xs" aria-live="polite">
          <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#174c3c]" />
          <p className="text-sm font-bold text-slate-700">Resolving these offers&hellip;</p>
          <p className="text-[11px] text-slate-400">
            {candidates.length > 0 ? `${candidates.length} resolved so far` : "Reading the catalog"}
          </p>
        </div>
      ) : null}

      {/* Requested identifiers that produced no offer, with the reason available */}
      {!loading && rejected.length > 0 ? (
        <div className="bg-white rounded-3xl border border-slate-200 p-6 space-y-3">
          <h2 className="text-sm font-black text-slate-900">
            {rejected.length} requested {rejected.length === 1 ? "candidate" : "candidates"} could not be
            compared
          </h2>
          <ul className="space-y-2 text-xs">
            {rejected.map((candidate) => (
              <li
                key={`${candidate.kind}-${candidate.requested}`}
                className="rounded-2xl border border-slate-200 bg-slate-50 p-3 space-y-1"
              >
                <p className="font-mono font-bold text-slate-800">
                  {candidate.kind === "offer" ? "offer" : "product"} {candidate.requested}
                </p>
                <p className="text-slate-600 leading-relaxed">
                  {candidate.error
                    ? `${candidate.error.code}: ${candidate.error.message}`
                    : candidate.outsidePage
                    ? `No offer with this identifier was inside the page of ${LOOKUP_LIMIT} the catalog returned. There is no identifier-scoped lookup open to this browser, so this page cannot say whether one exists further down the ranking.`
                    : "The catalog returned no active, in-stock, unexpired offer for this identifier."}
                </p>
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-slate-500 leading-relaxed">
            The catalog reports the offers that matched a query and nothing about the ones it
            eliminated, so the reasons above are resolution failures rather than per-constraint
            rejections. Where a resolved offer fails a baseline condition -- expired, or holding no
            stock -- that is stated in its column.
          </p>
        </div>
      ) : null}

      {/* The matrix */}
      {!loading && compared.length > 0 ? (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-xs overflow-x-auto">
          <table className="w-full text-xs text-left border-collapse min-w-[640px]">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/80">
                <th className="p-4 sm:p-6 font-bold text-slate-400 uppercase tracking-wider text-[11px] w-1/4">
                  Offer
                </th>
                {compared.map((candidate) => {
                  const offer = candidate.offer;
                  const image =
                    typeof offer.image_url === "string" && offer.image_url.trim()
                      ? offer.image_url.trim()
                      : null;
                  return (
                    <th key={offer.offer_id} className="p-4 sm:p-6 w-1/4 align-top">
                      <div className="space-y-3">
                        <div className="h-32 w-full rounded-2xl overflow-hidden bg-slate-100 relative">
                          {image ? (
                            <img src={image} alt={offer.title} className="w-full h-full object-cover" />
                          ) : (
                            <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-slate-500">
                              <ImageOff className="h-5 w-5" />
                              <span className="text-[10px] font-semibold">No catalog image</span>
                            </div>
                          )}
                          <button
                            onClick={() => toggleCompare(offer.product_id)}
                            className="absolute top-2 right-2 w-6 h-6 bg-slate-900/70 hover:bg-slate-900 text-white rounded-full text-[10px] flex items-center justify-center"
                            title="Remove from the browser comparison list"
                          >
                            &times;
                          </button>
                        </div>
                        <div>
                          <span className="text-[10px] font-bold text-[#174c3c] uppercase">
                            {offer.category || "Uncategorised"}
                          </span>
                          <Link href={`/product/${encodeURIComponent(offer.product_id)}`}>
                            <h4 className="font-bold text-slate-900 text-xs leading-snug line-clamp-2 hover:text-[#174c3c]">
                              {offer.title}
                            </h4>
                          </Link>
                          <div
                            className="text-base font-black text-slate-900 mt-1"
                            data-amount-minor={offer.unit_price_minor}
                            data-currency={offer.currency}
                          >
                            {formatMinorToMajor(offer.unit_price_minor, offer.currency)}
                          </div>
                        </div>
                        <button
                          onClick={() =>
                            addToCart(exploreOfferToProductItem(offer, catalogSource), 1)
                          }
                          disabled={offer.available_stock <= 0 || isExpired(offer.expires_at, Date.now())}
                          className="w-full py-2 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded-xl shadow-xs transition-all inline-flex items-center justify-center gap-1.5"
                        >
                          <ShoppingBag className="h-3.5 w-3.5" />
                          Add to bag
                        </button>
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Unit price</td>
                {compared.map((candidate) => (
                  <td
                    key={candidate.offer.offer_id}
                    className="p-4 font-black text-slate-900 text-sm"
                    data-amount-minor={candidate.offer.unit_price_minor}
                    data-currency={candidate.offer.currency}
                  >
                    {formatMinorToMajor(candidate.offer.unit_price_minor, candidate.offer.currency)}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Price provenance</td>
                {compared.map((candidate) => (
                  <td
                    key={candidate.offer.offer_id}
                    className="p-4 text-slate-800 font-medium"
                    title={pricingSourceDetail(candidate.offer.pricing_source)}
                  >
                    {pricingSourceLabel(candidate.offer.pricing_source)}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Memory</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-slate-800 font-semibold">
                    {specValue(candidate.offer, "memory_gb")}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Storage</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-slate-800 font-medium">
                    {specValue(candidate.offer, "storage_gb")}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Weight</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-slate-800 font-medium">
                    {specValue(candidate.offer, "weight_grams")}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Delivery</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-emerald-700 font-bold">
                    {candidate.offer.delivery_days}{" "}
                    {candidate.offer.delivery_days === 1 ? "day" : "days"}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Return window</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-slate-700 font-medium">
                    {candidate.offer.return_period_days} days
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Stock</td>
                {compared.map((candidate) => (
                  <td
                    key={candidate.offer.offer_id}
                    className={`p-4 font-bold ${
                      candidate.offer.available_stock > 0 ? "text-slate-800" : "text-rose-700"
                    }`}
                  >
                    {stockLabel(candidate.offer.available_stock)}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Rating</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-slate-900 font-bold">
                    {candidate.offer.reviews_count > 0
                      ? `${candidate.offer.rating} / 5 (${candidate.offer.reviews_count})`
                      : "No ratings recorded"}
                  </td>
                ))}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Offer validity</td>
                {compared.map((candidate) => {
                  const expired = isExpired(candidate.offer.expires_at, Date.now());
                  return (
                    <td
                      key={candidate.offer.offer_id}
                      className={`p-4 font-medium ${expired ? "text-rose-700" : "text-slate-700"}`}
                    >
                      {expired ? "Expired \u2014 " : "Valid until "}
                      {readableInstant(candidate.offer.expires_at) || "not stated"}
                    </td>
                  );
                })}
              </tr>

              <tr>
                <td className="p-4 font-bold text-slate-500 bg-slate-50/40">Identity</td>
                {compared.map((candidate) => (
                  <td key={candidate.offer.offer_id} className="p-4 text-slate-500 font-mono text-[11px]">
                    {candidate.offer.offer_id}
                    <br />v{candidate.offer.offer_version} &middot; {candidate.offer.merchant_id}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}

      {!loading ? (
        <div className="rounded-3xl border border-slate-200 bg-slate-50 p-5 text-[11px] text-slate-500 space-y-1">
          <p title={catalogSourceDetail(catalogSource)}>
            <span className="font-bold text-slate-700">Catalog that answered: </span>
            {catalogSourceLabel(catalogSource)}.
          </p>
          <p>
            <span className="font-bold text-slate-700">Not shown: </span>a recommendation about which
            offer is better. No endpoint produces one, and this page will not synthesise a verdict from
            figures it merely displayed.
          </p>
          <button
            type="button"
            onClick={reload}
            className="mt-1 font-bold text-[#174c3c] hover:underline"
          >
            Re-resolve these offers
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <div className="max-w-7xl mx-auto py-12 text-center text-slate-500 text-sm">
          Preparing comparison&hellip;
        </div>
      }
    >
      <CompareContent />
    </Suspense>
  );
}
