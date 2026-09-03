"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowRight, Loader2, Sparkles } from "lucide-react";
import { OfferCard } from "@/components/OfferCard";
import { useStore } from "@/context/StoreContext";
import type { ApiError } from "@/lib/api";
import { CREDENTIAL_GAP_NOTE, RANKING_DESCRIPTION } from "@/catalog/client";
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
};

const LISTING_LIMIT = 12;

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

  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

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
          {categoryId ? `Catalog category: ${categoryId}` : "Not a catalog category"}
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

      {credentialGap ? (
        <div className="rounded-[22px] border border-amber-200 bg-amber-50 p-5 text-xs leading-relaxed text-amber-950">
          <p className="mb-1 text-sm font-black">
            Deterministic filter endpoint unavailable to this browser
          </p>
          <p>{CREDENTIAL_GAP_NOTE}</p>
        </div>
      ) : null}

      <section>
        <div className="mb-6 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-2xl font-extrabold tracking-tight">
              Explore {title.toLowerCase()}
            </h2>
            <p className="mt-1 text-sm text-[#68736d]">
              {phase === "loading"
                ? "Reading the catalog\u2026"
                : phase === "ready" && outcome
                ? `${outcome.count} ${outcome.count === 1 ? "offer" : "offers"} available now`
                : phase === "unmapped"
                ? "This collection has no matching catalog category"
                : "No listing to show"}
            </p>
            {phase === "ready" && outcome ? (
              <p className="mt-1 text-[11px] text-[#8a938e]">
                <span title={catalogSourceDetail(outcome.catalogSource)}>
                  {catalogSourceLabel(outcome.catalogSource)}
                </span>
                {" \u00b7 "}
                {outcome.answeredBy === "deterministic"
                  ? "POST /api/v1/catalog/search"
                  : "POST /api/explore"}
                {" \u00b7 "}
                {RANKING_DESCRIPTION}
              </p>
            ) : null}
          </div>
          <Link
            href={`/search${categoryId ? `?category=${encodeURIComponent(slug)}` : ""}`}
            className="hidden items-center text-sm font-bold text-[#174c3c] sm:inline-flex"
          >
            Filter this category <ArrowRight className="ml-1 h-4 w-4" />
          </Link>
        </div>

        {phase === "loading" ? (
          <div
            className="rounded-2xl border border-[#e6e8df] bg-white p-12 text-center"
            aria-live="polite"
          >
            <Loader2 className="mx-auto h-7 w-7 animate-spin text-[#174c3c]" />
            <p className="mt-3 text-sm font-bold text-[#17231e]">Reading the catalog&hellip;</p>
            <p className="mt-1 text-xs text-[#68736d]">
              This request has a bound and will report an outcome either way.
            </p>
          </div>
        ) : null}

        {phase === "unmapped" ? (
          <div className="rounded-2xl border border-dashed border-[#cbd8cf] bg-white p-12 text-center">
            <p className="font-bold">No catalog category matches &ldquo;{slug}&rdquo;</p>
            <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#68736d]">
              The catalog stores its own category identifiers, and this address does not name one of
              them. Rather than run a query that cannot match and present the empty result as a
              collection, nothing was requested.
            </p>
            <Link href="/search" className="mt-3 inline-block text-sm font-bold text-[#174c3c]">
              Search the whole catalog &rarr;
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
                {outcome.offers.map((offer, index) => (
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
              <p className="font-bold">No active offer in this category right now</p>
              <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#68736d]">
                The only constraint applied was{" "}
                <span className="font-mono font-bold">category = {categoryId}</span>. The catalog
                answered with zero offers, which means it holds no active, in-stock, unexpired offer
                here rather than that anything went wrong.
              </p>
              <Link href="/search" className="mt-3 inline-block text-sm font-bold text-[#174c3c]">
                Search without the category filter &rarr;
              </Link>
            </div>
          )
        ) : null}
      </section>
    </div>
  );
}
