import { NextResponse } from "next/server";
import { ALL_PRODUCTS } from "@/data/products";

export async function GET() {
  const agentCatalog = {
    "@context": "https://schema.org/",
    "@type": "DataFeed",
    title: "Agentic Commerce Real-Time Product Catalog",
    protocol: "ACP/1.0 & NPCI-UAP/2026",
    updated_at: new Date().toISOString(),
    merchant: {
      "@type": "MerchantReturnPolicy",
      merchant_id: "mer_agentpay_flagship",
      name: "AgentPay Official Flagship Store",
      currency: "INR",
      payment_methods: ["Razorpay Test Mode", "UPI", "AP2"],
    },
    items: ALL_PRODUCTS.map((p) => ({
      "@type": "Product",
      product_id: p.id,
      sku: p.slug,
      name: p.title,
      brand: {
        "@type": "Brand",
        name: p.brand,
      },
      category: p.category,
      category_label: p.categoryLabel,
      image: p.imageUrl,
      offers: {
        "@type": "Offer",
        price: p.priceMinor / 100,
        price_minor: p.priceMinor,
        original_price_minor: p.originalPriceMinor,
        priceCurrency: p.currency || "INR",
        availability: p.stock > 0 ? "https://schema.org/InStock" : "https://schema.org/OutOfStock",
        stock_count: p.stock,
        delivery_lead_time_days: p.deliveryDays || 2,
        return_period_days: p.returnDays || 14,
      },
      agentic_metadata: {
        autonomous_checkout_allowed: p.priceMinor <= 7000000,
        bounding_ceiling_minor: 7000000,
        spec_summary: p.shortSpecs,
        why_fits_summary: p.whyFitsYou?.summary || "",
        specs: p.specsGrouped?.performance || {},
        compatible_accessories: p.crossSell ? [p.crossSell.id] : [],
      },
    })),
  };

  return NextResponse.json(agentCatalog, {
    headers: {
      "Content-Type": "application/ld+json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=1800",
    },
  });
}
