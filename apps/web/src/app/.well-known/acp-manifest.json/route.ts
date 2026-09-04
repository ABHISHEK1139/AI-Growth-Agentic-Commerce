import { NextResponse } from "next/server";

export async function GET() {
  const manifest = {
    protocol_version: "ACP/1.0",
    specification: "https://agentcommerce.org/spec/v1.0",
    protocols_supported: [
      {
        name: "Agent Commerce Protocol (ACP)",
        version: "1.0",
        role: "merchant_gateway",
        status: "active",
      },
      {
        name: "NPCI Unified Agent Protocol (UAP)",
        version: "2026.1",
        role: "transactable_merchant",
        status: "active",
      },
      {
        name: "Agent Payment Protocol (AP2)",
        version: "draft-03",
        role: "delegated_authorization_receiver",
        status: "active",
      },
      {
        name: "x402 Autonomous Payment Protocol",
        version: "1.0",
        role: "bounded_micropayment_receiver",
        status: "active",
      },
    ],
    merchant: {
      id: "mer_agentpay_flagship",
      legal_name: "AgentPay Flagship Storefront",
      kyc_status: "verified",
      country: "IND",
      default_currency: "INR",
      payment_rails: ["razorpay_test_mode", "upi_intent", "card_tokenization", "ap2_mandate"],
    },
    capabilities: {
      catalog_discovery: {
        endpoint: "/api/v1/agent-catalog",
        format: "application/ld+json",
        query_support: ["category", "price_ceiling", "technical_specs", "in_stock_only"],
        semantic_search: true,
      },
      upsell_cross_sell_negotiation: {
        endpoint: "/api/v1/recommendations/cross-sell",
        max_bundle_discount_bps: 1500, // 15%
        explainability_required: true,
      },
      bounded_checkout: {
        endpoint: "/api/v1/checkout",
        hard_ceiling_minor: 7000000, // ₹70,000 max single autonomous order
        requires_step_up_above_minor: 3000000, // ₹30,000 requires 2FA / step-up
        price_lock_duration_seconds: 900,
      },
      audit_ledger: {
        endpoint: "/api/v1/audit/events",
        tamper_evident: true,
        hash_algorithm: "sha256",
      },
    },
    service_level: {
      stock_guarantee: true,
      return_policy_days: 14,
      shipping_dispatch_hours: 24,
    },
  };

  return NextResponse.json(manifest, {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
