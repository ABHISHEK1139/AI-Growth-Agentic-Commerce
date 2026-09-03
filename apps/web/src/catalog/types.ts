/**
 * The catalog wire shapes, transcribed from the API rather than invented.
 *
 * Every interface here mirrors one server-side declaration, and the comment on
 * each names the file it was read from. A buyer screen that needs a field this
 * module does not carry is looking at a field the API does not send, which is a
 * gap to report rather than a value to fill in.
 *
 * Sources:
 *   `packages/schemas/v1.py`            -- OfferV1, ProductSpecificationsV1, PriceBreakdownV1, CheckoutV1
 *   `apps/api/routers/catalog.py`       -- POST /api/v1/catalog/search, GET /api/v1/catalog/products/{id}
 *   `apps/api/routers/explore.py`       -- POST /api/explore
 *   `apps/api/routers/research.py`      -- POST /api/v1/research/ask
 *   `apps/api/routers/recommendations.py` -- POST /api/v1/recommendations/cross-sell
 *   `services/offers/constraints.py`    -- the filter set and the ranking
 */

/** `ProductSpecificationsV1`. A null is "the catalog does not hold this fact". */
export interface OfferSpecifications {
  memory_gb: number | null;
  storage_gb: number | null;
  weight_grams: number | null;
  length_mm: number | null;
  width_mm: number | null;
  height_mm: number | null;
}

/**
 * Where a price came from. Carried on every offer for exactly one reason: a
 * price in this system is generated inside a band or configured by the merchant,
 * and it is never scraped from a market. The buyer surface says which.
 */
export type PricingSource = "synthetic_band_random" | "merchant_configured";

/** Which catalog answered a search (`apps/api/catalog_source.py`). */
export type CatalogSourceName = "postgresql" | "seed_fixture";

/** `OfferV1`. Money is an integer minor unit; nothing here is a float. */
export interface CatalogOffer {
  schema_version: string;
  offer_id: string;
  product_id: string;
  merchant_id: string;
  status: "active" | "inactive" | "expired" | "needs_review";
  unit_price_minor: number;
  currency: string;
  available_quantity: number;
  delivery_days: number;
  return_period_days: number;
  expires_at: string;
  offer_version: number;
  pricing_source: PricingSource;
  specifications: OfferSpecifications;
}

/** One image row from `GET /api/v1/catalog/products/{id}`. */
export interface CatalogProductImage {
  source_url: string | null;
  storage_key: string | null;
  resolution: string | null;
  position: number | null;
}

/**
 * The `product` object of `GET /api/v1/catalog/products/{id}`.
 *
 * `description` is typed as unknown on purpose: the column holds whatever the
 * importer wrote, and the seed artifacts write a list of paragraphs while a
 * hand-written row may hold a single string. The screen normalises it rather
 * than assuming.
 */
export interface CatalogProduct {
  product_id: string;
  external_product_id: string | null;
  category_id: string | null;
  title: string;
  status: string | null;
  description: unknown;
  specifications: Record<string, unknown> | null;
  average_rating: number | null;
  rating_number: number | null;
  images: CatalogProductImage[];
}

/**
 * One entry of the `products` array of `POST /api/explore`.
 *
 * This is an offer joined to the product facts a buyer surface needs, projected
 * by `_offer_payload` in `apps/api/routers/explore.py`. It is the only offer
 * projection reachable without a catalog-read credential.
 */
export interface ExploreOffer {
  offer_id: string;
  product_id: string;
  merchant_id: string;
  title: string;
  category: string | null;
  unit_price_minor: number;
  currency: string;
  available_stock: number;
  delivery_days: number;
  return_period_days: number;
  expires_at: string;
  offer_version: number;
  pricing_source: PricingSource;
  rating: number;
  reviews_count: number;
  image_url: string | null;
  specs: Record<string, unknown>;
}

/** The `intent` object of an explore response. Every field may be null. */
export interface ExploreIntent {
  query: string | null;
  category: string | null;
  budget_minor: number | null;
  currency: string | null;
  min_memory_gb: number | null;
  min_storage_gb: number | null;
  max_delivery_days: number | null;
  quantity: number | null;
}

export interface ExploreEvidence {
  claim: string;
  citation_type: string | null;
  source_url: string | null;
  confidence: number | null;
}

/**
 * `POST /api/explore`.
 *
 * Note that this endpoint answers with a *flat* body, not the standard success
 * envelope, which is why `catalog/client.ts` reads it through its own reader
 * instead of `apiPost`.
 */
export interface ExploreResponse {
  ok: boolean;
  guard_blocked: boolean;
  threat?: string | null;
  evaluator?: string | null;
  message?: string | null;
  intent: ExploreIntent | null;
  products: ExploreOffer[];
  count?: number;
  catalog_source?: CatalogSourceName;
  applied_filters?: string[];
  warnings?: string[];
  research?: {
    evidence: ExploreEvidence[];
    product_id: string | null;
    source_count: number;
  };
}

/** The `data` of `POST /api/v1/catalog/search` (and `POST /api/v1/offers/query`). */
export interface CatalogSearchData {
  offers: CatalogOffer[];
  count: number;
}

/**
 * One citation attached to a research answer.
 *
 * Every key is optional because the orchestrator builds these dictionaries at
 * several call sites with different keys: a catalog fact carries
 * `citation_type`, an external document carries `source_type` and a confidence.
 * A screen renders what is present and claims nothing about what is not.
 */
export interface ResearchEvidenceItem {
  claim?: string | null;
  citation_type?: string | null;
  source_type?: string | null;
  source_url?: string | null;
  confidence_level?: string | null;
  confidence_score?: number | null;
}

/** `POST /api/v1/research/ask`. Also a flat body rather than an envelope. */
export interface ResearchAnswer {
  ok: boolean;
  product_id: string;
  question: string;
  answer: string;
  source_type: string | null;
  source_label: string | null;
  source_url: string | null;
  confidence_score: number | null;
  confidence_level: string | null;
  evidence_items: ResearchEvidenceItem[];
  reason_for_web_search: string | null;
  transparency_steps: string[];
  from_cache: boolean;
}

/** `PriceBreakdownV1`. The only authoritative total in the system. */
export interface PriceBreakdown {
  unit_price_minor: number;
  quantity: number;
  subtotal_minor: number;
  shipping_minor: number;
  tax_minor: number;
  discount_minor: number;
  total_minor: number;
  currency: string;
}

/** `CheckoutV1`. */
export interface CheckoutRecord {
  schema_version: string;
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

/** One recommendation from `POST /api/v1/recommendations/cross-sell`. */
export interface CrossSellRecommendation {
  product_id: string;
  offer_id: string | null;
  title: string;
  category: string | null;
  price_minor: number;
  currency: string;
  compatibility_reason: string | null;
  savings_minor: number | null;
  alternative_title: string | null;
}

export interface CrossSellData {
  target_product_id: string;
  target_title: string | null;
  recommendations: CrossSellRecommendation[];
  metrics: {
    base_aov_minor: number;
    projected_aov_minor: number;
    estimated_attach_rate_pct: number;
  };
}
