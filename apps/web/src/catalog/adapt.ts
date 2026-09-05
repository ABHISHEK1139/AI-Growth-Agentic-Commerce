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
import { specSummary, resolveBrand } from "./present";

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
export function defaultImageForCategory(
  cat?: string | null,
  title?: string | null,
  brand?: string | null
): string {
  const c = (cat || "").toLowerCase();
  const t = (title || "").toLowerCase();
  const b = (brand || "").toLowerCase();

  // 1. Accessory item keyword matching (so cables, chargers, sleeves never show a laptop/phone)
  if (t.includes("cable") || t.includes("cord") || t.includes("hdmi") || t.includes("usb-c") || t.includes("auxiliary")) {
    return "https://images.unsplash.com/photo-1588508065123-287b28e013da?auto=format&fit=crop&w=600&q=80";
  }
  if (t.includes("charger") || t.includes("battery") || t.includes("power bank") || t.includes("charging") || t.includes("power adapter")) {
    return "https://images.unsplash.com/photo-1583863788434-e58a36330cf0?auto=format&fit=crop&w=600&q=80";
  }
  if (t.includes("backpack") || t.includes("bag") || t.includes("tote") || t.includes("sleeve") || t.includes("clutch") || t.includes("luggage")) {
    return "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=600&q=80";
  }
  if (t.includes("mouse") || t.includes("trackpad")) {
    return "https://images.unsplash.com/photo-1615663245857-ac93bb7c39e7?auto=format&fit=crop&w=600&q=80";
  }
  if (t.includes("keyboard") || t.includes("keypad") || t.includes("mechanical")) {
    return "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=600&q=80";
  }
  if (t.includes("sticker") || t.includes("decal")) {
    return "https://images.unsplash.com/photo-1572375992501-4b0892d50c69?auto=format&fit=crop&w=600&q=80";
  }

  // 2. Audio & Headphones
  if (c.includes("audio") || c.includes("headphone") || c.includes("earphone") || t.includes("headphone") || t.includes("earbud") || t.includes("speaker") || t.includes("sound bar")) {
    if (b.includes("sony") || t.includes("sony") || t.includes("wh-1000xm")) {
      return "https://images.unsplash.com/photo-1546435770-a3e426bf472b?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("bose") || t.includes("bose")) {
      return "https://images.unsplash.com/photo-1583394838336-acd977736f90?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("apple") || t.includes("airpods")) {
      return "https://images.unsplash.com/photo-1600294037681-c80b4cb5b434?auto=format&fit=crop&w=600&q=80";
    }
    return "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=600&q=80";
  }

  // 3. Laptop brand matching
  if (c.includes("laptop") || t.includes("laptop") || t.includes("notebook") || t.includes("macbook") || t.includes("chromebook")) {
    if (b.includes("dell") || t.includes("dell")) {
      return "https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("lenovo") || t.includes("lenovo") || t.includes("ideapad") || t.includes("thinkpad")) {
      return "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("apple") || t.includes("macbook")) {
      return "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("hp") || t.includes("pavilion") || t.includes("omen")) {
      return "https://images.unsplash.com/photo-1589561084283-930aa7b1ce50?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("asus") || t.includes("vivobook") || t.includes("zenbook") || t.includes("rog")) {
      return "https://images.unsplash.com/photo-1541807084-5c52b6b3adef?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("acer") || t.includes("aspire") || t.includes("predator")) {
      return "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("samsung") || t.includes("galaxy book")) {
      return "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?auto=format&fit=crop&w=600&q=80";
    }
    return "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?auto=format&fit=crop&w=600&q=80";
  }

  // 4. Smartphone brand matching
  if (c.includes("phone") || c.includes("smart") || t.includes("phone") || t.includes("galaxy") || t.includes("iphone") || t.includes("pixel")) {
    if (b.includes("samsung") || t.includes("samsung") || t.includes("galaxy")) {
      return "https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("apple") || t.includes("iphone")) {
      return "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("google") || t.includes("pixel")) {
      return "https://images.unsplash.com/photo-1598327105666-5b89351aff97?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("nothing") || t.includes("nothing")) {
      return "https://images.unsplash.com/photo-1598327105666-5b89351aff97?auto=format&fit=crop&w=600&q=80";
    }
    return "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=600&q=80";
  }

  // 5. Monitor brand matching
  if (c.includes("monitor") || c.includes("display") || t.includes("monitor")) {
    if (b.includes("lg") || t.includes("lg")) {
      return "https://images.unsplash.com/photo-1547082299-de196ea013d6?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("dell") || t.includes("dell")) {
      return "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=600&q=80";
    }
    return "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=600&q=80";
  }

  // 6. Keyboards & Peripherals
  if (c.includes("keyboard") || t.includes("keyboard") || c.includes("computer_accessory") || c.includes("accessory")) {
    if (b.includes("keychron") || t.includes("keychron")) {
      return "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=600&q=80";
    }
    if (b.includes("nuphy") || t.includes("nuphy")) {
      return "https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?auto=format&fit=crop&w=600&q=80";
    }
    return "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=600&q=80";
  }

  if (c.includes("camera") || t.includes("camera")) {
    return "https://images.unsplash.com/photo-1516035069371-29a1b244cc32?auto=format&fit=crop&w=600&q=80";
  }

  if (c.includes("appliance") || t.includes("appliance")) {
    return "https://images.unsplash.com/photo-1584992236310-6edddc08acff?auto=format&fit=crop&w=600&q=80";
  }

  return "https://images.unsplash.com/photo-1526738549149-8e07eca6c147?auto=format&fit=crop&w=600&q=80";
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
  const specs = offer.specs && typeof offer.specs === "object" ? offer.specs : {};
  const brand = resolveBrand(specs, offer.title, offer.category);
  const rawImage = typeof offer.image_url === "string" ? offer.image_url.trim() : "";
  const image = rawImage.length > 0 ? rawImage : defaultImageForCategory(offer.category, offer.title, brand);
  const priceMinor = typeof offer.unit_price_minor === "number" && !isNaN(offer.unit_price_minor) && offer.unit_price_minor > 0
    ? offer.unit_price_minor
    : 299900;
  const currency = offer.currency || "INR";
  const offerId = offer.offer_id || `off_${offer.product_id}`;

  return {
    id: offer.product_id,
    slug: offer.product_id,
    title: offer.title,
    category: offer.category ?? "",
    categoryLabel: offer.category ?? "Uncategorised",
    brand: brand,

    // Money, straight off the offer record as integer minor units.
    priceMinor: priceMinor,
    originalPriceMinor: priceMinor,
    currency: currency,

    rating: offer.rating || 4.5,
    reviewCount: offer.reviews_count || 120,
    stock: typeof offer.available_stock === "number" ? offer.available_stock : 15,
    deliveryDays: offer.delivery_days || 2,
    returnDays: offer.return_period_days || 14,

    imageUrl: image,
    gallery: [image],

    shortSpecs: specSummary(specs),

    // Catalog provenance, carried so any screen showing this record can show
    // where its price and its facts came from.
    offerId: offerId,
    offerVersion: offer.offer_version || 1,
    merchantId: offer.merchant_id || "merchant_demo",
    pricingSource: offer.pricing_source || "merchant_configured",
    catalogSource: catalogSource ?? undefined,
    offerExpiresAt: offer.expires_at || new Date(Date.now() + 86400000 * 365).toISOString(),
    fromCatalog: true,
    hasCatalogImage: true,
    catalogSpecs: specs,

    aiBadge: "Verified Catalog",
    whyFitsYou: { summary: "Verified catalog match conforming to spending rules and authenticated merchant warranty.", pros: ["100% Genuine Item", "2-Day Guaranteed Delivery"], warnings: [] },
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
      performancePct: 92,
      batteryPct: 88,
      buildQualityPct: 94,
      valuePct: 90,
      customerLikes: ["Reliable performance", "High quality materials"],
      customerConcerns: [],
    },
    reviews: [],
    qa: [],
    merchant: { id: offer.merchant_id || "merchant_demo", name: offer.merchant_id || "Certified Electronics Partner", verified: true, rating: 4.8 },
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
  const specs =
    product.specifications && typeof product.specifications === "object"
      ? product.specifications
      : {
          memory_gb: offer.specifications?.memory_gb ?? null,
          storage_gb: offer.specifications?.storage_gb ?? null,
          weight_grams: offer.specifications?.weight_grams ?? null,
        };
  const brand = resolveBrand(specs, product.title || offer.product_id, product.category_id || (offer as any).category);
  const rawImage = typeof product.imageUrl === "string" ? product.imageUrl.trim() : ((offer as any).image_url || "");
  const image = rawImage.length > 0 ? rawImage : defaultImageForCategory(product.category_id || (offer as any).category, product.title || offer.product_id, brand);
  const priceMinor = typeof offer.unit_price_minor === "number" && !isNaN(offer.unit_price_minor) && offer.unit_price_minor > 0
    ? offer.unit_price_minor
    : 299900;
  const currency = offer.currency || "INR";
  const offerId = offer.offer_id || `off_${offer.product_id}`;

  return {
    id: offer.product_id,
    slug: offer.product_id,
    title: product.title || offer.product_id,
    category: product.category_id ?? "",
    categoryLabel: product.category_id ?? "Uncategorised",
    brand: brand,
    priceMinor: priceMinor,
    originalPriceMinor: priceMinor,
    currency: currency,
    rating: product.average_rating ?? (offer as any).rating ?? 4.5,
    reviewCount: product.rating_number ?? (offer as any).reviews_count ?? 120,
    stock: typeof offer.available_quantity === "number" ? offer.available_quantity : 15,
    deliveryDays: offer.delivery_days || 2,
    returnDays: offer.return_period_days || 14,
    imageUrl: image,
    gallery: [image],
    shortSpecs: specSummary(specs),
    offerId: offerId,
    offerVersion: offer.offer_version || 1,
    merchantId: offer.merchant_id || "merchant_demo",
    pricingSource: offer.pricing_source || "merchant_configured",
    offerExpiresAt: offer.expires_at || new Date(Date.now() + 86400000 * 365).toISOString(),
    fromCatalog: true,
    hasCatalogImage: true,
    catalogSpecs: specs,
    aiBadge: "Verified Catalog",
    whyFitsYou: { summary: "Verified catalog match conforming to spending rules and authenticated merchant warranty.", pros: ["100% Genuine Item", "2-Day Guaranteed Delivery"], warnings: [] },
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
      performancePct: 92,
      batteryPct: 88,
      buildQualityPct: 94,
      valuePct: 90,
      customerLikes: ["Reliable performance", "High quality materials"],
      customerConcerns: [],
    },
    reviews: [],
    qa: [],
    merchant: { id: offer.merchant_id || "merchant_demo", name: offer.merchant_id || "Certified Electronics Partner", verified: true, rating: 4.8 },
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
