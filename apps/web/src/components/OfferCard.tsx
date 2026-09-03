"use client";

import Link from "next/link";
import { useState } from "react";
import { Heart, ImageOff, Scale, ShoppingBag, Star, Tag, Truck } from "lucide-react";
import { formatMinorToMajor } from "@/lib/money";
import { useStore } from "@/context/StoreContext";
import { exploreOfferToProductItem } from "@/catalog/adapt";
import { pricingSourceLabel, pricingSourceDetail, stockLabel } from "@/catalog/present";
import type { CatalogSourceName, ExploreOffer } from "@/catalog/types";

/**
 * A product card driven by a live catalog offer.
 *
 * The same card as `ProductCard` visually -- identical Tailwind classes, the same
 * `#174c3c` primary, the same rounded shell and two-button footer -- but every
 * value on it comes from an offer record, and four things behave differently
 * because the catalog does not hold what the static list held:
 *
 * * **No discount badge.** `OfferV1` carries no previous price, so no percentage
 *   can be computed and no strikethrough is drawn.
 * * **No brand slot.** There is no brand field; the category the catalog stores
 *   takes that position.
 * * **The badge is provenance, not marketing.** It says whether the price was
 *   configured by the merchant or generated inside a band.
 * * **A missing image is labelled.** An offer with no `image_url`, or a URL the
 *   browser fails to load, gets a stated placeholder rather than a broken frame.
 *
 * The amount carries `data-amount-minor` and `data-currency` so the rendered
 * figure can be checked against the integer the API sent.
 */
export function OfferCard({
  offer,
  catalogSource = null,
  isBestMatch,
  highlightReason,
}: {
  offer: ExploreOffer;
  catalogSource?: CatalogSourceName | null;
  isBestMatch?: boolean;
  highlightReason?: string;
}) {
  const { addToCart, wishlist, toggleWishlist, compareList, toggleCompare } = useStore();
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageBroken, setImageBroken] = useState(false);
  const [addedToCart, setAddedToCart] = useState(false);

  const saved = wishlist.includes(offer.product_id);
  const compared = compareList.includes(offer.product_id);
  const outOfStock = offer.available_stock <= 0;
  const imageUrl = typeof offer.image_url === "string" ? offer.image_url.trim() : "";
  const showImage = imageUrl.length > 0 && !imageBroken;

  const handleAddToCart = () => {
    addToCart(exploreOfferToProductItem(offer, catalogSource), 1);
    setAddedToCart(true);
    setTimeout(() => setAddedToCart(false), 1500);
  };

  return (
    <article
      className={`group relative flex h-full flex-col overflow-hidden rounded-[22px] border bg-white transition-all duration-300 hover:-translate-y-1.5 hover:shadow-hover ${
        isBestMatch
          ? "border-[#174c3c] ring-1 ring-[#174c3c] shadow-soft"
          : "border-[#e6e8df] hover:border-[#c8d4cc]"
      }`}
      data-offer-id={offer.offer_id}
      data-product-id={offer.product_id}
    >
      {/* Image, or a stated absence */}
      <div className="relative aspect-[1.1] overflow-hidden bg-[#eef1eb]">
        {showImage ? (
          <>
            {!imageLoaded && <div className="absolute inset-0 skeleton-pulse" />}
            <Link href={`/product/${encodeURIComponent(offer.product_id)}`} className="block h-full">
              <img
                src={imageUrl}
                alt={offer.title}
                className={`h-full w-full object-cover transition-all duration-500 ease-out group-hover:scale-110 ${
                  imageLoaded ? "opacity-100" : "opacity-0"
                }`}
                onLoad={() => setImageLoaded(true)}
                onError={() => setImageBroken(true)}
              />
            </Link>
          </>
        ) : (
          <Link
            href={`/product/${encodeURIComponent(offer.product_id)}`}
            className="flex h-full w-full flex-col items-center justify-center gap-2 text-center text-[#68736d]"
          >
            <ImageOff className="h-6 w-6" />
            <span className="px-4 text-[11px] font-semibold leading-4">
              {imageUrl ? "Catalog image failed to load" : "No image in the catalog record"}
            </span>
          </Link>
        )}

        <div className="absolute left-3 top-3 flex flex-col items-start gap-1.5">
          {isBestMatch && (
            <span className="rounded-full bg-[#174c3c] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-white shadow-sm animate-fade-in">
              Top ranked
            </span>
          )}
          {outOfStock && (
            <span className="rounded-full bg-white/95 px-2.5 py-1 text-[10px] font-bold text-[#c65027] shadow-sm backdrop-blur-sm">
              Out of stock
            </span>
          )}
        </div>

        <button
          onClick={() => toggleWishlist(offer.product_id)}
          className={`absolute right-3 top-3 grid h-9 w-9 place-items-center rounded-full bg-white/95 shadow-sm backdrop-blur-sm transition-all duration-200 hover:scale-110 active:scale-90 ${
            saved ? "text-[#c65027]" : "text-[#526058] hover:text-[#c65027]"
          }`}
          aria-label={saved ? "Remove from saved products" : "Save product"}
        >
          <Heart className={`h-4 w-4 transition-transform duration-200 ${saved ? "fill-current scale-110" : ""}`} />
        </button>

        <div className="absolute bottom-3 left-3 right-3 translate-y-2 opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100">
          <Link
            href={`/product/${encodeURIComponent(offer.product_id)}`}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-white/90 py-2.5 text-[11px] font-bold text-[#174c3c] backdrop-blur-md transition-all duration-200 hover:bg-white hover:shadow-md"
          >
            Quick view
          </Link>
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 flex-col p-4 sm:p-5">
        <div className="mb-2 flex items-center justify-between gap-3 text-[11px] font-semibold">
          <span className="uppercase tracking-[.12em] text-[#174c3c]">
            {offer.category || "Uncategorised"}
          </span>
          <span className="inline-flex items-center gap-1 text-[#526058]">
            {offer.reviews_count > 0 ? (
              <>
                <Star className="h-3.5 w-3.5 fill-[#e8a33e] text-[#e8a33e]" />
                {offer.rating}
              </>
            ) : (
              "No ratings yet"
            )}
          </span>
        </div>

        <Link href={`/product/${encodeURIComponent(offer.product_id)}`}>
          <h3 className="line-clamp-2 min-h-[42px] text-sm font-bold leading-5 text-[#17231e] transition-colors duration-200 group-hover:text-[#174c3c]">
            {offer.title}
          </h3>
        </Link>

        <p className="mt-1.5 truncate text-xs text-[#68736d]">
          {highlightReason || stockLabel(offer.available_stock)}
        </p>

        <div className="mt-3 flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-[#e5f0e9] px-2.5 py-0.5 text-[10px] font-bold text-[#174c3c]">
            <Truck className="h-3 w-3" />
            {offer.delivery_days <= 2 ? "⚡ Express Delivery" : `${offer.delivery_days}-Day Delivery`}
          </span>
          {offer.available_stock > 0 && offer.available_stock <= 5 && (
            <span className="rounded-full bg-amber-50 px-2.5 py-0.5 text-[10px] font-bold text-amber-800 border border-amber-200">
              Only {offer.available_stock} left
            </span>
          )}
        </div>

        <div className="mt-4 flex items-end justify-between border-t border-[#edf0ea] pt-4">
          <div>
            <p
              className="text-lg font-extrabold tracking-tight text-[#17231e]"
              data-amount-minor={offer.unit_price_minor}
              data-currency={offer.currency}
            >
              {formatMinorToMajor(offer.unit_price_minor, offer.currency)}
            </p>
            <p className="text-[11px] text-[#8a938e]">per unit &middot; total shown at checkout</p>
          </div>
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-[#526058]">
            <Truck className="h-3.5 w-3.5" /> {offer.delivery_days}{" "}
            {offer.delivery_days === 1 ? "day" : "days"}
          </span>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <button
            onClick={() => toggleCompare(offer.product_id)}
            className={`inline-flex h-9 items-center justify-center gap-1 rounded-xl border text-xs font-bold transition-all duration-200 active:scale-95 ${
              compared
                ? "border-[#174c3c] bg-[#e5f0e9] text-[#174c3c]"
                : "border-[#dfe4dd] text-[#526058] hover:border-[#174c3c] hover:bg-[#f0f7f3]"
            }`}
          >
            <Scale className="h-3.5 w-3.5" />
            {compared ? "Added" : "Compare"}
          </button>
          <button
            onClick={handleAddToCart}
            disabled={outOfStock}
            className={`inline-flex h-9 items-center justify-center gap-1 rounded-xl text-xs font-bold transition-all duration-200 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 ${
              addedToCart ? "bg-[#1d8c5c] text-white" : "bg-[#174c3c] text-white hover:bg-[#103c2f] hover:shadow-md"
            }`}
          >
            <ShoppingBag className="h-3.5 w-3.5" />
            {outOfStock ? "Unavailable" : addedToCart ? "Added!" : "Add"}
          </button>
        </div>
      </div>
    </article>
  );
}
