import { NextResponse } from "next/server";

export async function GET() {
  const manifest = {
    protocol_version: "ACP/1.0 & NPCI-UAP/2026",
    specification: "https://agentcommerce.org/spec/v1.0",
    name: "AgentPay AI Growth & Agentic Commerce Gateway",
    track: "Track 01: AI Growth & Agentic Commerce",
    mission: "Grow merchant revenue and make merchants sellable to AI buyers with bounded, gated, explainable transactions.",
    supported_protocols: [
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
    safety_and_governance: {
      hard_ceiling_minor: 7000000,
      hard_ceiling_inr: 70000,
      human_escalation_available: true,
      explainability_rule: "Every money action explainable, bounded and gated with immutable cryptographic audit ledger.",
      nonce_price_freeze_ttl_seconds: 900,
    },
    service_endpoints: {
      agent_catalog: "/api/v1/agent-catalog",
      catalog_search: "/api/v1/catalog/search",
      checkout: "/api/v1/checkout",
      authorization: "/api/v1/authorization",
      cross_sell: "/api/v1/recommendations/cross-sell",
      campaigns: "/api/v1/campaigns",
      audit_events: "/api/v1/audit/events",
      audit_aggregates: "/api/v1/audit/aggregates",
      payment_verification: "/api/v1/payments/razorpay/verify-signature",
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
