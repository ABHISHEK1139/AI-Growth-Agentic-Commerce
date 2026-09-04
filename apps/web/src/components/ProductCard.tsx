"use client";

import Link from "next/link";
import { Heart, Scale, ShoppingBag, Sparkles, Star, Truck } from "lucide-react";
import { formatMinorToMajor } from "@/lib/money";
import { ProductItem } from "@/data/products";
import { useStore } from "@/context/StoreContext";
import { useState } from "react";
import { defaultImageForCategory } from "@/catalog/adapt";

export function ProductCard({ product, highlightReason, isBestMatch }: { product: ProductItem; highlightReason?: string; isBestMatch?: boolean }) {
  const { addToCart, wishlist, toggleWishlist, compareList, toggleCompare, openAiDrawer } = useStore();
  const saved = wishlist.includes(product.id);
  const compared = compareList.includes(product.id);
  const discount = product.originalPriceMinor > product.priceMinor ? Math.round((1 - product.priceMinor / product.originalPriceMinor) * 100) : 0;
  const [addedToCart, setAddedToCart] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);

  const fallbackImg = defaultImageForCategory(product.category, product.title, product.brand);
  const initialImg = (product.imageUrl && !product.imageUrl.endsWith(".gif") && !product.imageUrl.includes("01RmK") && !product.imageUrl.includes("placeholder"))
    ? product.imageUrl
    : fallbackImg;
  const [currentImg, setCurrentImg] = useState(initialImg);

  const handleAddToCart = () => {
    addToCart(product, 1);
    setAddedToCart(true);
    setTimeout(() => setAddedToCart(false), 1500);
  };

  return <article className={`group relative flex h-full flex-col overflow-hidden rounded-[22px] border bg-white transition-all duration-300 hover:-translate-y-1.5 hover:shadow-hover ${isBestMatch ? "border-[#174c3c] ring-1 ring-[#174c3c] shadow-soft" : "border-[#e6e8df] hover:border-[#c8d4cc]"}`}>
    {/* Image Section */}
    <div className="relative aspect-[1.1] overflow-hidden bg-[#eef1eb]">
      {/* Skeleton shimmer while image loads */}
      {!imageLoaded && (
        <div className="absolute inset-0 skeleton-pulse" />
      )}
      <Link href={`/product/${product.id}`} className="block h-full">
        <img
          src={currentImg}
          alt={product.title}
          className={`h-full w-full object-cover transition-all duration-500 ease-out group-hover:scale-110 ${imageLoaded ? "opacity-100" : "opacity-0"}`}
          onLoad={() => setImageLoaded(true)}
          onError={() => {
            if (currentImg !== fallbackImg) setCurrentImg(fallbackImg);
          }}
        />
      </Link>
      {/* Gradient overlay on hover */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/10 via-transparent to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
      <div className="absolute left-3 top-3 flex flex-col items-start gap-1.5">
        {isBestMatch && <span className="rounded-full bg-[#174c3c] px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-white shadow-sm animate-fade-in">Best match</span>}
        {discount > 0 && <span className="rounded-full bg-white/95 px-2.5 py-1 text-[10px] font-bold text-[#c65027] shadow-sm backdrop-blur-sm">Save {discount}%</span>}
      </div>
      <button onClick={() => toggleWishlist(product.id)} className={`absolute right-3 top-3 grid h-9 w-9 place-items-center rounded-full bg-white/95 shadow-sm backdrop-blur-sm transition-all duration-200 hover:scale-110 active:scale-90 ${saved ? "text-[#c65027]" : "text-[#526058] hover:text-[#c65027]"}`} aria-label={saved ? "Remove from saved products" : "Save product"}><Heart className={`h-4 w-4 transition-transform duration-200 ${saved ? "fill-current scale-110" : ""}`} /></button>
      {/* Quick view and Ask AI overlay on hover */}
      <div className="absolute bottom-3 left-3 right-3 flex items-center gap-1.5 translate-y-2 opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100">
        <Link href={`/product/${product.id}`} className="flex-1 flex items-center justify-center gap-1 rounded-xl bg-white/95 py-2 text-[11px] font-bold text-[#174c3c] backdrop-blur-md transition-all duration-200 hover:bg-white hover:shadow-md">
          Quick view
        </Link>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            openAiDrawer({ pageType: "product", product });
          }}
          className="flex items-center justify-center gap-1 rounded-xl bg-[#174c3c] px-3 py-2 text-[11px] font-bold text-white shadow-sm transition-all duration-200 hover:bg-[#103c2f] hover:scale-105"
          title="Ask AI Assistant about this product"
        >
          <Sparkles className="h-3 w-3 text-[#a9d1b6]" />
          <span>Ask AI</span>
        </button>
      </div>
    </div>
    {/* Content Section */}
    <div className="flex flex-1 flex-col p-4 sm:p-5">
      <div className="mb-2 flex items-center justify-between gap-3 text-[11px] font-semibold"><span className="uppercase tracking-[.12em] text-[#174c3c]">{product.brand}</span><span className="inline-flex items-center gap-1 text-[#526058]"><Star className="h-3.5 w-3.5 fill-[#e8a33e] text-[#e8a33e]" />{product.rating}</span></div>
      <Link href={`/product/${product.id}`}><h3 className="line-clamp-2 min-h-[42px] text-sm font-bold leading-5 text-[#17231e] transition-colors duration-200 group-hover:text-[#174c3c]">{product.title}</h3></Link>
      <p className="mt-1.5 truncate text-xs text-[#68736d]">{product.shortSpecs}</p>
      <div className="mt-3 inline-flex w-fit items-center gap-1.5 rounded-full bg-[#e5f0e9] px-2.5 py-1 text-[10px] font-semibold text-[#174c3c] transition-colors duration-200 group-hover:bg-[#d4e8da]"><Sparkles className="h-3 w-3" />{highlightReason || product.aiBadge}</div>
      <div className="mt-4 flex items-end justify-between border-t border-[#edf0ea] pt-4"><div><p className="text-lg font-extrabold tracking-tight text-[#17231e]">{formatMinorToMajor(product.priceMinor, product.currency)}</p>{discount > 0 && <p className="text-[11px] text-[#8a938e] line-through">{formatMinorToMajor(product.originalPriceMinor, product.currency)}</p>}</div><span className="inline-flex items-center gap-1 text-[10px] font-semibold text-[#526058]"><Truck className="h-3.5 w-3.5" /> {product.deliveryDays} {product.deliveryDays === 1 ? "day" : "days"}</span></div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <button onClick={() => toggleCompare(product.id)} className={`inline-flex h-9 items-center justify-center gap-1 rounded-xl border text-xs font-bold transition-all duration-200 active:scale-95 ${compared ? "border-[#174c3c] bg-[#e5f0e9] text-[#174c3c]" : "border-[#dfe4dd] text-[#526058] hover:border-[#174c3c] hover:bg-[#f0f7f3]"}`}><Scale className="h-3.5 w-3.5" />{compared ? "Added" : "Compare"}</button>
        <button onClick={handleAddToCart} className={`inline-flex h-9 items-center justify-center gap-1 rounded-xl text-xs font-bold transition-all duration-200 active:scale-95 ${addedToCart ? "bg-[#1d8c5c] text-white" : "bg-[#174c3c] text-white hover:bg-[#103c2f] hover:shadow-md"}`}><ShoppingBag className="h-3.5 w-3.5" />{addedToCart ? "Added!" : "Add"}</button>
      </div>
    </div>
  </article>;
}
