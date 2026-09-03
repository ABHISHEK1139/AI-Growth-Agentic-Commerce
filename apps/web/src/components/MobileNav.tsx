"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Heart, Home, Package, Search, ShoppingBag, Sparkles } from "lucide-react";
import { useStore } from "@/context/StoreContext";

export function MobileNav() {
  const { cart, openAiDrawer } = useStore();
  const pathname = usePathname();
  const totalCartCount = cart.reduce((total, item) => total + item.quantity, 0);

  const isActive = (path: string) => {
    if (path === "/") return pathname === "/";
    return pathname.startsWith(path);
  };

  const linkClass = (path: string) =>
    `grid place-items-center gap-1 py-1 text-[10px] font-semibold transition-colors ${
      isActive(path) ? "text-[#174c3c] font-bold" : "text-[#526058]"
    }`;

  return (
    <nav className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-6 border-t border-[#e6e8df] bg-white/95 px-2 py-2 backdrop-blur-md md:hidden" aria-label="Mobile navigation">
      <Link href="/" className={linkClass("/")} aria-label="Home">
        <Home className="h-4 w-4" />
        <span>Home</span>
      </Link>
      <Link href="/search" className={linkClass("/search")} aria-label="Search products">
        <Search className="h-4 w-4" />
        <span>Search</span>
      </Link>
      <Link href="/cart" className={`relative ${linkClass("/cart")}`} aria-label={`Shopping bag with ${totalCartCount} items`}>
        <ShoppingBag className="h-4 w-4" />
        {totalCartCount > 0 && (
          <span className="absolute top-0 right-1/4 grid h-3.5 w-3.5 place-items-center rounded-full bg-[#e87544] text-[8px] font-bold text-white">
            {totalCartCount}
          </span>
        )}
        <span>Bag</span>
      </Link>
      <button
        onClick={() => openAiDrawer({ pageType: "home" })}
        className="grid place-items-center gap-1 py-1 text-[10px] font-bold text-[#174c3c]"
        aria-label="Open AI shopping assistant"
      >
        <span className="grid h-7 w-7 place-items-center rounded-full bg-[#174c3c] text-white shadow-sm">
          <Sparkles className="h-3.5 w-3.5" />
        </span>
        <span>Ask</span>
      </button>
      <Link href="/orders" className={linkClass("/orders")} aria-label="Your orders">
        <Package className="h-4 w-4" />
        <span>Orders</span>
      </Link>
      <Link href="/wishlist" className={linkClass("/wishlist")} aria-label="Saved products">
        <Heart className="h-4 w-4" />
        <span>Saved</span>
      </Link>
    </nav>
  );
}
