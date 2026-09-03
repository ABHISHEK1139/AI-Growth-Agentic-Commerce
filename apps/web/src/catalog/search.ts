/**
 * One constrained catalog search, usable from any buyer screen.
 *
 * The screens that list offers -- search and the category landings -- must agree on
 * three things, so they share this instead of each implementing them:
 *
 * 1. **Which endpoint answered.** The deterministic query
 *    (`POST /api/v1/catalog/search`) is preferred because it enforces every filter
 *    it accepts. When it refuses this browser for want of a catalog-read
 *    credential, the open natural-language endpoint (`POST /api/explore`) answers
 *    instead, and the outcome says so.
 * 2. **The join.** `POST /api/v1/catalog/search` returns `OfferV1` records with no
 *    title, rating, or image, because those are product facts. Each result's
 *    product row is read and joined so a card has something true to render.
 * 3. **What was actually applied.** The deterministic path applies every stated
 *    filter by construction. The natural-language path reports its own
 *    `applied_filters`, and that list -- not the screen's intent -- is what the
 *    outcome carries.
 *
 * The credential refusal is latched for the life of the page load. Re-asking a
 * question already answered would cost a 401 per search, and the latch is global
 * because the credential is a property of the browser, not of one screen.
 */

import {
  exploreCatalog,
  composeExplorePrompt,
  getCatalogProduct,
  isCredentialGap,
  searchCatalogOffers,
  statedFilters,
  type CatalogFilters,
} from "./client";
import { toOfferView } from "./adapt";
import type { ApiError } from "@/lib/api";
import type { CatalogSourceName, ExploreIntent, ExploreOffer } from "./types";

export type AnsweredBy = "deterministic" | "natural";

export interface CatalogSearchOutcome {
  offers: ExploreOffer[];
  /** The count the server reported, which can exceed what was returned. */
  count: number;
  answeredBy: AnsweredBy;
  /** Filter names the server confirmed applying. */
  appliedFilters: string[];
  catalogSource: CatalogSourceName | null;
  warnings: string[];
  intent: ExploreIntent | null;
  /** Product rows that could not be read on the deterministic path. */
  productJoinFailures: number;
}

export type CatalogSearchResult =
  | { kind: "ok"; outcome: CatalogSearchOutcome }
  | { kind: "blocked"; message: string }
  | { kind: "failed"; error: ApiError };

let deterministicRefused = false;

/** True once the deterministic endpoint has refused this browser. */
export function deterministicPathRefused(): boolean {
  return deterministicRefused;
}

const DEFAULT_LIMIT = 12;

export async function runCatalogSearch(filters: CatalogFilters): Promise<CatalogSearchResult> {
  const limit = filters.limit ?? DEFAULT_LIMIT;

  if (!deterministicRefused) {
    const result = await searchCatalogOffers({ ...filters, limit });
    if (result.ok) {
      const offers = Array.isArray(result.data.offers) ? result.data.offers : [];
      const joined = await Promise.all(
        offers.map(async (offer) => {
          const productResult = await getCatalogProduct(offer.product_id);
          if (!productResult.ok) return { view: toOfferView(offer, null), failed: true };
          const product = productResult.data?.product;
          const firstImage = (product?.images ?? []).find(
            (image) => typeof image?.source_url === "string" && image.source_url.trim().length > 0
          );
          return {
            view: toOfferView(offer, {
              title: product?.title,
              category_id: product?.category_id,
              average_rating: product?.average_rating,
              rating_number: product?.rating_number,
              imageUrl: firstImage?.source_url ?? null,
              specifications: product?.specifications ?? null,
            }),
            failed: false,
          };
        })
      );
      return {
        kind: "ok",
        outcome: {
          offers: joined.map((entry) => entry.view),
          count: typeof result.data.count === "number" ? result.data.count : offers.length,
          answeredBy: "deterministic",
          appliedFilters: statedFilters(filters),
          // A success here means the query ran against the published catalog: this
          // endpoint has no offline fallback, it uses the request's own session.
          catalogSource: "postgresql",
          warnings: result.warnings.map((warning) => warning.message),
          intent: null,
          productJoinFailures: joined.filter((entry) => entry.failed).length,
        },
      };
    }
    if (isCredentialGap(result.error)) {
      deterministicRefused = true;
    }
  }

  const explore = await exploreCatalog({
    prompt: composeExplorePrompt(filters),
    category: filters.category ?? null,
    max_price_minor: filters.max_price_minor ?? null,
    limit,
  });
  if (!explore.ok) return { kind: "failed", error: explore.error };
  if (explore.data.guard_blocked) {
    return {
      kind: "blocked",
      message:
        explore.data.message ||
        "The safety classifier refused this request before it reached the catalog.",
    };
  }
  const products = Array.isArray(explore.data.products) ? explore.data.products : [];
  return {
    kind: "ok",
    outcome: {
      offers: products,
      count: typeof explore.data.count === "number" ? explore.data.count : products.length,
      answeredBy: "natural",
      appliedFilters: Array.isArray(explore.data.applied_filters)
        ? explore.data.applied_filters
        : [],
      catalogSource: explore.data.catalog_source ?? null,
      warnings: Array.isArray(explore.data.warnings) ? explore.data.warnings : [],
      intent: explore.data.intent ?? null,
      productJoinFailures: 0,
    },
  };
}
