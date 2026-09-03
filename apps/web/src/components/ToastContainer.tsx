"use client";

import React from "react";
import Link from "next/link";
import { ShoppingBag, Heart, Scale, CheckCircle2, X, ArrowRight } from "lucide-react";
import { useStore } from "@/context/StoreContext";

export function ToastContainer() {
  const { toasts, removeToast } = useStore();

  if (!toasts || toasts.length === 0) return null;

  return (
    <div className="fixed bottom-6 left-6 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none sm:left-auto sm:right-6">
      {toasts.map((t) => {
        const Icon =
          t.type === "cart"
            ? ShoppingBag
            : t.type === "wishlist"
            ? Heart
            : t.type === "compare"
            ? Scale
            : CheckCircle2;

        return (
          <div
            key={t.id}
            className="pointer-events-auto flex items-center justify-between gap-3 p-3.5 bg-[#17231e]/95 text-white rounded-2xl shadow-2xl backdrop-blur-md border border-white/10 transition-all duration-300 animate-fade-in-up"
          >
            <div className="flex items-center gap-3 min-w-0 flex-1">
              <div className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[#174c3c] text-white">
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold leading-snug line-clamp-2">{t.message}</p>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              {t.actionHref && t.actionLabel && (
                <Link
                  href={t.actionHref}
                  onClick={() => removeToast(t.id)}
                  className="inline-flex items-center gap-1 rounded-xl bg-white/15 hover:bg-white/25 px-2.5 py-1.5 text-[11px] font-bold text-white transition-all"
                >
                  <span>{t.actionLabel}</span>
                  <ArrowRight className="h-3 w-3" />
                </Link>
              )}
              {t.onAction && t.actionLabel && !t.actionHref && (
                <button
                  type="button"
                  onClick={() => {
                    t.onAction?.();
                    removeToast(t.id);
                  }}
                  className="inline-flex items-center gap-1 rounded-xl bg-white/15 hover:bg-white/25 px-2.5 py-1.5 text-[11px] font-bold text-white transition-all"
                >
                  <span>{t.actionLabel}</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => removeToast(t.id)}
                className="rounded-lg p-1 text-white/60 hover:text-white transition-colors"
                aria-label="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
