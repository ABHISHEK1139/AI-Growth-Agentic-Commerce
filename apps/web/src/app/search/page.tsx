"use client";

import React, { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Check,
  Filter,
  Loader2,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { OfferCard } from "@/components/OfferCard";
import { useStore } from "@/context/StoreContext";
import {
  type CatalogFilters,
} from "@/catalog/client";
import {
  runCatalogSearch,
  type CatalogSearchOutcome,
} from "@/catalog/search";
import {
  CATEGORY_SLUG_TO_ID,
  categoryIdForSlug,
} from "@/catalog/present";

const DEFAULT_LIMIT = 16;

const CATEGORY_CHOICES: Array<{ slug: string; label: string; icon: string }> = [
  { slug: "all", label: "All Categories", icon: "✨" },
  { slug: "appliances", label: "Appliances", icon: "🏠" },
  { slug: "laptops", label: "Laptops", icon: "💻" },
  { slug: "phones", label: "Smartphones", icon: "📱" },
  { slug: "audio", label: "Audio & Headphones", icon: "🎧" },
  { slug: "cameras", label: "Cameras & Optics", icon: "📷" },
  { slug: "keyboards", label: "Computer Accessories", icon: "⌨️" },
  { slug: "phone_accessories", label: "Phone Accessories", icon: "🔌" },
  { slug: "monitors", label: "Monitors & Displays", icon: "🖥️" },
];

const PRICE_PRESETS = [
  { label: "Any Price", value: null },
  { label: "Under ₹25,000", value: 2500000 },
  { label: "Under ₹50,000", value: 5000000 },
  { label: "Under ₹80,000", value: 8000000 },
  { label: "Under ₹1,50,000", value: 15000000 },
];

const MEMORY_CHOICES: Array<{ label: string; value: number | null }> = [
  { label: "Any", value: null },
  { label: "8 GB+", value: 8 },
  { label: "16 GB+", value: 16 },
  { label: "32 GB+", value: 32 },
];

const STORAGE_CHOICES: Array<{ label: string; value: number | null }> = [
  { label: "Any", value: null },
  { label: "256 GB+", value: 256 },
  { label: "512 GB+", value: 512 },
  { label: "1 TB+", value: 1024 },
];

const DELIVERY_CHOICES: Array<{ label: string; value: number | null }> = [
  { label: "Any Speed", value: null },
  { label: "⚡ Express (1-2 Days)", value: 2 },
  { label: "Standard (3-5 Days)", value: 5 },
];

type Phase = "idle" | "loading" | "ready" | "failed" | "blocked";

function SearchAndFilterContent() {
  const { openAiDrawer } = useStore();
  const searchParams = useSearchParams();

  const [queryText, setQueryText] = useState("");
  const [categorySlug, setCategorySlug] = useState("all");
  const [maxPriceMinor, setMaxPriceMinor] = useState<number | null>(null);
  const [minMemoryGb, setMinMemoryGb] = useState<number | null>(null);
  const [minStorageGb, setMinStorageGb] = useState<number | null>(null);
  const [maxDeliveryDays, setMaxDeliveryDays] = useState<number | null>(null);
  const [mobileFilterOpen, setMobileFilterOpen] = useState(false);

  const [committed, setCommitted] = useState<CatalogFilters | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [outcome, setOutcome] = useState<CatalogSearchOutcome | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const runId = useRef(0);
  const busy = phase === "loading";

  const readParams = useCallback((): CatalogFilters => {
    const q = searchParams.get("q") || "";
    const slugParam = (searchParams.get("category") || "").toLowerCase();
    const slug = slugParam && CATEGORY_SLUG_TO_ID[slugParam] ? slugParam : "all";
    const numeric = (key: string): number | null => {
      const raw = searchParams.get(key);
      if (raw === null) return null;
      const parsed = Number(raw);
      return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
    };
    return {
      query: q,
      category: slug === "all" ? null : categoryIdForSlug(slug),
      max_price_minor: numeric("max_price_minor"),
      min_memory_gb: numeric("min_memory_gb"),
      min_storage_gb: numeric("min_storage_gb"),
      max_delivery_days: numeric("max_delivery_days"),
      quantity: 1,
      limit: DEFAULT_LIMIT,
    };
  }, [searchParams]);

  useEffect(() => {
    const initial = readParams();
    setQueryText(initial.query || "");
    const slugParam = (searchParams.get("category") || "").toLowerCase();
    if (slugParam && CATEGORY_SLUG_TO_ID[slugParam]) setCategorySlug(slugParam);
    setMaxPriceMinor(initial.max_price_minor ?? null);
    setMinMemoryGb(initial.min_memory_gb ?? null);
    setMinStorageGb(initial.min_storage_gb ?? null);
    setMaxDeliveryDays(initial.max_delivery_days ?? null);
    setCommitted(initial);
  }, [readParams, searchParams]);

  const currentFilters = useCallback((): CatalogFilters => {
    return {
      query: queryText.trim(),
      category: categorySlug === "all" ? null : categoryIdForSlug(categorySlug),
      max_price_minor: maxPriceMinor,
      min_memory_gb: minMemoryGb,
      min_storage_gb: minStorageGb,
      max_delivery_days: maxDeliveryDays,
      quantity: 1,
      limit: DEFAULT_LIMIT,
    };
  }, [queryText, categorySlug, maxPriceMinor, minMemoryGb, minStorageGb, maxDeliveryDays]);

  const executeSearch = (filters: CatalogFilters) => {
    setCommitted(filters);
  };

  const handleFormSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    executeSearch(currentFilters());
  };

  const handleCategorySelect = (slug: string) => {
    setCategorySlug(slug);
    executeSearch({
      ...currentFilters(),
      category: slug === "all" ? null : categoryIdForSlug(slug),
    });
  };

  const handlePriceSelect = (price: number | null) => {
    setMaxPriceMinor(price);
    executeSearch({
      ...currentFilters(),
      max_price_minor: price,
    });
  };

  const handleClearFilters = () => {
    setQueryText("");
    setCategorySlug("all");
    setMaxPriceMinor(null);
    setMinMemoryGb(null);
    setMinStorageGb(null);
    setMaxDeliveryDays(null);
    executeSearch({
      query: "",
      category: null,
      max_price_minor: null,
      min_memory_gb: null,
      min_storage_gb: null,
      max_delivery_days: null,
      quantity: 1,
      limit: DEFAULT_LIMIT,
    });
  };

  useEffect(() => {
    if (!committed) return;
    runId.current += 1;
    const token = runId.current;
    let cancelled = false;

    setPhase("loading");
    setErrorMessage(null);

    (async () => {
      try {
        const result = await runCatalogSearch(committed);
        if (cancelled || token !== runId.current) return;

        if (result.kind === "ok") {
          setOutcome(result.outcome);
          setPhase("ready");
        } else if (result.kind === "blocked") {
          setErrorMessage(result.message || "Query blocked by safety filters.");
          setPhase("blocked");
        } else {
          setErrorMessage(result.error?.message || "Failed to load products.");
          setPhase("failed");
        }
      } catch {
        if (!cancelled && token === runId.current) {
          setErrorMessage("Failed to search products. Please try again.");
          setPhase("failed");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [committed]);

  const activeOffers = outcome?.offers ?? [];
  const hasActiveFilters =
    categorySlug !== "all" ||
    maxPriceMinor !== null ||
    minMemoryGb !== null ||
    minStorageGb !== null ||
    maxDeliveryDays !== null ||
    queryText.length > 0;

  return (
    <div className="space-y-8 pb-20 max-w-7xl mx-auto px-4 sm:px-6">
      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#174c3c] to-[#0c2e24] p-6 sm:p-10 text-white shadow-lg">
        <div className="relative z-10 max-w-3xl space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3.5 py-1 text-xs font-semibold backdrop-blur-md">
            <Sparkles className="h-3.5 w-3.5 text-[#e5f0e9]" />
            <span>AI-Powered Catalog Search</span>
          </div>

          <h1 className="text-2xl sm:text-4xl font-extrabold tracking-tight">
            {committed?.query ? (
              <span>Results for &ldquo;{committed.query}&rdquo;</span>
            ) : (
              <span>Explore Verified Hardware &amp; Electronics</span>
            )}
          </h1>

          <p className="text-xs sm:text-sm text-white/80 leading-relaxed">
            Search genuine electronics with live stock, verified pricing in ₹ INR, and atomic 15-minute price protection at checkout.
          </p>

          <form onSubmit={handleFormSubmit} className="pt-2 flex flex-col sm:flex-row gap-2.5">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                disabled={busy}
                placeholder="Search laptops, smartphones, headphones, monitors..."
                className="w-full pl-10 pr-4 py-3.5 text-xs sm:text-sm rounded-2xl bg-white text-slate-900 placeholder:text-slate-400 font-medium focus:outline-none focus:ring-2 focus:ring-emerald-400 shadow-sm"
              />
            </div>
            <button
              type="submit"
              disabled={busy}
              className="px-7 py-3.5 bg-emerald-500 hover:bg-emerald-400 active:scale-98 text-slate-950 font-black text-xs sm:text-sm rounded-2xl shadow-md transition-all inline-flex items-center justify-center gap-2"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              <span>{busy ? "Searching..." : "Search"}</span>
            </button>
            <button
              type="button"
              onClick={() => openAiDrawer({ pageType: "search" })}
              className="px-5 py-3.5 bg-white/10 hover:bg-white/20 active:scale-98 text-white font-bold text-xs sm:text-sm rounded-2xl backdrop-blur-md border border-white/20 transition-all inline-flex items-center justify-center gap-2"
            >
              <Sparkles className="h-4 w-4 text-emerald-300" />
              <span>Ask AI Shopper</span>
            </button>
          </form>

          <div className="pt-1 flex flex-wrap items-center gap-2 text-[11px]">
            <span className="text-white/60">Popular:</span>
            {["Laptops under ₹60,000", "Wireless Headphones", "Smartphones", "Monitors"].map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setQueryText(s);
                  executeSearch({ ...currentFilters(), query: s });
                }}
                className="rounded-full bg-white/10 hover:bg-white/20 px-3 py-1 text-white/90 transition-all"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-none">
        {CATEGORY_CHOICES.map((choice) => {
          const isSelected = categorySlug === choice.slug;
          return (
            <button
              key={choice.slug}
              type="button"
              onClick={() => handleCategorySelect(choice.slug)}
              className={`flex shrink-0 items-center gap-2 rounded-2xl px-4 py-2.5 text-xs font-bold transition-all ${
                isSelected
                  ? "bg-[#174c3c] text-white shadow-sm"
                  : "bg-white text-slate-700 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>{choice.icon}</span>
              <span>{choice.label}</span>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        <aside className="hidden lg:block lg:col-span-3 bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-6">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-[#174c3c]" />
              <h3 className="font-extrabold text-slate-900 text-sm">Filters</h3>
            </div>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={handleClearFilters}
                className="text-xs text-rose-600 hover:underline font-bold flex items-center gap-1"
              >
                <RotateCcw className="h-3 w-3" />
                <span>Reset</span>
              </button>
            )}
          </div>

          <div className="space-y-2.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Price Budget</label>
            <div className="space-y-1.5">
              {PRICE_PRESETS.map((p) => {
                const isSelected = maxPriceMinor === p.value;
                return (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => handlePriceSelect(p.value)}
                    className={`w-full flex items-center justify-between px-3.5 py-2 rounded-xl text-xs font-semibold transition-all ${
                      isSelected
                        ? "bg-[#e5f0e9] text-[#174c3c] font-bold border border-[#bcd7c4]"
                        : "text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    <span>{p.label}</span>
                    {isSelected && <Check className="h-3.5 w-3.5 text-[#174c3c]" />}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2.5 pt-4 border-t border-slate-100">
            <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Delivery Speed</label>
            <div className="space-y-1.5">
              {DELIVERY_CHOICES.map((d) => {
                const isSelected = maxDeliveryDays === d.value;
                return (
                  <button
                    key={d.label}
                    type="button"
                    onClick={() => {
                      setMaxDeliveryDays(d.value);
                      executeSearch({ ...currentFilters(), max_delivery_days: d.value });
                    }}
                    className={`w-full flex items-center justify-between px-3.5 py-2 rounded-xl text-xs font-semibold transition-all ${
                      isSelected
                        ? "bg-[#e5f0e9] text-[#174c3c] font-bold border border-[#bcd7c4]"
                        : "text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    <span>{d.label}</span>
                    {isSelected && <Check className="h-3.5 w-3.5 text-[#174c3c]" />}
                  </button>
                );
              })}
            </div>
          </div>

          {["all", "laptops", "laptop", "phones", "smartphones", "smartphone"].includes(categorySlug) && (
            <>
              <div className="space-y-2.5 pt-4 border-t border-slate-100">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">RAM (Memory)</label>
                <div className="grid grid-cols-2 gap-1.5">
                  {MEMORY_CHOICES.map((m) => {
                    const isSelected = minMemoryGb === m.value;
                    return (
                      <button
                        key={m.label}
                        type="button"
                        onClick={() => {
                          setMinMemoryGb(m.value);
                          executeSearch({ ...currentFilters(), min_memory_gb: m.value });
                        }}
                        className={`py-2 px-3 text-center rounded-xl text-xs font-bold border transition-all ${
                          isSelected
                            ? "bg-[#174c3c] border-[#174c3c] text-white"
                            : "bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100"
                        }`}
                      >
                        {m.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2.5 pt-4 border-t border-slate-100">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Storage (SSD/HDD)</label>
                <div className="grid grid-cols-2 gap-1.5">
                  {STORAGE_CHOICES.map((s) => {
                    const isSelected = minStorageGb === s.value;
                    return (
                      <button
                        key={s.label}
                        type="button"
                        onClick={() => {
                          setMinStorageGb(s.value);
                          executeSearch({ ...currentFilters(), min_storage_gb: s.value });
                        }}
                        className={`py-2 px-3 text-center rounded-xl text-xs font-bold border transition-all ${
                          isSelected
                            ? "bg-[#174c3c] border-[#174c3c] text-white"
                            : "bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100"
                        }`}
                      >
                        {s.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </aside>

        <main className="lg:col-span-9 space-y-6">
          <div className="flex items-center justify-between bg-white px-5 py-3.5 rounded-2xl border border-slate-200 text-xs font-medium text-slate-600">
            <div>
              {phase === "loading" ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-[#174c3c]" />
                  <span>Loading products...</span>
                </span>
              ) : (
                <span>
                  Showing <strong className="text-slate-900">{activeOffers.length}</strong> verified items
                </span>
              )}
            </div>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setMobileFilterOpen(!mobileFilterOpen)}
                className="lg:hidden flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-100 text-slate-700 font-bold"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" />
                <span>Filters</span>
              </button>

              <button
                type="button"
                onClick={() => openAiDrawer({ pageType: "search" })}
                className="flex items-center gap-1.5 text-[#174c3c] font-bold hover:underline"
              >
                <Sparkles className="h-3.5 w-3.5" />
                <span>Ask AI to Recommend</span>
              </button>
            </div>
          </div>

          {phase === "loading" && (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-6">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="h-96 rounded-3xl bg-white border border-slate-200 p-4 space-y-4 animate-pulse">
                  <div className="aspect-[1.1] rounded-2xl bg-slate-100" />
                  <div className="h-4 bg-slate-100 rounded-md w-3/4" />
                  <div className="h-4 bg-slate-100 rounded-md w-1/2" />
                  <div className="h-8 bg-slate-100 rounded-xl w-full pt-4" />
                </div>
              ))}
            </div>
          )}

          {phase === "failed" && (
            <div className="rounded-3xl border border-rose-200 bg-rose-50/60 p-8 sm:p-12 text-center space-y-4">
              <h3 className="text-lg font-bold text-rose-950">Unable to load search results</h3>
              <p className="text-xs text-rose-800 max-w-md mx-auto">
                {errorMessage || "We encountered an issue querying the catalog. Please try refreshing or ask the AI assistant."}
              </p>
              <div className="flex justify-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => executeSearch(currentFilters())}
                  className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs"
                >
                  Try Again
                </button>
                <button
                  type="button"
                  onClick={handleClearFilters}
                  className="px-5 py-2.5 bg-white border border-slate-200 text-slate-700 font-bold text-xs rounded-xl hover:bg-slate-50"
                >
                  Clear Filters
                </button>
              </div>
            </div>
          )}

          {phase === "ready" && activeOffers.length === 0 && (
            <div className="rounded-3xl border border-slate-200 bg-white p-8 sm:p-14 text-center space-y-5 shadow-xs">
              <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-slate-100 text-2xl">
                🔍
              </div>
              <div className="space-y-1.5">
                <h3 className="text-xl font-bold text-slate-900">No matching products found</h3>
                <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
                  We couldn&apos;t find any active products matching all your selected filters. Try broadening your criteria or ask our AI assistant.
                </p>
              </div>
              <div className="flex flex-wrap justify-center gap-3 pt-2">
                <button
                  type="button"
                  onClick={handleClearFilters}
                  className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-xs"
                >
                  View All Products
                </button>
                <button
                  type="button"
                  onClick={() => openAiDrawer({ pageType: "search" })}
                  className="px-5 py-2.5 bg-[#e5f0e9] hover:bg-[#d4e8da] text-[#174c3c] font-bold text-xs rounded-xl border border-[#bcd7c4] flex items-center gap-1.5"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  <span>Ask AI Assistant</span>
                </button>
              </div>
            </div>
          )}

          {phase === "ready" && activeOffers.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-6">
              {activeOffers.map((offer, idx) => (
                <OfferCard
                  key={offer.offer_id}
                  offer={offer}
                  catalogSource={outcome?.catalogSource}
                  isBestMatch={idx === 0 && Boolean(committed?.query)}
                />
              ))}
            </div>
          )}
        </main>
      </div>

      {/* Mobile Filter Slide-Over Drawer */}
      {mobileFilterOpen && (
        <div className="fixed inset-0 z-50 lg:hidden flex justify-end">
          <div
            className="fixed inset-0 bg-black/40 backdrop-blur-xs transition-opacity"
            onClick={() => setMobileFilterOpen(false)}
            aria-hidden="true"
          />
          <div className="relative w-full max-w-xs bg-white h-full shadow-2xl flex flex-col z-10 overflow-y-auto">
            <div className="flex items-center justify-between p-5 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-[#174c3c]" />
                <h3 className="font-extrabold text-slate-900 text-sm">Filters</h3>
              </div>
              <button
                type="button"
                onClick={() => setMobileFilterOpen(false)}
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="p-5 space-y-6 flex-1">
              <div className="space-y-2">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Price Budget</label>
                <div className="space-y-1">
                  {PRICE_PRESETS.map((p) => {
                    const isSelected = maxPriceMinor === p.value;
                    return (
                      <button
                        key={p.label}
                        type="button"
                        onClick={() => handlePriceSelect(p.value)}
                        className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-semibold transition-all ${
                          isSelected
                            ? "bg-[#e5f0e9] text-[#174c3c] font-bold border border-[#bcd7c4]"
                            : "text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        <span>{p.label}</span>
                        {isSelected && <Check className="h-3.5 w-3.5 text-[#174c3c]" />}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2 pt-4 border-t border-slate-100">
                <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Delivery Speed</label>
                <div className="space-y-1">
                  {DELIVERY_CHOICES.map((d) => {
                    const isSelected = maxDeliveryDays === d.value;
                    return (
                      <button
                        key={d.label}
                        type="button"
                        onClick={() => {
                          setMaxDeliveryDays(d.value);
                          executeSearch({ ...currentFilters(), max_delivery_days: d.value });
                        }}
                        className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs font-semibold transition-all ${
                          isSelected
                            ? "bg-[#e5f0e9] text-[#174c3c] font-bold border border-[#bcd7c4]"
                            : "text-slate-600 hover:bg-slate-50"
                        }`}
                      >
                        <span>{d.label}</span>
                        {isSelected && <Check className="h-3.5 w-3.5 text-[#174c3c]" />}
                      </button>
                    );
                  })}
                </div>
              </div>

              {["all", "laptops", "laptop", "phones", "smartphones", "smartphone"].includes(categorySlug) && (
                <>
                  <div className="space-y-2 pt-4 border-t border-slate-100">
                    <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">RAM (Memory)</label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {MEMORY_CHOICES.map((m) => {
                        const isSelected = minMemoryGb === m.value;
                        return (
                          <button
                            key={m.label}
                            type="button"
                            onClick={() => {
                              setMinMemoryGb(m.value);
                              executeSearch({ ...currentFilters(), min_memory_gb: m.value });
                            }}
                            className={`py-2 px-2.5 text-center rounded-xl text-xs font-bold border transition-all ${
                              isSelected
                                ? "bg-[#174c3c] border-[#174c3c] text-white"
                                : "bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100"
                            }`}
                          >
                            {m.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="space-y-2 pt-4 border-t border-slate-100">
                    <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Storage</label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {STORAGE_CHOICES.map((s) => {
                        const isSelected = minStorageGb === s.value;
                        return (
                          <button
                            key={s.label}
                            type="button"
                            onClick={() => {
                              setMinStorageGb(s.value);
                              executeSearch({ ...currentFilters(), min_storage_gb: s.value });
                            }}
                            className={`py-2 px-2.5 text-center rounded-xl text-xs font-bold border transition-all ${
                              isSelected
                                ? "bg-[#174c3c] border-[#174c3c] text-white"
                                : "bg-slate-50 border-slate-200 text-slate-700 hover:bg-slate-100"
                            }`}
                          >
                            {s.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </>
              )}
            </div>

            <div className="p-4 border-t border-slate-100 bg-slate-50 flex gap-2">
              <button
                type="button"
                onClick={handleClearFilters}
                className="flex-1 py-2.5 bg-white border border-slate-200 text-slate-700 text-xs font-bold rounded-xl hover:bg-slate-100"
              >
                Reset
              </button>
              <button
                type="button"
                onClick={() => setMobileFilterOpen(false)}
                className="flex-1 py-2.5 bg-[#174c3c] text-white text-xs font-bold rounded-xl hover:bg-[#103c2f]"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="p-12 text-center text-slate-500 font-bold text-sm">
          Loading catalog...
        </div>
      }
    >
      <SearchAndFilterContent />
    </Suspense>
  );
}
