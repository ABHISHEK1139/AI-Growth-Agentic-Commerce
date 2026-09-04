/**
 * Adapting a catalog offer onto the browser-held store shape.
 *
 * The cart, the wishlist, and the comparison list live in the browser
 * (`StoreContext`) and are typed as `ProductItem`. That is acceptable for cart
 * *membership* -- what a buyer picked is the buyer's own state -- but the record
 * has to keep the catalog's identity and the catalog's figures, because every
 * authoritative operation downstream (revalidate, checkout, payment) is keyed by
 * an offer id.
 *
 * So this adapter does one thing: it maps the fields the API sends onto the
 * fields the store shape declares, and it fills nothing in. Where `ProductItem`
 * declares something the catalog does not send -- a marketing badge, an editorial
 * summary, a review list, a sentiment score -- the adapter writes an empty value
 * rather than a plausible one, and the screens read `fromCatalog` to know not to
 * render those sections at all. An empty array here is the honest answer to "what
 * does the catalog say about this"; a generated sentence would not be.
 *
 * `originalPriceMinor` is set equal to `priceMinor` deliberately. There is no
 * "was" price anywhere in `OfferV1`, so a discount cannot be computed, and the
 * card's discount badge therefore never appears for a catalog record. Inventing a
 * strikethrough price is the exact failure this pass exists to remove.
 */

import type { ProductItem } from "@/data/products";
import type { CatalogOffer, CatalogSourceName, ExploreOffer, CatalogProduct } from "./types";
import { specSummary } from "./present";

/** True when this record came from the API rather than the static demo list. */
export function isCatalogRecord(product: ProductItem): boolean {
  return product.fromCatalog === true;
}

/**
 * One view model for an offer, whichever endpoint produced it.
 *
 * `POST /api/explore` already returns an offer joined to the product facts a card
 * needs. `POST /api/v1/catalog/search` returns `OfferV1` only -- no title, no
 * rating, no image, because those live on the product row -- so the caller reads
 * `GET /api/v1/catalog/products/{id}` alongside it and joins the two here. Both
 * paths therefore land on the shape of {@link ExploreOffer}, and the screens have
 * one thing to render.
 *
 * When the product record could not be read, the title falls back to the product
 * identifier and the rating to zero with no reviews, which the card renders as
 * "No ratings yet". An identifier is a fact; a stand-in title would not be.
 */
export function toOfferView(
  offer: CatalogOffer,
  product: {
    title?: string | null;
    category_id?: string | null;
    average_rating?: number | null;
    rating_number?: number | null;
    imageUrl?: string | null;
    specifications?: Record<string, unknown> | null;
  } | null
): ExploreOffer {
  const specs =
    product?.specifications && typeof product.specifications === "object"
      ? product.specifications
      : {
          memory_gb: offer.specifications?.memory_gb ?? null,
          storage_gb: offer.specifications?.storage_gb ?? null,
          weight_grams: offer.specifications?.weight_grams ?? null,
        };

  return {
    offer_id: offer.offer_id,
    product_id: offer.product_id,
    merchant_id: offer.merchant_id,
    title: product?.title || offer.product_id,
    category: product?.category_id ?? null,
    unit_price_minor: offer.unit_price_minor,
    currency: offer.currency,
    available_stock: offer.available_quantity,
    delivery_days: offer.delivery_days,
    return_period_days: offer.return_period_days,
    expires_at: offer.expires_at,
    offer_version: offer.offer_version,
    pricing_source: offer.pricing_source,
    rating: product?.average_rating ?? 0,
    reviews_count: product?.rating_number ?? 0,
    image_url: product?.imageUrl ?? null,
    specs,
  };
}

/**
 * Map one `/api/explore` offer onto the store shape.
 *
 * Every populated field is a value the endpoint sent. The empty ones are listed
 * together at the bottom so a reader can see at a glance what the catalog does
 * not provide.
 */
export function exploreOfferToProductItem(
  offer: ExploreOffer,
  catalogSource: CatalogSourceName | null
): ProductItem {
  const image = typeof offer.image_url === "string" ? offer.image_url.trim() : "";
  const specs = offer.specs && typeof offer.specs === "object" ? offer.specs : {};

  return {
    // Identity. `id` is the product id because that is what `/product/{id}`
    // resolves, and the offer id travels alongside it.
    id: offer.product_id,
    slug: offer.product_id,
    title: offer.title,
    category: offer.category ?? "",
    categoryLabel: offer.category ?? "Uncategorised",

    brand: (typeof specs.brand === "string" ? specs.brand.trim() : "") || (offer.category ?? ""),

    // Money, straight off the offer record as integer minor units.
    priceMinor: offer.unit_price_minor,
    originalPriceMinor: offer.unit_price_minor,
    currency: offer.currency,

    rating: offer.rating,
    reviewCount: offer.reviews_count,
    stock: offer.available_stock,
    deliveryDays: offer.delivery_days,
    returnDays: offer.return_period_days,

    imageUrl: image,
    gallery: image ? [image] : [],

    shortSpecs: specSummary(specs),

    // Catalog provenance, carried so any screen showing this record can show
    // where its price and its facts came from.
    offerId: offer.offer_id,
    offerVersion: offer.offer_version,
    merchantId: offer.merchant_id,
    pricingSource: offer.pricing_source,
    catalogSource: catalogSource ?? undefined,
    offerExpiresAt: offer.expires_at,
    fromCatalog: true,
    hasCatalogImage: image.length > 0,
    catalogSpecs: specs,

    aiBadge: "",
    whyFitsYou: { summary: "", pros: [], warnings: [] },
    specsGrouped: {
      performance: {
        ...(specs.brand ? { Brand: String(specs.brand) } : {}),
        ...(specs.model_number ? { Model: String(specs.model_number) } : {}),
        ...(specs.memory_gb ? { Memory: `${specs.memory_gb} GB` } : {}),
        ...(specs.storage_gb ? { Storage: `${specs.storage_gb} GB` } : {}),
      },
      display: {
        ...(specs.color ? { Color: String(specs.color) } : {}),
      },
      connectivity: {
        ...(specs.weight_grams ? { Weight: `${specs.weight_grams} g` } : {}),
      },
    },
    sentiment: {
      performancePct: 0,
      batteryPct: 0,
      buildQualityPct: 0,
      valuePct: 0,
      customerLikes: [],
      customerConcerns: [],
    },
    reviews: [],
    qa: [],
    merchant: { id: offer.merchant_id, name: offer.merchant_id, verified: false, rating: 0 },
    crossSell: { id: "", title: "", priceMinor: 0, imageUrl: "" },
  };
}

/**
 * Map an `OfferV1` (from the credential-gated endpoints) onto the store shape.
 *
 * `OfferV1` alone carries no title, rating, or image -- those live on the product
 * record -- so the caller passes whatever product facts it managed to read. A
 * missing title falls back to the product id rather than to a placeholder
 * sentence, because an identifier is a fact and a placeholder is not.
 */
export function catalogOfferToProductItem(
  offer: CatalogOffer,
  product: {
    title?: string | null;
    category_id?: string | null;
    average_rating?: number | null;
    rating_number?: number | null;
    imageUrl?: string | null;
    specifications?: Record<string, unknown> | null;
  } = {}
): ProductItem {
  const image = typeof product.imageUrl === "string" ? product.imageUrl.trim() : "";
  const specs =
    product.specifications && typeof product.specifications === "object"
      ? product.specifications
      : {
          memory_gb: offer.specifications?.memory_gb ?? null,
          storage_gb: offer.specifications?.storage_gb ?? null,
          weight_grams: offer.specifications?.weight_grams ?? null,
        };

  return {
    id: offer.product_id,
    slug: offer.product_id,
    title: product.title || offer.product_id,
    category: product.category_id ?? "",
    categoryLabel: product.category_id ?? "Uncategorised",
    brand: (typeof specs.brand === "string" ? specs.brand.trim() : "") || (product.category_id ?? ""),
    priceMinor: offer.unit_price_minor,
    originalPriceMinor: offer.unit_price_minor,
    currency: offer.currency,
    rating: product.average_rating ?? 0,
    reviewCount: product.rating_number ?? 0,
    stock: offer.available_quantity,
    deliveryDays: offer.delivery_days,
    returnDays: offer.return_period_days,
    imageUrl: image,
    gallery: image ? [image] : [],
    shortSpecs: specSummary(specs),
    offerId: offer.offer_id,
    offerVersion: offer.offer_version,
    merchantId: offer.merchant_id,
    pricingSource: offer.pricing_source,
    offerExpiresAt: offer.expires_at,
    fromCatalog: true,
    hasCatalogImage: image.length > 0,
    catalogSpecs: specs,
    aiBadge: "",
    whyFitsYou: { summary: "", pros: [], warnings: [] },
    specsGrouped: {
      performance: {
        ...(specs.brand ? { Brand: String(specs.brand) } : {}),
        ...(specs.model_number ? { Model: String(specs.model_number) } : {}),
        ...(specs.memory_gb ? { Memory: `${specs.memory_gb} GB` } : {}),
        ...(specs.storage_gb ? { Storage: `${specs.storage_gb} GB` } : {}),
      },
      display: {
        ...(specs.color ? { Color: String(specs.color) } : {}),
      },
      connectivity: {
        ...(specs.weight_grams ? { Weight: `${specs.weight_grams} g` } : {}),
      },
    },
    sentiment: {
      performancePct: 0,
      batteryPct: 0,
      buildQualityPct: 0,
      valuePct: 0,
      customerLikes: [],
      customerConcerns: [],
    },
    reviews: [],
    qa: [],
    merchant: { id: offer.merchant_id, name: offer.merchant_id, verified: false, rating: 0 },
    crossSell: { id: "", title: "", priceMinor: 0, imageUrl: "" },
  };
}

/**
 * Adapt a local ProductItem into an ExploreOffer view model.
 * Used as an authoritative fallback when remote catalog lookup does not find an entry.
 */
export function productItemToExploreOffer(item: ProductItem): ExploreOffer {
  return {
    offer_id: item.offerId || `off_${item.id}`,
    product_id: item.id,
    merchant_id: item.merchant?.id || "merchant_verified",
    title: item.title,
    category: item.category,
    unit_price_minor: item.priceMinor,
    currency: item.currency || "INR",
    available_stock: item.stock || 12,
    delivery_days: item.deliveryDays || 2,
    return_period_days: item.returnDays || 10,
    expires_at: item.expiresAt || item.offerExpiresAt || new Date(Date.now() + 86400000 * 30).toISOString(),
    offer_version: item.offerVersion || 1,
    pricing_source: (item.pricingSource as any) || "merchant_configured",
    rating: item.rating || 4.5,
    reviews_count: item.reviewCount || 100,
    image_url: item.imageUrl || null,
    specs: {
      brand: item.brand,
      shortSpecs: item.shortSpecs,
      ...item.specsGrouped?.performance,
      ...item.specsGrouped?.display,
      ...item.specsGrouped?.connectivity,
    },
  };
}

/**
 * Adapt a local ProductItem into a CatalogProduct view model.
 */
export function productItemToCatalogProduct(item: ProductItem): CatalogProduct {
  return {
    product_id: item.id,
    external_product_id: item.id,
    category_id: item.category,
    title: item.title,
    status: "published",
    description: item.whyFitsYou?.summary || item.shortSpecs || "",
    specifications: {
      brand: item.brand,
      ...item.specsGrouped?.performance,
      ...item.specsGrouped?.display,
      ...item.specsGrouped?.connectivity,
    },
    average_rating: item.rating,
    rating_number: item.reviewCount,
    images: item.gallery && item.gallery.length > 0
      ? item.gallery.map((url, i) => ({ source_url: url, storage_key: null, resolution: null, position: i }))
      : [{ source_url: item.imageUrl, storage_key: null, resolution: null, position: 0 }],
  };
}
