import { NextResponse } from "next/server";
import { ALL_PRODUCTS } from "@/data/products";
import { searchCatalog } from "@/lib/serverDb";

export async function GET() {
  let items: any[] = [];
  try {
    const dbRes = searchCatalog({ limit: 120 });
    if (dbRes.offers && dbRes.offers.length > 0) {
      items = dbRes.offers.map((o) => ({
        "@type": "Product",
        product_id: o.product_id,
        sku: o.offer_id,
        name: o.title,
        brand: {
          "@type": "Brand",
          name: o.brand || "AgentPay Select",
        },
        category: o.category,
        image: o.image_url,
        offers: {
          "@type": "Offer",
          price: o.unit_price_minor / 100,
          price_minor: o.unit_price_minor,
          priceCurrency: o.currency || "INR",
          availability: (o.available_quantity || 10) > 0 ? "https://schema.org/InStock" : "https://schema.org/OutOfStock",
          stock_count: o.available_quantity || 10,
          delivery_lead_time_days: o.delivery_days || 2,
          return_period_days: o.return_period_days || 10,
        },
        agentic_metadata: {
          autonomous_checkout_allowed: o.unit_price_minor <= 7000000,
          bounding_ceiling_minor: 7000000,
          spec_summary: o.title,
          specs: o.specifications || {},
        },
      }));
    }
  } catch {}

  if (items.length === 0) {
    items = ALL_PRODUCTS.map((p) => ({
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
    }));
  }

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
    items,
  };

  return NextResponse.json(agentCatalog, {
    headers: {
      "Content-Type": "application/ld+json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=1800",
    },
  });
}
