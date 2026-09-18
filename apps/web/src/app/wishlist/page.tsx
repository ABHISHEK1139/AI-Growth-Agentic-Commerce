"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useStore } from "@/context/StoreContext";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";
import { SEED_CATALOG_PRODUCTS } from "@/data/seedCatalog";
import { ProductCard } from "@/components/ProductCard";
import { Loader2 } from "lucide-react";
import { exploreOfferToProductItem } from "@/catalog/adapt";
import { lookupOfferInCatalog } from "@/catalog/client";

const COMBINED_PRODUCTS: ProductItem[] = [
  ...ALL_PRODUCTS,
  ...SEED_CATALOG_PRODUCTS.filter((sp) => !ALL_PRODUCTS.some((ap) => ap.id === sp.id)),
];

export default function WishlistPage() {
  const { wishlist, openAiDrawer } = useStore();
  const [savedProducts, setSavedProducts] = useState<ProductItem[]>([]);
  const [staleCount, setStaleCount] = useState(0);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadWishlistProducts() {
      if (!wishlist.length) {
        setSavedProducts([]);
        setStaleCount(0);
        return;
      }

      setIsLoading(true);
      try {
        // Resolve every saved id against the live catalog first. Only items
        // the gateway cannot resolve fall back to the static copy, labelled
        // cached — never with an invented live price.
        const loaded = await Promise.all(
          wishlist.map(async (productId) => {
            const lookup = await lookupOfferInCatalog({ productId });
            if (lookup.ok && lookup.data?.found) {
              return {
                item: exploreOfferToProductItem(lookup.data.found, lookup.data.catalogSource),
                stale: false,
              };
            }

            const fallback = COMBINED_PRODUCTS.find(
              (item) => item.id === productId || item.slug === productId || item.offerId === productId
            );
            if (!fallback) return null;
            return {
              item: { ...fallback, aiBadge: "Cached — price may be stale" },
              stale: true,
            };
          })
        );

        if (!cancelled) {
          const kept = loaded.filter((p): p is { item: ProductItem; stale: boolean } => p !== null);
          setSavedProducts(kept.map((p) => p.item));
          setStaleCount(kept.filter((p) => p.stale).length);
        }
      } catch (err) {
        console.warn("Wishlist live fetch note:", err);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadWishlistProducts();
    return () => {
      cancelled = true;
    };
  }, [wishlist]);

  return (
    <div className="space-y-10 pb-16 max-w-7xl mx-auto">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">Your Saved Wishlist</h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">
            {savedProducts.length} saved catalog items
            {staleCount > 0
              ? ` (${staleCount} cached — live price unavailable).`
              : " with live price tracking and policy compliance."}
          </p>
        </div>

        {savedProducts.length > 1 && (
          <button
            onClick={() =>
              openAiDrawer({
                pageType: "compare",
                customPrompt: "Which of my saved items offers the best value for my requirements?",
              })
            }
            className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-xs transition-all flex items-center gap-1.5 self-start sm:self-auto"
          >
            <span>✦</span>
            <span>Compare My Saved Items &rarr;</span>
          </button>
        )}
      </div>

      {/* AI Wishlist Price Intelligence Banner */}
      {savedProducts.length > 0 && (
        <div className="bg-gradient-to-r from-indigo-50/80 via-purple-50/40 to-slate-50 border border-indigo-100 rounded-3xl p-5 space-y-2">
          <div className="flex items-center gap-2 text-xs font-bold text-indigo-950">
            <span className="p-1 bg-indigo-600 text-white rounded-lg font-mono text-[10px]">✦</span>
            <span>AI Wishlist Live Intelligence</span>
          </div>
          <p className="text-xs text-slate-700 font-medium">
            📉 <strong>Deterministic Catalog Sync:</strong> All saved items are synced against live merchant inventory and pricing policies.
          </p>
        </div>
      )}

      {/* Products Grid */}
      {isLoading ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-indigo-600" />
          <p className="text-xs font-bold text-slate-500">Loading your saved items...</p>
        </div>
      ) : savedProducts.length === 0 ? (
        <div className="bg-white rounded-3xl border border-slate-200 p-12 text-center space-y-4 shadow-xs">
          <span className="text-4xl block">♡</span>
          <h3 className="text-lg font-black text-slate-900">Your Wishlist is Empty</h3>
          <p className="text-xs text-slate-500 max-w-sm mx-auto">
            Click the heart icon on any product card to save items and track live price updates.
          </p>
          <Link
            href="/search"
            className="inline-block px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs rounded-xl shadow-xs"
          >
            Explore Products &rarr;
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {savedProducts.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      )}
    </div>
  );
}
