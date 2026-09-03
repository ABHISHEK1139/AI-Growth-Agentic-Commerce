"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Boxes,
  Compass,
  FileSpreadsheet,
  History,
  LayoutDashboard,
  Megaphone,
  Receipt,
  Scale,
  Shield,
  Store,
} from "lucide-react";

const NAV_ITEMS = [
  { label: "Overview", href: "/merchant", icon: LayoutDashboard },
  { label: "AI Campaigns", href: "/merchant/campaigns", icon: Megaphone },
  { label: "Catalog", href: "/merchant/catalog", icon: Boxes },
  { label: "Inventory", href: "/merchant/inventory", icon: FileSpreadsheet },
  { label: "Policy Controls", href: "/merchant/policy", icon: Scale },
  { label: "Audit Ledger", href: "/merchant/audit", icon: History },
  { label: "Transactions", href: "/merchant/transactions", icon: Receipt },
  { label: "API & Agents", href: "/merchant/api-usage", icon: Compass },
];

export default function MerchantLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  const isActive = (href: string) => {
    if (href === "/merchant") return pathname === "/merchant";
    return pathname.startsWith(href);
  };

  return (
    <div className="min-h-screen bg-[#f8faf9] text-slate-900 -mx-4 -mt-4 sm:-mx-6 sm:-mt-10 lg:-mx-10">
      {/* Dedicated Merchant Portal Navigation Header */}
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur-md shadow-xs">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <Link
              href="/merchant"
              className="flex items-center gap-2.5 transition-transform hover:scale-[1.02]"
            >
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#174c3c] text-sm font-black text-white shadow-sm">
                M
              </span>
              <div>
                <span className="font-display text-lg font-black tracking-tight text-slate-900 block leading-tight">
                  agentpay
                </span>
                <span className="text-[10px] font-extrabold uppercase tracking-wider text-[#174c3c] block">
                  Merchant Console
                </span>
              </div>
            </Link>

            <span className="hidden sm:inline-block h-4 w-px bg-slate-200 ml-2" />

            <span className="hidden sm:inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-bold text-emerald-700 border border-emerald-200/60">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              Gateway Connected
            </span>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <Link
              href="/scenarios"
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-1.5 text-xs font-bold text-amber-900 hover:bg-amber-100/80 transition-colors"
              title="Adversarial Security Suite & Failure Simulator"
            >
              <Shield className="h-3.5 w-3.5 text-amber-700" />
              <span className="hidden sm:inline">Security Lab</span>
            </Link>

            <Link
              href="/"
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-100 hover:text-slate-900 transition-colors shadow-2xs"
            >
              <Store className="h-3.5 w-3.5 text-[#174c3c]" />
              <span>← Back to Storefront</span>
            </Link>
          </div>
        </div>

        {/* Merchant Sub-navigation Tabs */}
        <div className="border-t border-slate-100 bg-slate-50/80 px-4 sm:px-6 lg:px-8">
          <div className="mx-auto flex max-w-7xl items-center gap-1 overflow-x-auto py-1.5 scrollbar-none">
            {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-bold transition-all duration-150 shrink-0 ${
                    active
                      ? "bg-white text-[#174c3c] shadow-xs border border-slate-200/80"
                      : "text-slate-600 hover:bg-white/60 hover:text-slate-900"
                  }`}
                >
                  <Icon className={`h-3.5 w-3.5 ${active ? "text-[#174c3c]" : "text-slate-400"}`} />
                  <span>{label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      </header>

      {/* Main Merchant Operations Content */}
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        {children}
      </div>
    </div>
  );
}
