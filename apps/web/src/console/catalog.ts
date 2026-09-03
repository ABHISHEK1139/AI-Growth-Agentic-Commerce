/**
 * Catalog and inventory reads for the merchant console.
 *
 * ### Two sources, never mixed silently
 *
 * `POST /api/v1/catalog/search` is the merchant-scoped read: `OfferService`
 * filters on `principal.merchant_id`, so the tenant is the endpoint's decision
 * and this module sends none. Its projection is `OfferV1`, which carries the
 * fields an operator needs and no catalogue text — no title, no image, no
 * rating. Titles come from `GET /api/v1/catalog/products/{id}`, one call per
 * offer, which is also merchant-scoped.
 *
 * Both routes declare `require_scopes(Scope.CATALOG_READ)`, and no endpoint in
 * `apps/api` issues a browser session, so from a browser they answer
 * `401 UNAUTHENTICATED` today. When that happens this module falls back to
 * `POST /api/explore`, which declares no scope — and the fallback is *reported*,
 * not hidden, because the two reads are not equivalent:
 *
 * | | scoped read | open discovery read |
 * | --- | --- | --- |
 * | Tenant | the signed-in principal's | `settings.default_merchant_id`, the gateway's own |
 * | Offer status | served (`active`/`needs_review`/…) | absent; only matching offers are returned |
 * | Titles, ratings, images | a second call per offer | included |
 *
 * A screen showing the fallback says so on the surface, every time, because
 * "your catalog" and "the gateway's default tenant's catalog" are different
 * claims.
 *
 * ### Health figures are the backend's, not this module's
 *
 * `services/catalog/service.py` computes `product_count`, `valid_count` and
 * `needs_review_count` for each import run, writes them to `catalog_version`, and
 * records them in the audit metadata of the event it appends. Those three
 * integers are the only catalog-health figures anything in this system actually
 * computes, so they are read from the ledger and nothing is derived from the offer
 * rows to stand in for them. There is no "specifications 96% complete" score
 * anywhere in the gateway; the previous screen invented one.
 *
 * Note the event naming, which is genuinely confusing and worth knowing before
 * reading a count: an import run is appended as `CATALOG_SEARCHED` and a publish
 * as `OFFERS_RETURNED`, both against the `catalog_version` aggregate, with the
 * real action in `metadata.action`.
 */

import type { ApiError } from "@/lib/api";
import {
  getCatalogProduct,
  searchCatalogOffers,
  exploreCatalog,
  isCredentialGap,
  CREDENTIAL_GAP_NOTE,
  MAX_SEARCH_LIMIT,
} from "@/catalog/client";
import type {
  CatalogOffer,
  CatalogProduct,
  CatalogSourceName,
  ExploreOffer,
  PricingSource,
} from "@/catalog/types";
import { fetchAuditEvents, metadataNumber, metadataString } from "./audit";

export { CREDENTIAL_GAP_NOTE, isCredentialGap, MAX_SEARCH_LIMIT };

/** Which read answered. Every screen that shows rows also shows this. */
export type OfferSourceKind = "scoped" | "open";

/** One offer as the console renders it. Every field is a served field. */
export interface ConsoleOfferRow {
  offerId: string;
  productId: string;
  /** From the product endpoint (scoped read) or the explore payload. */
  title: string | null;
  category: string | null;
  unitPriceMinor: number;
  currency: string;
  availableStock: number;
  deliveryDays: number;
  returnPeriodDays: number;
  offerVersion: number;
  pricingSource: PricingSource;
  expiresAt: string;
  /** Served only by the scoped read. */
  status: string | null;
  /** Product-level facts. Null when the read that answered does not carry them. */
  averageRating: number | null;
  ratingCount: number | null;
  imageCount: number | null;
  imageUrl: string | null;
  memoryGb: number | null;
  storageGb: number | null;
}

export interface OfferReadOutcome {
  kind: OfferSourceKind;
  rows: ConsoleOfferRow[];
  /** True when the scoped read was refused for want of a credential. */
  credentialGap: boolean;
  /** The scoped read's failure, kept so a screen can show the real code. */
  scopedError: ApiError | null;
  /** Provenance from the open read: `postgresql` or `seed_fixture`. */
  catalogSource: CatalogSourceName | null;
  warnings: string[];
  /** True when the read returned as many rows as it was allowed to. */
  truncated: boolean;
  requestedLimit: number;
}

function specNumber(specs: Record<string, unknown> | null, key: string): number | null {
  const raw = specs?.[key];
  return typeof raw === "number" ? raw : null;
}

function rowFromScopedOffer(
  offer: CatalogOffer,
  product: CatalogProduct | null
): ConsoleOfferRow {
  return {
    offerId: offer.offer_id,
    productId: offer.product_id,
    title: product ? product.title : null,
    category: product ? product.category_id : null,
    unitPriceMinor: offer.unit_price_minor,
    currency: offer.currency,
    availableStock: offer.available_quantity,
    deliveryDays: offer.delivery_days,
    returnPeriodDays: offer.return_period_days,
    offerVersion: offer.offer_version,
    pricingSource: offer.pricing_source,
    expiresAt: offer.expires_at,
    status: offer.status,
    averageRating: product ? product.average_rating : null,
    ratingCount: product ? product.rating_number : null,
    imageCount: product ? product.images.length : null,
    imageUrl:
      product && product.images.length > 0 ? (product.images[0].source_url ?? null) : null,
    memoryGb: offer.specifications.memory_gb,
    storageGb: offer.specifications.storage_gb,
  };
}

function rowFromExploreOffer(offer: ExploreOffer): ConsoleOfferRow {
  return {
    offerId: offer.offer_id,
    productId: offer.product_id,
    title: offer.title,
    category: offer.category,
    unitPriceMinor: offer.unit_price_minor,
    currency: offer.currency,
    availableStock: offer.available_stock,
    deliveryDays: offer.delivery_days,
    returnPeriodDays: offer.return_period_days,
    offerVersion: offer.offer_version,
    pricingSource: offer.pricing_source,
    expiresAt: offer.expires_at,
    // The explore projection omits offer status: the query already restricted the
    // result to offers it would serve, so there is nothing honest to display.
    status: null,
    averageRating: typeof offer.rating === "number" ? offer.rating : null,
    ratingCount: typeof offer.reviews_count === "number" ? offer.reviews_count : null,
    imageCount: null,
    imageUrl: offer.image_url,
    memoryGb: specNumber(offer.specs, "memory_gb"),
    storageGb: specNumber(offer.specs, "storage_gb"),
  };
}

/**
 * Read the catalog: the scoped endpoint first, the open one only if the scoped
 * read was refused for want of a credential.
 *
 * A scoped read that fails for any *other* reason is returned as a failure rather
 * than papered over with a different tenant's data.
 */
export async function readOffers(
  options: { limit?: number; category?: string | null; signal?: AbortSignal } = {}
): Promise<{ ok: true; outcome: OfferReadOutcome } | { ok: false; error: ApiError }> {
  const limit = Math.min(Math.max(options.limit ?? 24, 1), MAX_SEARCH_LIMIT);

  const scoped = await searchCatalogOffers(
    { category: options.category ?? null, limit },
    { signal: options.signal }
  );

  if (scoped.ok) {
    const offers = Array.isArray(scoped.data?.offers) ? scoped.data.offers : [];
    // One product lookup per offer. `GET /api/v1/catalog/*` is limited to 60
    // requests a minute, and `limit` is capped at 50, so a single page of offers
    // stays inside the budget.
    const products = await Promise.all(
      offers.map(async (offer) => {
        const result = await getCatalogProduct(offer.product_id, { signal: options.signal });
        return result.ok ? result.data.product : null;
      })
    );
    return {
      ok: true,
      outcome: {
        kind: "scoped",
        rows: offers.map((offer, index) => rowFromScopedOffer(offer, products[index])),
        credentialGap: false,
        scopedError: null,
        catalogSource: null,
        warnings: [],
        truncated: offers.length >= limit,
        requestedLimit: limit,
      },
    };
  }

  if (!isCredentialGap(scoped.error)) {
    return { ok: false, error: scoped.error };
  }

  const open = await exploreCatalog(
    {
      prompt: "List the currently available catalog offers.",
      category: options.category ?? null,
      limit,
    },
    { signal: options.signal }
  );

  if (!open.ok) return { ok: false, error: open.error };

  const products = Array.isArray(open.data.products) ? open.data.products : [];
  return {
    ok: true,
    outcome: {
      kind: "open",
      rows: products.map(rowFromExploreOffer),
      credentialGap: true,
      scopedError: scoped.error,
      catalogSource: open.data.catalog_source ?? null,
      warnings: Array.isArray(open.data.warnings) ? open.data.warnings : [],
      truncated: products.length >= limit,
      requestedLimit: limit,
    },
  };
}

// ---------------------------------------------------------------------------
// Catalog health, computed by the importer and recorded in the ledger
// ---------------------------------------------------------------------------

export interface CatalogHealthSnapshot {
  catalogVersionId: string;
  /** The importer's counters. Null when the metadata did not carry one. */
  productCount: number | null;
  validCount: number | null;
  needsReviewCount: number | null;
  sourceChecksum: string | null;
  recordedAt: string;
  /** True when a publish event was appended for the same catalog version. */
  published: boolean;
  publishedAt: string | null;
}

export interface CatalogHealthOutcome {
  /** Newest first. Empty when no import has been logged for the tenant. */
  snapshots: CatalogHealthSnapshot[];
  /** Set when the ledger read failed; the health tiles then show a gap. */
  error: ApiError | null;
  credentialGap: boolean;
}

/**
 * Read the importer's own catalog-health counters out of the audit ledger.
 *
 * The events are appended with `merchant_id`, so the ledger endpoint scopes them
 * to the caller's tenant exactly as it does everything else.
 */
export async function readCatalogHealth(
  options: { signal?: AbortSignal } = {}
): Promise<CatalogHealthOutcome> {
  const result = await fetchAuditEvents(
    { aggregateType: "catalog_version", limit: 200 },
    { signal: options.signal }
  );

  if (!result.ok) {
    return {
      snapshots: [],
      error: result.error,
      credentialGap: isCredentialGap(result.error),
    };
  }

  const events = Array.isArray(result.data?.events) ? result.data.events : [];
  const publishes: Record<string, string> = {};
  for (let i = 0; i < events.length; i += 1) {
    const event = events[i];
    if (metadataString(event.metadata, "action") === "publish") {
      publishes[event.aggregate_id] = event.created_at;
    }
  }

  const snapshots: CatalogHealthSnapshot[] = [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (metadataString(event.metadata, "action") !== "import") continue;
    snapshots.push({
      catalogVersionId: event.aggregate_id,
      productCount: metadataNumber(event.metadata, "product_count"),
      validCount: metadataNumber(event.metadata, "valid_count"),
      needsReviewCount: metadataNumber(event.metadata, "needs_review_count"),
      sourceChecksum: metadataString(event.metadata, "source_checksum"),
      recordedAt: event.created_at,
      published: Object.prototype.hasOwnProperty.call(publishes, event.aggregate_id),
      publishedAt: publishes[event.aggregate_id] ?? null,
    });
  }

  return { snapshots, error: null, credentialGap: false };
}

// ---------------------------------------------------------------------------
// Catalog import (Phase 2: CSV upload, validate, publish, rollback)
// ---------------------------------------------------------------------------

/** A catalog import record, as returned by the backend. */
export interface CatalogImport {
  import_id: string;
  merchant_id: string;
  filename: string;
  status: "pending" | "valid" | "invalid" | "published";
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  error_summary: string | null;
  created_at: string;
  validated_at: string | null;
  published_at: string | null;
  published_catalog_version_id: string | null;
}

/** One row from the staging table, with validation results. */
export interface CatalogImportRow {
  row_number: number;
  sku: string;
  title: string;
  price_minor: number;
  currency: string;
  inventory: number;
  status: string;
  category: string | null;
  is_valid: boolean;
  validation_errors: Record<string, string> | null;
}

export interface ImportCreated {
  import_id: string;
  filename: string;
  total_rows: number;
  status: string;
}

export interface ValidationResult {
  import_id: string;
  status: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  error_summary: string | null;
}

export interface PublishResult {
  import_id: string;
  status: string;
  catalog_version_id: string;
  products_created: number;
  offers_created: number;
}

/** Upload a CSV file and stage its rows for validation. */
export async function createCatalogImport(
  file: File,
  options?: { signal?: AbortSignal }
): Promise<{ ok: true; outcome: ImportCreated } | { ok: false; error: ApiError }> {
  const { apiUpload } = await import("@/lib/api");
  const result = await apiUpload<ImportCreated>(
    "/api/v1/merchant/catalog/imports",
    file,
    "file",
    { signal: options?.signal }
  );
  if (result.ok) return { ok: true, outcome: result.data };
  return { ok: false, error: result.error };
}

/** Get the current status of an import. */
export async function readCatalogImport(
  importId: string,
  options?: { signal?: AbortSignal }
): Promise<{ ok: true; outcome: CatalogImport } | { ok: false; error: ApiError }> {
  const { apiGet } = await import("@/lib/api");
  const result = await apiGet<CatalogImport>(
    `/api/v1/merchant/catalog/imports/${importId}`,
    { signal: options?.signal }
  );
  if (result.ok) return { ok: true, outcome: result.data };
  return { ok: false, error: result.error };
}

/** Run validation on all staged rows for an import. */
export async function validateCatalogImport(
  importId: string,
  options?: { signal?: AbortSignal }
): Promise<{ ok: true; outcome: ValidationResult } | { ok: false; error: ApiError }> {
  const { apiPost } = await import("@/lib/api");
  const result = await apiPost<ValidationResult>(
    `/api/v1/merchant/catalog/imports/${importId}/validate`,
    {},
    { signal: options?.signal }
  );
  if (result.ok) return { ok: true, outcome: result.data };
  return { ok: false, error: result.error };
}

/** List staged rows for an import (paginated). */
export async function listCatalogImportRows(
  importId: string,
  params?: { page?: number; page_size?: number; valid_only?: boolean; signal?: AbortSignal }
): Promise<{
  ok: true;
  outcome: { rows: CatalogImportRow[]; total: number; page: number; page_size: number };
} | { ok: false; error: ApiError }> {
  const { apiGet } = await import("@/lib/api");
  const search = new URLSearchParams();
  if (params?.page) search.set("page", String(params.page));
  if (params?.page_size) search.set("page_size", String(params.page_size));
  if (params?.valid_only) search.set("valid_only", "true");
  const qs = search.toString();
  const result = await apiGet<{
    rows: CatalogImportRow[];
    total: number;
    page: number;
    page_size: number;
  }>(
    `/api/v1/merchant/catalog/imports/${importId}/rows${qs ? `?${qs}` : ""}`,
    { signal: params?.signal }
  );
  if (result.ok) return { ok: true, outcome: result.data };
  return { ok: false, error: result.error };
}

/** Publish a validated import, promoting its rows to a new catalog version. */
export async function publishCatalogImport(
  importId: string,
  options?: { signal?: AbortSignal }
): Promise<{ ok: true; outcome: PublishResult } | { ok: false; error: ApiError }> {
  const { apiPost } = await import("@/lib/api");
  const result = await apiPost<PublishResult>(
    `/api/v1/merchant/catalog/imports/${importId}/publish`,
    {},
    { signal: options?.signal }
  );
  if (result.ok) return { ok: true, outcome: result.data };
  return { ok: false, error: result.error };
}

/** Delete a pending/validated import and all its staged rows. */
export async function rollbackCatalogImport(
  importId: string,
  options?: { signal?: AbortSignal }
): Promise<{ ok: true } | { ok: false; error: ApiError }> {
  const { apiPost } = await import("@/lib/api");
  const result = await apiPost<{ import_id: string; status: string }>(
    `/api/v1/merchant/catalog/imports/${importId}/rollback`,
    {},
    { signal: options?.signal }
  );
  if (result.ok) return { ok: true };
  return { ok: false, error: result.error };
}

export const CATALOG_HEALTH_SOURCE_NOTE =
  "Computed by services/catalog/service.py during the import run, written to catalog_version, and recorded in the audit metadata of the event it appends. Nothing on this screen derives a health figure from the offer rows.";

export const NO_HEALTH_SCORE_REASON =
  "No endpoint computes a catalog quality or AI-readiness score. The importer counts products, valid products and products needing review, and that is the whole of what the gateway measures.";
