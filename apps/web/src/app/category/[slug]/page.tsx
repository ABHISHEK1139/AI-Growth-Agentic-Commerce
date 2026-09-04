"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowRight, Loader2, Sparkles } from "lucide-react";
import { OfferCard } from "@/components/OfferCard";
import { useStore } from "@/context/StoreContext";
import type { ApiError } from "@/lib/api";
import { RANKING_DESCRIPTION } from "@/catalog/client";
import {
  deterministicPathRefused,
  runCatalogSearch,
  type CatalogSearchOutcome,
} from "@/catalog/search";
import {
  catalogSourceDetail,
  catalogSourceLabel,
  categoryIdForSlug,
  categoryTitleForSlug,
} from "@/catalog/present";

/**
 * Category landing.
 *
 * A listing of real offers, filtered by the one thing the catalog can filter a
 * category on: `category`, which is the `category_id` stored on the product row.
 *
 * The route vocabulary and the catalog vocabulary differ -- `/category/laptops`
 * against a stored `laptop` -- so `CATEGORY_SLUG_TO_ID` translates, and the
 * identifier actually sent is printed on the page. A slug with no catalog category
 * is not searched for at all: the screen says there is no such category rather
 * than running a query guaranteed to return nothing and calling it an empty
 * collection.
 */

const CATEGORY_COPY: Record<string, { description: string; prompts: string[] }> = {
  laptops: {
    description: "Find a capable machine for your work, study and creative time.",
    prompts: ["Programming", "Student", "Lightweight", "Long battery"],
  },
  phones: {
    description: "Everyday devices with the camera, battery and performance that fit your day.",
    prompts: ["Best camera", "Long battery", "Compact", "Flagship"],
  },
  audio: {
    description: "Make commutes, flights and focus time sound better.",
    prompts: ["For travel", "Noise cancelling", "For calls", "Best value"],
  },
  keyboards: {
    description: "Build a workspace that feels good to use, all day.",
    prompts: ["Mechanical", "Wireless", "For coding", "Quiet"],
  },
  accessories: {
    description: "The small additions that finish a desk setup.",
    prompts: ["For a laptop", "Wireless", "Best value", "For travel"],
  },
  monitors: {
    description: "More room for focused work, creative detail and better posture.",
    prompts: ["4K", "USB-C", "For designers", "Budget"],
  },
  cameras: {
    description: "Capture crisp photo and video with verified digital optics and gear.",
    prompts: ["DSLR", "Mirrorless", "For travel", "Action cam"],
  },
  appliances: {
    description: "Reliable home and kitchen tech engineered for daily life.",
    prompts: ["Smart home", "Energy efficient", "Kitchen", "Compact"],
  },
  phone_accessories: {
    description: "High-speed chargers, protective cases, and wireless docks.",
    prompts: ["Fast charger", "Wireless charging", "MagSafe", "Car mount"],
  },
};

const LISTING_LIMIT = 24;

type Phase = "loading" | "ready" | "failed" | "blocked" | "unmapped";

export default function CategoryLandingPage() {
  const params = useParams<{ slug: string }>();
  const slug = (params?.slug ?? "").toString();
  const { openAiDrawer } = useStore();

  const categoryId = categoryIdForSlug(slug);
  const title = categoryTitleForSlug(slug);
  const copy = CATEGORY_COPY[slug.toLowerCase()] ?? {
    description: "Browse the current, verified selection.",
    prompts: ["Best value", "For work", "For travel"],
  };

  const [phase, setPhase] = useState<Phase>(categoryId ? "loading" : "unmapped");
  const [outcome, setOutcome] = useState<CatalogSearchOutcome | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [guardMessage, setGuardMessage] = useState<string | null>(null);
  const [credentialGap, setCredentialGap] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [sortBy, setSortBy] = useState<"featured" | "price_asc" | "price_desc" | "rating">("featured");

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  const sortedOffers = useMemo(() => {
    if (!outcome?.offers) return [];
    const list = [...outcome.offers];
    if (sortBy === "price_asc") {
      return list.sort((a, b) => a.unit_price_minor - b.unit_price_minor);
    }
    if (sortBy === "price_desc") {
      return list.sort((a, b) => b.unit_price_minor - a.unit_price_minor);
    }
    if (sortBy === "rating") {
      return list.sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
    }
    return list;
  }, [outcome?.offers, sortBy]);

  useEffect(() => {
    if (!categoryId) {
      setPhase("unmapped");
      return;
    }
    let cancelled = false;
    setPhase("loading");
    setError(null);
    setGuardMessage(null);

    (async () => {
      const result = await runCatalogSearch({ category: categoryId, limit: LISTING_LIMIT });
      if (cancelled) return;
      if (deterministicPathRefused()) setCredentialGap(true);
      if (result.kind === "failed") {
        setError(result.error);
        setPhase("failed");
        return;
      }
      if (result.kind === "blocked") {
        setGuardMessage(result.message);
        setPhase("blocked");
        return;
      }
      setOutcome(result.outcome);
      setPhase("ready");
    })();

    return () => {
      cancelled = true;
    };
  }, [categoryId, reloadToken]);

  return (
    <div className="space-y-9 pb-14">
      <nav className="text-xs font-semibold text-[#68736d]">
        <Link href="/" className="hover:text-[#174c3c]">
          Home
        </Link>
        <span className="mx-2">/</span>
        <span className="text-[#17231e]">{title}</span>
      </nav>

      <section className="overflow-hidden rounded-[28px] bg-[#e5f0e9] p-7 sm:p-10">
        <p className="text-xs font-bold uppercase tracking-[.15em] text-[#174c3c]">
          {categoryId ? `Official Collection` : "Electronics Store"}
        </p>
        <div className="mt-3 flex flex-col justify-between gap-5 lg:flex-row lg:items-end">
          <div>
            <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">{title}</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-[#526058]">{copy.description}</p>
          </div>
          <button
            onClick={() =>
              openAiDrawer({ pageType: "search", customPrompt: `Find the best ${slug} for my needs` })
            }
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-[#174c3c] px-4 py-3 text-sm font-bold text-white"
          >
            <Sparkles className="h-4 w-4" /> Help me choose
          </button>
        </div>
        <div className="mt-7 flex flex-wrap gap-2">
          {copy.prompts.map((prompt) => (
            <Link
              key={prompt}
              href={`/search?q=${encodeURIComponent(`${prompt} ${slug}`)}${
                categoryId ? `&category=${encodeURIComponent(slug)}` : ""
              }`}
              className="rounded-full border border-[#bcd7c4] bg-white/70 px-3 py-1.5 text-xs font-bold text-[#174c3c] transition hover:bg-white"
            >
              {prompt}
            </Link>
          ))}
        </div>
      </section>

      <section>
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-2xl font-extrabold tracking-tight text-slate-900">
              Explore {title}
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              {phase === "loading"
                ? "Reading verified catalog assortment…"
                : phase === "ready" && outcome
                ? `${sortedOffers.length} verified ${sortedOffers.length === 1 ? "product" : "products"} available with 2-day delivery`
                : phase === "unmapped"
                ? "This collection has no matching catalog category"
                : "No listing to show"}
            </p>
          </div>

          {phase === "ready" && outcome && outcome.offers.length > 0 && (
            <div className="flex items-center gap-3">
              <label htmlFor="sort-by" className="text-xs font-semibold text-slate-500">
                Sort by:
              </label>
              <select
                id="sort-by"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                aria-label="Sort products by"
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm transition hover:border-slate-300 focus:outline-none focus:ring-2 focus:ring-[#174c3c]"
              >
                <option value="featured">Featured & Best Match</option>
                <option value="price_asc">Price: Low to High</option>
                <option value="price_desc">Price: High to Low</option>
                <option value="rating">Highest Customer Rating</option>
              </select>
            </div>
          )}
        </div>

        {phase === "loading" ? (
          <div
            className="rounded-2xl border border-[#e6e8df] bg-white p-12 text-center"
            aria-live="polite"
          >
            <Loader2 className="mx-auto h-7 w-7 animate-spin text-[#174c3c]" />
            <p className="mt-3 text-sm font-bold text-[#17231e]">Loading items&hellip;</p>
            <p className="mt-1 text-xs text-[#68736d]">
              Fetching the latest verified electronics from our catalog.
            </p>
          </div>
        ) : null}

        {phase === "unmapped" ? (
          <div className="rounded-2xl border border-dashed border-[#cbd8cf] bg-white p-12 text-center">
            <p className="font-bold text-slate-900">Collection &ldquo;{slug}&rdquo; is not listed</p>
            <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#68736d]">
              We couldn&apos;t find a dedicated category under this name. Explore our full catalog or use search to find what you&apos;re looking for.
            </p>
            <Link href="/search" className="mt-3 inline-block text-sm font-bold text-[#174c3c] hover:underline">
              Search the full catalog &rarr;
            </Link>
          </div>
        ) : null}

        {phase === "failed" && error ? (
          <div className="rounded-2xl border border-rose-200 bg-white p-8 text-center">
            <p className="font-bold text-[#17231e]">This category could not be read</p>
            <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#68736d]">{error.message}</p>
            <p className="mt-2 font-mono text-[11px] text-[#8a938e]">
              {error.code}
              {error.status != null ? ` \u00b7 HTTP ${error.status}` : ""}
              {error.requestId ? ` \u00b7 ${error.requestId}` : ""}
            </p>
            <button
              type="button"
              onClick={reload}
              className="mt-4 rounded-xl bg-[#174c3c] px-5 py-2.5 text-xs font-bold text-white hover:bg-[#103c2f]"
            >
              Try again
            </button>
          </div>
        ) : null}

        {phase === "blocked" && guardMessage ? (
          <div className="rounded-2xl border border-amber-200 bg-white p-8 text-center">
            <p className="font-bold text-[#17231e]">This request was refused</p>
            <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#68736d]">{guardMessage}</p>
          </div>
        ) : null}

        {phase === "ready" && outcome ? (
          outcome.offers.length > 0 ? (
            <>
              {outcome.warnings.length > 0 ? (
                <ul className="mb-4 space-y-1 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-[11px] text-amber-900">
                  {outcome.warnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              ) : null}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {sortedOffers.map((offer, index) => (
                  <OfferCard
                    key={offer.offer_id}
                    offer={offer}
                    catalogSource={outcome.catalogSource}
                    isBestMatch={index === 0}
                  />
                ))}
              </div>
            </>
          ) : (
            <div className="rounded-2xl border border-dashed border-[#cbd8cf] bg-white p-12 text-center">
              <p className="font-bold text-slate-900">No active items in this category right now</p>
              <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#68736d]">
                We currently don&apos;t have available stock listed in this collection. Browse our full catalog or check back shortly.
              </p>
              <Link href="/search" className="mt-3 inline-block text-sm font-bold text-[#174c3c] hover:underline">
                Explore all products &rarr;
              </Link>
            </div>
          )
        ) : null}
      </section>
    </div>
  );
}
