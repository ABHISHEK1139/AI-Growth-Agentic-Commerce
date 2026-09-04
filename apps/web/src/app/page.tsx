"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Headphones,
  Laptop,
  Monitor,
  ShieldCheck,
  Smartphone,
  Sparkles,
  Star,
  Truck,
  Undo2,
  Users,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { ProductCard } from "@/components/ProductCard";
import { useStore } from "@/context/StoreContext";
import { ALL_PRODUCTS, type ProductItem } from "@/data/products";
import { ScrollReveal } from "@/components/ScrollReveal";
import { TestimonialCarousel } from "@/components/TestimonialCarousel";
import { BrandMarquee } from "@/components/BrandMarquee";
import { HowItWorks } from "@/components/HowItWorks";
import { runCatalogSearch } from "@/catalog/search";
import { exploreOfferToProductItem } from "@/catalog/adapt";

const prompts = [
  "A laptop for coding under ₹75,000",
  "Headphones for long flights",
  "A clean work-from-home setup",
];

const categories = [
  {
    slug: "laptops",
    label: "Laptops",
    detail: "Work, create, play",
    Icon: Laptop,
    image:
      "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=800&q=80",
  },
  {
    slug: "phones",
    label: "Phones",
    detail: "Everyday essentials",
    Icon: Smartphone,
    image:
      "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=800&q=80",
  },
  {
    slug: "audio",
    label: "Audio",
    detail: "Hear every detail",
    Icon: Headphones,
    image:
      "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=800&q=80",
  },
  {
    slug: "monitors",
    label: "Workspace",
    detail: "Make room to focus",
    Icon: Monitor,
    image:
      "https://images.unsplash.com/photo-1586210579191-33b45e38fa2c?auto=format&fit=crop&w=800&q=80",
  },
];

export default function ConsumerHomePage() {
  const router = useRouter();
  const { openAiDrawer } = useStore();
  const [prompt, setPrompt] = useState("");
  const [picks, setPicks] = useState<ProductItem[]>(ALL_PRODUCTS.slice(0, 8));
  const [deals, setDeals] = useState<ProductItem[]>(
    ALL_PRODUCTS.filter((p) => p.originalPriceMinor > p.priceMinor).slice(0, 4)
  );
  const [allItems, setAllItems] = useState<ProductItem[]>(ALL_PRODUCTS);
  const [selectedCategory, setSelectedCategory] = useState("all");

  useEffect(() => {
    let cancelled = false;

    async function loadLiveCatalogFeatured() {
      try {
        const result = await runCatalogSearch({ limit: 32 });
        if (!cancelled && result.kind === "ok" && result.outcome.offers.length > 0) {
          const liveItems: ProductItem[] = result.outcome.offers.map((offer) =>
            exploreOfferToProductItem(offer, result.outcome.catalogSource)
          );

          if (liveItems.length > 0) {
            const merged = [
              ...ALL_PRODUCTS,
              ...liveItems.filter((li) => !ALL_PRODUCTS.some((ap) => ap.id === li.id)),
            ];
            setAllItems(merged);
            setPicks(merged.slice(0, 8));
            const liveDeals = merged.filter(
              (p) => p.originalPriceMinor > p.priceMinor
            );
            setDeals(liveDeals.length > 0 ? liveDeals.slice(0, 4) : merged.slice(4, 8));
          }
        }
      } catch (err) {
        console.warn("Home catalog load note:", err);
      }
    }

    loadLiveCatalogFeatured();
    return () => {
      cancelled = true;
    };
  }, []);

  const go = (query: string) => {
    if (!query.trim()) return;
    openAiDrawer({ pageType: "search", customPrompt: query });
    router.push(`/search?q=${encodeURIComponent(query)}&ai=true`);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    go(prompt);
  };

  return (
    <div className="space-y-16 pb-10 sm:space-y-24">
      {/* Hero Section */}
      <section
        className="relative isolate overflow-hidden rounded-[32px] bg-gradient-to-br from-[#174c3c] via-[#1a5844] to-[#0e3328] px-6 py-14 text-white shadow-lift sm:px-12 sm:py-20 lg:px-16 animated-gradient"
        style={{ backgroundSize: "200% 200%" }}
      >
        <div className="absolute inset-0 opacity-[.12] paper-grid" />
        <div className="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-[#e8a33e] opacity-25 blur-3xl animate-float-gentle" />
        <div className="absolute -bottom-40 left-1/3 h-80 w-80 rounded-full bg-[#92c5a3] opacity-20 blur-3xl animate-float-slow" />
        <div
          className="absolute right-1/4 top-1/2 h-40 w-40 rounded-full bg-[#e87544] opacity-15 blur-2xl animate-float-gentle"
          style={{ animationDelay: "2s" }}
        />
        <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.03)_1px,transparent_1px)] bg-[size:60px_60px]" />

        <div className="relative grid items-center gap-10 lg:grid-cols-[1.1fr_.9fr]">
          <div className="max-w-2xl">
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1.5 text-xs font-bold text-[#d6eadc] backdrop-blur-sm animate-fade-in-down">
              <Sparkles className="h-3.5 w-3.5 animate-bounce-subtle" /> Shopping, made more considered
            </p>
            <h1 className="font-display text-4xl font-extrabold leading-[1.05] tracking-tight sm:text-5xl lg:text-[3.5rem] animate-fade-in-up">
              Find things that{" "}
              <span className="relative text-[#b7ddc6]">
                <span className="relative z-10">fit your life.</span>
                <span className="absolute -bottom-1 left-0 h-3 w-full bg-[#e87544]/20 rounded-full -z-0" />
              </span>
            </h1>
            <p
              className="mt-5 max-w-xl text-base leading-7 text-[#d8e5dc] sm:text-lg animate-fade-in-up stagger-2"
              style={{ opacity: 0, animationFillMode: "forwards", animationDelay: "0.2s" }}
            >
              Tell AgentPay what matters. We will help you discover, compare and decide with clear answers at every step.
            </p>
            <form
              onSubmit={submit}
              className="mt-8 rounded-2xl bg-white p-2 shadow-xl transition-shadow duration-300 hover:shadow-2xl animate-fade-in-up stagger-3"
              style={{ opacity: 0, animationFillMode: "forwards", animationDelay: "0.3s" }}
            >
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder="What are you looking for?"
                  className="min-w-0 flex-1 rounded-xl px-4 py-3 text-sm text-[#17231e] outline-none placeholder:text-[#8a938e] transition-all focus:ring-2 focus:ring-[#e5f0e9]"
                />
                <button className="inline-flex items-center justify-center gap-2 rounded-xl bg-[#e87544] px-5 py-3 text-sm font-bold text-white transition-all duration-200 hover:bg-[#d56234] hover:shadow-lg hover:scale-[1.02] active:scale-[0.98]">
                  <Sparkles className="h-4 w-4" /> Ask AgentPay <ArrowRight className="h-4 w-4" />
                </button>
              </div>
            </form>
            <div
              className="mt-4 flex flex-wrap gap-2 animate-fade-in-up stagger-4"
              style={{ opacity: 0, animationFillMode: "forwards", animationDelay: "0.4s" }}
            >
              {prompts.map((item) => (
                <button
                  key={item}
                  onClick={() => go(item)}
                  className="rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[#e6f0e8] backdrop-blur-sm transition-all duration-200 hover:bg-white/15 hover:border-white/30 hover:scale-105"
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          {/* AI Assistant Preview Card */}
          <div
            className="hidden lg:block animate-slide-in-right"
            style={{ opacity: 0, animationFillMode: "forwards", animationDelay: "0.5s" }}
          >
            <div className="ml-auto max-w-sm rounded-[28px] border border-white/20 bg-[#f7f7f2] p-5 text-[#17231e] shadow-2xl transition-transform duration-300 hover:-translate-y-1 hover:shadow-[0_30px_60px_rgba(0,0,0,0.3)]">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="grid h-9 w-9 place-items-center rounded-full bg-[#e5f0e9] text-[#174c3c]">
                    <Sparkles className="h-4 w-4" />
                  </span>
                  <div>
                    <p className="text-sm font-bold">Your shopping assistant</p>
                    <p className="text-[11px] text-[#68736d]">Here when you need it</p>
                  </div>
                </div>
                <span className="h-2 w-2 rounded-full bg-[#5caa77] animate-pulse" />
              </div>
              <div className="mt-5 rounded-2xl bg-[#edf3ed] p-4 text-sm leading-6 text-[#365046]">
                &ldquo;I need a lightweight laptop for coding, under ₹75,000.&rdquo;
              </div>
              <div className="mt-3 rounded-2xl border border-[#e6e8df] p-4">
                <p className="text-xs font-bold text-[#174c3c]">I found 8 good matches</p>
                <p className="mt-1 text-xs leading-5 text-[#68736d]">
                  I will prioritise 16GB RAM, portability and reliable battery life.
                </p>
                <button
                  onClick={() => go(prompts[0])}
                  className="mt-3 text-xs font-bold text-[#174c3c] transition-colors hover:text-[#e87544]"
                >
                  See recommendations &rarr;
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Social proof bar */}
      <ScrollReveal>
        <section className="flex flex-wrap items-center justify-center gap-6 rounded-2xl bg-white/60 px-6 py-4 shadow-sm backdrop-blur-sm border border-[#e6e8df] sm:gap-10">
          <div className="flex items-center gap-2 text-sm">
            <Users className="h-4 w-4 text-[#174c3c]" />
            <span className="font-bold text-[#17231e]">10,000+</span>
            <span className="text-[#68736d]">happy customers</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <Star className="h-4 w-4 fill-[#e8a33e] text-[#e8a33e]" />
            <span className="font-bold text-[#17231e]">4.8/5</span>
            <span className="text-[#68736d]">average rating</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <ShieldCheck className="h-4 w-4 text-[#174c3c]" />
            <span className="font-bold text-[#17231e]">100%</span>
            <span className="text-[#68736d]">secure payments</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <Truck className="h-4 w-4 text-[#174c3c]" />
            <span className="font-bold text-[#17231e]">2-day</span>
            <span className="text-[#68736d]">express delivery</span>
          </div>
        </section>
      </ScrollReveal>

      {/* Brand Marquee */}
      <ScrollReveal>
        <section>
          <p className="mb-4 text-center text-xs font-bold uppercase tracking-[.15em] text-[#68736d]">
            Trusted brands, honest recommendations
          </p>
          <BrandMarquee />
        </section>
      </ScrollReveal>

      {/* How It Works */}
      <section>
        <ScrollReveal>
          <div className="mb-8 text-center">
            <p className="text-xs font-bold uppercase tracking-[.15em] text-[#174c3c]">Simple as 1-2-3</p>
            <h2 className="mt-2 text-2xl font-extrabold tracking-tight sm:text-3xl">How AgentPay works</h2>
            <p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-[#526058]">
              No more tab overload. Ask naturally, see comparisons that matter, and buy with full confidence.
            </p>
          </div>
        </ScrollReveal>
        <HowItWorks />
      </section>

      {/* Categories Section */}
      <section>
        <ScrollReveal>
          <div className="mb-7 flex items-end justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-[.15em] text-[#174c3c]">Start with a feeling</p>
              <h2 className="mt-2 text-2xl font-extrabold tracking-tight sm:text-3xl">Explore by category</h2>
            </div>
            <Link
              href="/search"
              className="hidden items-center gap-1 text-sm font-bold text-[#174c3c] transition-all duration-200 hover:gap-2 sm:inline-flex"
            >
              See everything <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </ScrollReveal>
        <div className="grid grid-cols-2 gap-3 sm:gap-5 lg:grid-cols-4">
          {categories.map(({ slug, label, detail, Icon, image }, index) => (
            <ScrollReveal key={slug} delay={index * 100} direction="up">
              <Link
                href={`/category/${slug}`}
                className="group relative aspect-[1.08] overflow-hidden rounded-[24px] bg-[#e5f0e9] transition-all duration-300 hover:-translate-y-1 hover:shadow-lift block"
              >
                <img
                  src={image}
                  alt=""
                  className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-110"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-[#102d23]/80 via-[#102d23]/20 to-transparent transition-all duration-300 group-hover:from-[#102d23]/90" />
                <div className="absolute bottom-0 w-full p-4 text-white">
                  <Icon className="mb-7 h-5 w-5 transition-transform duration-300 group-hover:scale-110" />
                  <h3 className="font-display text-lg font-bold transition-transform duration-300 group-hover:translate-x-1">
                    {label}
                  </h3>
                  <p className="mt-0.5 text-xs text-[#d6eadc] transition-all duration-300 group-hover:text-white">
                    {detail}
                  </p>
                </div>
              </Link>
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* Popular Products */}
      <section>
        <ScrollReveal>
          <div className="mb-6 flex flex-col sm:flex-row sm:items-end justify-between gap-4">
            <div>
              <p className="text-xs font-bold uppercase tracking-[.15em] text-[#174c3c]">Picked with care</p>
              <h2 className="mt-2 text-2xl font-extrabold tracking-tight sm:text-3xl">Popular right now</h2>
              <p className="mt-1 text-sm text-[#68736d]">Curated electronics, guaranteed 2-day delivery, verified prices.</p>
            </div>
            <Link
              href="/search"
              className="inline-flex items-center gap-1 text-sm font-bold text-[#174c3c] transition-all duration-200 hover:gap-2 self-start sm:self-auto"
            >
              View all <ArrowRight className="inline h-4 w-4" />
            </Link>
          </div>
        </ScrollReveal>

        {/* Category quick filter pills */}
        <div className="flex flex-wrap gap-2 mb-6">
          {[
            { id: "all", label: "✨ All Products" },
            { id: "laptop", label: "💻 Laptops" },
            { id: "smartphone", label: "📱 Smartphones" },
            { id: "audio", label: "🎧 Audio" },
            { id: "monitor", label: "🖥️ Monitors" },
            { id: "accessory", label: "⌨️ Keyboards & Accessories" },
          ].map((cat) => (
            <button
              key={cat.id}
              onClick={() => setSelectedCategory(cat.id)}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-all ${
                selectedCategory === cat.id
                  ? "bg-[#174c3c] text-white shadow-xs"
                  : "bg-white border border-[#e6e8df] text-[#526058] hover:bg-[#eef4f0] hover:text-[#174c3c]"
              }`}
            >
              {cat.label}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {(selectedCategory === "all"
            ? (allItems.length > 0 ? allItems.slice(0, 8) : picks)
            : allItems.filter((p) => {
                const cat = (p.category || "").toLowerCase();
                const title = (p.title || "").toLowerCase();
                if (selectedCategory === "accessory") {
                  return cat.includes("accessor") || cat.includes("keyboard") || title.includes("keyboard") || title.includes("mouse");
                }
                return cat.includes(selectedCategory) || title.includes(selectedCategory);
              }).slice(0, 8)
          ).map((product, index) => (
            <ScrollReveal key={product.id} delay={index * 50} direction="up">
              <ProductCard product={product} isBestMatch={index === 0 && selectedCategory === "all"} />
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* Testimonials Section */}
      <section>
        <ScrollReveal>
          <div className="mb-8">
            <p className="text-xs font-bold uppercase tracking-[.15em] text-[#174c3c]">Real people, real stories</p>
            <h2 className="mt-2 text-2xl font-extrabold tracking-tight sm:text-3xl">What our customers say</h2>
          </div>
        </ScrollReveal>
        <ScrollReveal delay={150}>
          <TestimonialCarousel />
        </ScrollReveal>
      </section>

      {/* AI Picks / Deals Section */}
      <ScrollReveal>
        <section className="relative overflow-hidden grid gap-5 rounded-[30px] bg-gradient-to-br from-[#e5f0e9] via-[#edf5f0] to-[#f0f7f3] p-6 sm:p-8 lg:grid-cols-[.95fr_2fr] lg:p-10 border border-[#d4e8da]">
          <div className="pointer-events-none absolute -right-20 -top-20 h-60 w-60 rounded-full bg-[#174c3c] opacity-[0.05] blur-3xl" />
          <div>
            <p className="inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-[.14em] text-[#174c3c]">
              <Sparkles className="h-3.5 w-3.5 animate-bounce-subtle" /> AgentPay picks
            </p>
            <h2 className="mt-3 text-2xl font-extrabold tracking-tight">Need a second opinion?</h2>
            <p className="mt-3 max-w-xs text-sm leading-6 text-[#526058]">
              Ask in plain language. We will surface the trade-offs, not bury you in specs.
            </p>
            <button
              onClick={() => openAiDrawer({ pageType: "home" })}
              className="mt-6 rounded-full bg-[#174c3c] px-4 py-2.5 text-sm font-bold text-white transition-all duration-200 hover:bg-[#103c2f] hover:shadow-lg hover:scale-[1.03] active:scale-[0.97]"
            >
              Start a conversation
            </button>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {deals.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        </section>
      </ScrollReveal>

      {/* Trust Signals */}
      <ScrollReveal>
        <section className="grid gap-5 rounded-[24px] border border-[#e0e6df] bg-white/50 p-8 backdrop-blur-sm sm:grid-cols-3">
          <div className="group flex gap-3 transition-transform duration-200 hover:-translate-y-0.5">
            <ShieldCheck className="h-6 w-6 shrink-0 text-[#174c3c] transition-transform duration-200 group-hover:scale-110" />
            <div>
              <h3 className="text-sm font-bold">Secure from checkout to confirmation</h3>
              <p className="mt-1 text-xs leading-5 text-[#68736d]">Clear, verified Razorpay payments.</p>
            </div>
          </div>
          <div className="group flex gap-3 transition-transform duration-200 hover:-translate-y-0.5">
            <Truck className="h-6 w-6 shrink-0 text-[#174c3c] transition-transform duration-200 group-hover:scale-110" />
            <div>
              <h3 className="text-sm font-bold">Delivery you can plan for</h3>
              <p className="mt-1 text-xs leading-5 text-[#68736d]">Live stock and honest delivery windows.</p>
            </div>
          </div>
          <div className="group flex gap-3 transition-transform duration-200 hover:-translate-y-0.5">
            <Undo2 className="h-6 w-6 shrink-0 text-[#174c3c] transition-transform duration-200 group-hover:scale-110" />
            <div>
              <h3 className="text-sm font-bold">Easy returns</h3>
              <p className="mt-1 text-xs leading-5 text-[#68736d]">Change your mind with confidence.</p>
            </div>
          </div>
        </section>
      </ScrollReveal>
    </div>
  );
}
