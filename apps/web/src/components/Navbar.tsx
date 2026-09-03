"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Heart, Menu, Search, ShoppingBag, Scale, X, TrendingUp, Package, Sparkles } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useStore } from "@/context/StoreContext";

const links = [
  ["Laptops", "/category/laptops"],
  ["Phones", "/category/phones"],
  ["Audio", "/category/audio"],
  ["Monitors", "/category/monitors"],
  ["Keyboards", "/category/keyboards"],
  ["Deals", "/search?deals=true"],
] as const;

const trendingSearches = [
  "Laptop for coding under ₹70,000",
  "Noise cancelling headphones for travel",
  "Phone with best camera under ₹30,000",
  "4K monitor for programming",
  "True wireless earbuds with long battery",
];

export function Navbar() {
  const router = useRouter();
  const pathname = usePathname();
  const { cart, wishlist, compareList, openCartDrawer } = useStore();

  const [searchQuery, setSearchQuery] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [searchFocused, setSearchFocused] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const totalCartCount = cart.reduce((total, item) => total + item.quantity, 0);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setSearchFocused(true);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) {
        setSearchFocused(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;
    setSearchFocused(false);
    router.push(`/search?q=${encodeURIComponent(query)}`);
  };

  const handleTrendingClick = (query: string) => {
    setSearchQuery(query);
    setSearchFocused(false);
    router.push(`/search?q=${encodeURIComponent(query)}`);
  };

  if (pathname?.startsWith("/merchant") || pathname?.startsWith("/scenarios")) {
    return null;
  }

  return (
    <header
      className={`sticky top-0 z-40 border-b transition-all duration-300 ${
        scrolled ? "border-transparent navbar-scrolled" : "border-[#e6e8df] bg-[#f7f7f2]/95 backdrop-blur-xl"
      }`}
    >
      <div className="mx-auto flex h-[74px] max-w-[1440px] items-center gap-4 px-4 sm:px-6 lg:px-10">
        <Link href="/" className="flex shrink-0 items-center gap-2.5 transition-transform duration-200 hover:scale-[1.02]" aria-label="AgentPay home">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#174c3c] text-sm font-black text-white shadow-sm transition-shadow duration-300 hover:shadow-md">
            A
          </span>
          <span className="font-display text-xl font-extrabold tracking-tight text-[#17231e]">
            agentpay
          </span>
        </Link>

        <div ref={searchRef} className="relative mx-auto hidden max-w-[620px] flex-1 md:block">
          <form onSubmit={submit} className="relative">
            <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-[#68736d] transition-colors" />
            <input
              ref={inputRef}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => setSearchFocused(true)}
              placeholder="Search products or describe what you need..."
              className="h-11 w-full rounded-full border border-[#dfe4dd] bg-white px-11 pr-14 text-sm text-[#17231e] outline-none transition-all duration-300 placeholder:text-[#8a938e] focus:border-[#174c3c] focus:ring-4 focus:ring-[#e5f0e9] focus:shadow-sm"
            />
            {!searchQuery && (
              <div className="absolute right-3.5 top-1/2 -translate-y-1/2 hidden sm:flex items-center pointer-events-none">
                <kbd className="rounded border border-[#dfe4dd] bg-[#f7f7f2] px-1.5 py-0.5 text-[10px] font-mono font-semibold text-[#8a938e]">
                  ⌘K
                </kbd>
              </div>
            )}
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-[#8a938e] hover:text-[#17231e]"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </form>

          {/* Natural search suggestions */}
          {searchFocused && !searchQuery && (
            <div className="absolute left-0 right-0 top-[calc(100%+8px)] z-50 rounded-2xl border border-[#e6e8df] bg-white p-4 shadow-xl animate-fade-in-down" style={{ animationDuration: "0.15s" }}>
              <p className="mb-2.5 flex items-center gap-1.5 text-xs font-bold text-[#68736d]">
                <TrendingUp className="h-3.5 w-3.5 text-[#174c3c]" /> Natural Language & Need Searches
              </p>
              <div className="flex flex-col gap-1">
                {trendingSearches.map((query) => (
                  <button
                    key={query}
                    onClick={() => handleTrendingClick(query)}
                    className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm text-[#365046] transition-all duration-150 hover:bg-[#e5f0e9] hover:text-[#174c3c]"
                  >
                    <Sparkles className="h-3.5 w-3.5 shrink-0 text-[#174c3c]" />
                    <span>{query}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <nav className="ml-auto flex items-center gap-2">
          <Link
            href="/orders"
            className="relative hidden items-center gap-1.5 rounded-full px-3 py-2 text-sm font-semibold text-[#3d4942] transition-all duration-200 hover:bg-[#e5f0e9] hover:text-[#174c3c] sm:inline-flex"
            aria-label="View orders"
          >
            <Package className="h-4 w-4" />
            <span>Orders</span>
          </Link>

          {compareList.length > 0 && (
            <Link
              href="/compare"
              className="relative hidden rounded-full p-2.5 text-[#3d4942] transition-all duration-200 hover:bg-[#e5f0e9] hover:scale-105 sm:block"
              aria-label="Compare products"
            >
              <Scale className="h-5 w-5" />
              <span className="absolute -right-0.5 -top-0.5 grid h-4 w-4 place-items-center rounded-full bg-[#e87544] text-[9px] font-bold text-white">
                {compareList.length}
              </span>
            </Link>
          )}

          <Link
            href="/wishlist"
            className="relative rounded-full p-2.5 text-[#3d4942] transition-all duration-200 hover:bg-[#e5f0e9] hover:scale-105"
            aria-label="Saved products"
          >
            <Heart className="h-5 w-5" />
            {wishlist.length > 0 && (
              <span className="absolute -right-0.5 -top-0.5 grid h-4 w-4 place-items-center rounded-full bg-[#e87544] text-[9px] font-bold text-white">
                {wishlist.length}
              </span>
            )}
          </Link>

          <button
            type="button"
            onClick={openCartDrawer}
            className="relative inline-flex items-center gap-2 rounded-full bg-[#174c3c] px-3.5 py-2 text-sm font-bold text-white transition-all duration-200 hover:bg-[#103c2f] hover:shadow-md hover:scale-[1.02] active:scale-[0.98]"
            aria-label="Shopping bag"
          >
            <ShoppingBag className="h-4 w-4" />
            <span className="hidden sm:inline">Bag</span>
            {totalCartCount > 0 && (
              <span className="grid h-4 w-4 place-items-center rounded-full bg-[#e87544] text-[9px] font-bold text-white">
                {totalCartCount}
              </span>
            )}
          </button>

          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="rounded-full p-2.5 text-[#3d4942] transition-all duration-200 hover:bg-[#e5f0e9] active:scale-90 md:hidden"
            aria-label="Toggle navigation"
          >
            {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </nav>
      </div>

      <div
        className={`transition-all duration-300 overflow-hidden ${
          menuOpen ? "max-h-48 opacity-100" : "max-h-0 opacity-0 md:max-h-12 md:opacity-100"
        } border-t border-[#e6e8df] bg-[#f7f7f2] px-4 py-2.5 md:border-0 md:py-0`}
      >
        <div className="mx-auto flex max-w-[1440px] items-center gap-6 overflow-x-auto whitespace-nowrap text-sm font-medium text-[#526058] scrollbar-none md:px-10 md:pb-2.5">
          <Link href="/" className="font-bold text-[#174c3c] transition-colors">
            All Products
          </Link>
          {links.map(([label, href]) => (
            <Link
              key={label}
              href={href}
              className={`relative transition-all duration-200 hover:text-[#174c3c] ${
                label === "Deals" ? "font-semibold text-[#c65027]" : ""
              }`}
            >
              {label}
            </Link>
          ))}
        </div>
      </div>
    </header>
  );
}
