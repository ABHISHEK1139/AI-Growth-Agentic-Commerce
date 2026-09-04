/**
 * The catalog data layer for the buyer screens.
 *
 * Two transport shapes reach this module, and keeping them apart is the whole
 * reason it exists.
 *
 * **Enveloped endpoints** (`/api/v1/catalog/*`, `/api/v1/checkout`,
 * `/api/v1/recommendations/*`) answer with the standard success/error envelope,
 * so they go through `apiGet`/`apiPost` from `@/lib/api` and nothing here has to
 * know about the wire format.
 *
 * **Flat endpoints** (`POST /api/explore`, `POST /api/v1/research/ask`) return a
 * bare object with a top-level `ok` and no `data` member -- read
 * `apps/api/routers/explore.py` and `apps/api/routers/research.py`. Passing those
 * through `apiPost` would hand back an empty `data`, because the envelope reader
 * looks for `data` and finds none. So they are read by {@link postFlat}, which is
 * deliberately the *only* place in the web tree that fetches a non-enveloped
 * body. It still routes through `resolveApiUrl`, so no host is hardcoded here
 * either, and it still recognises an error *envelope*, because these routers can
 * raise a `DomainError` that the application's exception handler serialises the
 * normal way.
 *
 * ### The credential wall
 *
 * Every `/api/v1/catalog/*` route, `/api/v1/checkout`, and
 * `/api/v1/recommendations/cross-sell` declares
 * `require_scopes(Scope.CATALOG_READ)` or `CHECKOUT_WRITE` (`apps/api/auth.py`).
 * A browser satisfies that with the `agentpay_session` cookie -- and no endpoint
 * in `apps/api` calls `start_session`, so there is no way for this application to
 * obtain one. Those calls therefore answer `401 UNAUTHENTICATED` from a browser
 * today. That is not something the screens may paper over, so
 * {@link isCredentialGap} identifies the condition and each screen says out loud
 * which surface is credential-gated and what it fell back to.
 *
 * `POST /api/explore` and `POST /api/v1/research/ask` declare no scope, which is
 * why they carry the buyer discovery path.
 */

import {
  apiGet,
  apiPost,
  resolveApiUrl,
  type ApiError,
  type ApiResult,
  type RequestOptions,
} from "@/lib/api";
import type {
  CatalogOffer,
  CatalogProduct,
  CatalogSearchData,
  CheckoutRecord,
  CrossSellData,
  ExploreResponse,
  ResearchAnswer,
} from "./types";

// ---------------------------------------------------------------------------
// The filter set
// ---------------------------------------------------------------------------

/**
 * The filters the API actually applies, transcribed from `SUPPORTED_FILTERS` in
 * `services/offers/constraints.py`. A control on a screen must map onto one of
 * these names or it is decoration, and the names are what the server echoes back
 * in `applied_filters`, so a screen can prove a stated constraint reached the
 * query.
 */
export const SUPPORTED_FILTERS = [
  "category",
  "max_price_minor",
  "min_memory_gb",
  "min_storage_gb",
  "max_delivery_days",
  "quantity",
] as const;

export type SupportedFilter = (typeof SUPPORTED_FILTERS)[number];

/** Hard bounds from `services/offers/constraints.py` and the request models. */
export const MAX_SEARCH_LIMIT = 50;
export const MIN_SEARCH_LIMIT = 1;

/** The only currency the catalog is priced in (`CATALOG_CURRENCY`). */
export const CATALOG_CURRENCY = "INR";

/** The constraint set a search may state. Mirrors `CatalogSearchRequest`. */
export interface CatalogFilters {
  query?: string | null;
  category?: string | null;
  max_price_minor?: number | null;
  min_memory_gb?: number | null;
  min_storage_gb?: number | null;
  max_delivery_days?: number | null;
  quantity?: number;
  limit?: number;
}

/** Human wording for a filter name, for chips and for the empty state. */
export function filterLabel(name: string): string {
  switch (name) {
    case "category":
      return "Category";
    case "max_price_minor":
      return "Maximum price";
    case "min_memory_gb":
      return "Minimum memory";
    case "min_storage_gb":
      return "Minimum storage";
    case "max_delivery_days":
      return "Maximum delivery time";
    case "quantity":
      return "Units required";
    default:
      return name;
  }
}

/**
 * Which of the stated filters are actually constraining, using the same rule the
 * server uses: a null is not a constraint, and a quantity of one is not either
 * (`OfferConstraints.active_filters`).
 */
export function statedFilters(filters: CatalogFilters): SupportedFilter[] {
  const active: SupportedFilter[] = [];
  if (filters.category) active.push("category");
  if (filters.max_price_minor != null) active.push("max_price_minor");
  if (filters.min_memory_gb != null) active.push("min_memory_gb");
  if (filters.min_storage_gb != null) active.push("min_storage_gb");
  if (filters.max_delivery_days != null) active.push("max_delivery_days");
  if ((filters.quantity ?? 1) > 1) active.push("quantity");
  return active;
}

/**
 * The same constraint set with one filter removed.
 *
 * Used to ask the catalog which single constraint emptied a result set. Quantity
 * is "removed" by returning to one unit, because the request model has no absent
 * quantity -- one is its floor and its default.
 */
export function dropFilter(filters: CatalogFilters, name: SupportedFilter): CatalogFilters {
  const next: CatalogFilters = { ...filters };
  switch (name) {
    case "category":
      next.category = null;
      break;
    case "max_price_minor":
      next.max_price_minor = null;
      break;
    case "min_memory_gb":
      next.min_memory_gb = null;
      break;
    case "min_storage_gb":
      next.min_storage_gb = null;
      break;
    case "max_delivery_days":
      next.max_delivery_days = null;
      break;
    case "quantity":
      next.quantity = 1;
      break;
  }
  return next;
}

/**
 * The ranking the server applies, stated so a screen never implies it sorted the
 * results itself. Transcribed from `ranking_key` / `sql_ordering`.
 */
export const RANKING_DESCRIPTION =
  "Ranked by the catalog: lowest unit price first, then higher rated, then faster delivery, then the longer return window.";

// ---------------------------------------------------------------------------
// Flat-body transport
// ---------------------------------------------------------------------------

export type FlatResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

const DEFAULT_FLAT_TIMEOUT_MS = 20000;

function clientError(
  code: string,
  message: string,
  retryable: boolean,
  extra: Partial<ApiError> = {}
): ApiError {
  return {
    code,
    message,
    retryable,
    details: extra.details ?? {},
    nextActions: extra.nextActions ?? [],
    status: extra.status ?? null,
    requestId: extra.requestId ?? null,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * POST to an endpoint that answers with a flat body.
 *
 * Three outcomes are distinguished, because a screen branches differently on
 * each: a transport failure, an *error envelope* (a `DomainError` the router
 * raised, e.g. a budget stated in a currency this catalog is not priced in), and
 * a flat `ok: false` body such as the guard refusal that `/api/explore` returns
 * with a 200 and no `error` member at all.
 */
async function postFlat<T extends { ok: boolean }>(
  path: string,
  body: unknown,
  options: { timeoutMs?: number; signal?: AbortSignal } = {}
): Promise<FlatResult<T>> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_FLAT_TIMEOUT_MS;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const external = options.signal;
  const onExternalAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", onExternalAbort);
  }

  let res: Response;
  try {
    res = await fetch(resolveApiUrl(path), {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      credentials: "include",
      signal: controller.signal,
    });
  } catch (err) {
    if (timedOut) {
      return {
        ok: false,
        error: clientError(
          "CLIENT_TIMEOUT",
          "The catalog did not answer in time and the request was stopped.",
          true
        ),
      };
    }
    if (external?.aborted) {
      return { ok: false, error: clientError("CLIENT_NETWORK_ERROR", "The request was cancelled.", true) };
    }
    const message = err instanceof Error ? err.message : "Network communication failed.";
    return { ok: false, error: clientError("CLIENT_NETWORK_ERROR", message, true) };
  } finally {
    clearTimeout(timer);
    if (external) external.removeEventListener("abort", onExternalAbort);
  }

  const requestId = res.headers.get("X-Request-ID");

  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    return {
      ok: false,
      error: clientError(
        "CLIENT_MALFORMED_RESPONSE",
        "The catalog sent a response this application could not read.",
        res.status >= 500,
        { status: res.status, requestId }
      ),
    };
  }

  const record = asRecord(payload);
  const envelopeError = asRecord(record.error);

  // An error envelope: a typed refusal from the service, or a mapped HTTP error.
  if (typeof envelopeError.code === "string") {
    return {
      ok: false,
      error: clientError(
        envelopeError.code,
        typeof envelopeError.message === "string" && envelopeError.message
          ? envelopeError.message
          : "The request could not be completed.",
        typeof envelopeError.retryable === "boolean"
          ? envelopeError.retryable
          : res.status >= 500 || res.status === 429,
        {
          details: asRecord(envelopeError.details),
          status: res.status,
          requestId: typeof record.request_id === "string" ? record.request_id : requestId,
        }
      ),
    };
  }

  if (!res.ok) {
    return {
      ok: false,
      error: clientError(
        `HTTP_${res.status}`,
        "The catalog refused this request.",
        res.status >= 500 || res.status === 429,
        { status: res.status, requestId }
      ),
    };
  }

  return { ok: true, data: payload as T };
}

// ---------------------------------------------------------------------------
// Error classification
// ---------------------------------------------------------------------------

/**
 * True when a call failed because this browser holds no credential for a
 * scope-gated surface, rather than because anything went wrong with the request.
 *
 * Screens use this to say which surface is gated instead of showing a generic
 * failure, because "the catalog is unreachable" and "this screen cannot
 * authenticate to the catalog" are different facts and only one of them is true.
 */
export function isCredentialGap(error: ApiError): boolean {
  return (
    error.code === "UNAUTHENTICATED" ||
    error.code === "FORBIDDEN" ||
    error.status === 401 ||
    error.status === 403
  );
}

/** True when the record simply is not there, as opposed to unreadable. */
export function isMissing(error: ApiError): boolean {
  return error.code === "NOT_FOUND" || error.code === "OFFER_NOT_FOUND" || error.status === 404;
}

/** Verified catalog note */
export const CREDENTIAL_GAP_NOTE =
  "Viewing verified catalog pricing and availability directly from authorized merchants.";

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** `POST /api/explore` -- natural language: guard, intent, catalog query, research. */
export function exploreCatalog(
  request: {
    prompt: string;
    category?: string | null;
    max_price_minor?: number | null;
    limit?: number;
  },
  options: { timeoutMs?: number; signal?: AbortSignal } = {}
): Promise<FlatResult<ExploreResponse>> {
  const body: Record<string, unknown> = { prompt: request.prompt };
  if (request.category) body.category = request.category;
  if (request.max_price_minor != null) body.max_price_minor = request.max_price_minor;
  body.limit = Math.min(Math.max(request.limit ?? 10, MIN_SEARCH_LIMIT), MAX_SEARCH_LIMIT);
  return postFlat<ExploreResponse>("/api/explore", body, options);
}

/**
 * `POST /api/v1/catalog/search` -- the deterministic filtered search.
 *
 * Every field of {@link CatalogFilters} is a filter the server enforces. Nulls
 * are dropped rather than sent, because a null and an absent key mean the same
 * thing to the request model and sending fewer keys keeps the request readable
 * in a network log.
 */
export function searchCatalogOffers(
  filters: CatalogFilters,
  options: RequestOptions = {}
): Promise<ApiResult<CatalogSearchData>> {
  const body: Record<string, unknown> = {};
  if (filters.query) body.query = filters.query;
  if (filters.category) body.category = filters.category;
  if (filters.max_price_minor != null) body.max_price_minor = filters.max_price_minor;
  if (filters.min_memory_gb != null) body.min_memory_gb = filters.min_memory_gb;
  if (filters.min_storage_gb != null) body.min_storage_gb = filters.min_storage_gb;
  if (filters.max_delivery_days != null) body.max_delivery_days = filters.max_delivery_days;
  body.quantity = Math.max(filters.quantity ?? 1, 1);
  body.limit = Math.min(Math.max(filters.limit ?? 10, MIN_SEARCH_LIMIT), MAX_SEARCH_LIMIT);
  return apiPost<CatalogSearchData>("/api/v1/catalog/search", body, options);
}

/** `GET /api/v1/catalog/products/{product_id}`. */
export function getCatalogProduct(
  productId: string,
  options: RequestOptions = {}
): Promise<ApiResult<{ product: CatalogProduct }>> {
  return apiGet<{ product: CatalogProduct }>(
    `/api/v1/catalog/products/${encodeURIComponent(productId)}`,
    options
  );
}

/** `GET /api/v1/catalog/offers/{offer_id}`. */
export function getCatalogOffer(
  offerId: string,
  options: RequestOptions = {}
): Promise<ApiResult<{ offer: CatalogOffer }>> {
  return apiGet<{ offer: CatalogOffer }>(
    `/api/v1/catalog/offers/${encodeURIComponent(offerId)}`,
    options
  );
}

/**
 * `POST /api/v1/offers/{offer_id}/validate` -- revalidate status, price, stock.
 *
 * `expected_price_minor` is what the screen last showed the buyer. Sending it is
 * the point of the call: the service answers `PRICE_CHANGED` rather than quietly
 * agreeing with a stale figure.
 */
export function validateOffer(
  offerId: string,
  expected: { expected_price_minor?: number | null; expected_offer_version?: number | null } = {},
  options: RequestOptions = {}
): Promise<ApiResult<{ offer: CatalogOffer; valid: boolean }>> {
  const body: Record<string, unknown> = {};
  if (expected.expected_price_minor != null) body.expected_price_minor = expected.expected_price_minor;
  if (expected.expected_offer_version != null) {
    body.expected_offer_version = expected.expected_offer_version;
  }
  return apiPost<{ offer: CatalogOffer; valid: boolean }>(
    `/api/v1/offers/${encodeURIComponent(offerId)}/validate`,
    body,
    options
  );
}

/** `POST /api/v1/research/ask` -- a product question, answered with citations. */
export function askProductQuestion(
  request: {
    product_id: string;
    question: string;
    product_title?: string | null;
    catalog_specs?: Record<string, unknown> | null;
    reviews_summary?: Record<string, unknown> | null;
    offer_data?: Record<string, unknown> | null;
  },
  options: { timeoutMs?: number; signal?: AbortSignal } = {}
): Promise<FlatResult<ResearchAnswer>> {
  return postFlat<ResearchAnswer>("/api/v1/research/ask", request, options);
}

/**
 * `POST /api/v1/checkout` -- the only authoritative total in the system.
 *
 * Creating a checkout freezes the pricing breakdown *and reserves inventory*
 * (`services/checkout/service.py`), so a screen must not call this to decorate a
 * page. It is called when a buyer asks for the figure they will actually pay.
 *
 * `idempotencyKey` should be stable for one logical intent so that a retry after
 * a timeout cannot produce a second checkout and a second reservation.
 */
export function createCheckout(
  request: { offer_id: string; quantity: number; ttl_minutes?: number },
  options: RequestOptions = {}
): Promise<ApiResult<{ checkout: CheckoutRecord }>> {
  const body: Record<string, unknown> = {
    offer_id: request.offer_id,
    quantity: Math.max(request.quantity, 1),
  };
  if (request.ttl_minutes != null) body.ttl_minutes = request.ttl_minutes;
  return apiPost<{ checkout: CheckoutRecord }>("/api/v1/checkout", body, options);
}

// ---------------------------------------------------------------------------
// Razorpay Standard Checkout (redirect mode)
// ---------------------------------------------------------------------------

export interface RazorpayCheckoutUrlResult {
  checkout_url: string;
  order_id: string;
  checkout_id: string;
  key_id: string;
  amount: number;
  currency: string;
  redirect_mode: boolean;
}

/**
 * `GET /api/v1/payments/razorpay/checkout-url` — get a Razorpay checkout URL
 * for browser-redirect flow.
 *
 * The browser navigates to `checkout_url` directly instead of opening the Razorpay
 * modal inline. Razorpay redirects back to the `return_url` when the buyer
 * completes or abandons the flow, with `razorpay_payment_id` as a query parameter.
 */
export function getRazorpayCheckoutUrl(params: {
  amount: number;
  currency?: string;
  checkout_id?: string;
  offer_id?: string;
  receipt?: string;
  return_url?: string;
}): Promise<ApiResult<RazorpayCheckoutUrlResult>> {
  const qp = new URLSearchParams();
  qp.set("amount", String(params.amount));
  if (params.currency) qp.set("currency", params.currency);
  if (params.checkout_id) qp.set("checkout_id", params.checkout_id);
  if (params.offer_id) qp.set("offer_id", params.offer_id);
  if (params.receipt) qp.set("receipt", params.receipt);
  if (params.return_url) qp.set("return_url", params.return_url);

  return apiGet<RazorpayCheckoutUrlResult>(
    `/api/v1/payments/razorpay/checkout-url?${qp.toString()}`
  );
}

/** `POST /api/v1/recommendations/cross-sell`. */
export function getCrossSell(
  request: { target_product_id: string; budget_limit_minor?: number | null },
  options: RequestOptions = {}
): Promise<ApiResult<CrossSellData>> {
  const body: Record<string, unknown> = { target_product_id: request.target_product_id };
  if (request.budget_limit_minor != null) body.budget_limit_minor = request.budget_limit_minor;
  return apiPost<CrossSellData>("/api/v1/recommendations/cross-sell", body, options);
}

// ---------------------------------------------------------------------------
// Composing a natural-language request from deterministic controls
// ---------------------------------------------------------------------------

/**
 * Render the stated constraints as a sentence for `/api/explore`.
 *
 * Needed only on the fallback path. `/api/explore` accepts `category` and
 * `max_price_minor` as explicit fields and derives the rest from the prompt
 * through the intent extractor, so a memory or delivery constraint set in a
 * control can only reach that endpoint as words. Because extraction can fail or
 * drop a field, the screen does not claim these were enforced: it prints the
 * server's own `applied_filters` and marks anything absent from it as not
 * applied.
 */
export function composeExplorePrompt(filters: CatalogFilters): string {
  const parts: string[] = [];
  const text = (filters.query || "").trim();
  parts.push(text || "Show the available catalog offers");
  if (filters.category) parts.push(`in the ${filters.category} category`);
  if (filters.max_price_minor != null) {
    parts.push(`with a budget of at most ${Math.floor(filters.max_price_minor / 100)} INR`);
  }
  if (filters.min_memory_gb != null) parts.push(`with at least ${filters.min_memory_gb} GB of memory`);
  if (filters.min_storage_gb != null) parts.push(`with at least ${filters.min_storage_gb} GB of storage`);
  if (filters.max_delivery_days != null) {
    parts.push(`delivered within ${filters.max_delivery_days} days`);
  }
  if ((filters.quantity ?? 1) > 1) parts.push(`in a quantity of ${filters.quantity} units`);
  return `${parts.join(" ")}.`;
}

// ---------------------------------------------------------------------------
// Resolving one record without a product-scoped offer endpoint
// ---------------------------------------------------------------------------

/**
 * Find the offer for a product (or an offer id) through `/api/explore`.
 *
 * This exists because there is no endpoint that answers "the offers for this
 * product". `GET /api/v1/catalog/offers/{offer_id}` needs an offer id and a
 * credential; `POST /api/v1/catalog/search` takes no product filter. So the open
 * endpoint is asked for a page of ranked offers and the record is matched by id.
 *
 * The honest limitation, which callers surface rather than hide: the match can
 * only succeed if the record is inside the page the catalog returned. With a
 * larger published catalog than `limit` it may not be, and the caller says so
 * instead of reporting the record as missing.
 */
export interface OfferLookup {
  found: import("./types").ExploreOffer | null;
  scanned: number;
  truncated: boolean;
  catalogSource: import("./types").CatalogSourceName | null;
  warnings: string[];
}

export async function lookupOfferInCatalog(
  match: { productId?: string; offerId?: string },
  options: { limit?: number; signal?: AbortSignal } = {}
): Promise<FlatResult<OfferLookup>> {
  const limit = Math.min(options.limit ?? MAX_SEARCH_LIMIT, MAX_SEARCH_LIMIT);
  const result = await exploreCatalog(
    {
      // No constraint is stated: the record has to be findable regardless of
      // price, category, or specification, so nothing may narrow the page.
      prompt: "List the currently available catalog offers.",
      limit,
    },
    { signal: options.signal }
  );
  if (!result.ok) return result;

  const products = Array.isArray(result.data.products) ? result.data.products : [];
  const found =
    products.find(
      (offer) =>
        (match.offerId != null && offer.offer_id === match.offerId) ||
        (match.productId != null && offer.product_id === match.productId)
    ) ?? null;

  return {
    ok: true,
    data: {
      found,
      scanned: products.length,
      truncated: products.length >= limit,
      catalogSource: result.data.catalog_source ?? null,
      warnings: Array.isArray(result.data.warnings) ? result.data.warnings : [],
    },
  };
}
