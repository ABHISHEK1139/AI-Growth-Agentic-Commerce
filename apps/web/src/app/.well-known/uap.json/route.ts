import { NextResponse } from "next/server";

export async function GET() {
  const uapManifest = {
    uap_version: "2026.1",
    entity_type: "MERCHANT_ENDPOINT",
    npci_compliance: {
      framework: "Unified Agent Protocol (UAP)",
      mandate_support: true,
      instant_settlement_enabled: true,
      delegated_tokens_allowed: true,
      settlement_gateway: "Razorpay Test Network",
    },
    service_endpoints: {
      discovery: "/api/v1/agent-catalog",
      quotation: "/api/v1/catalog/search",
      checkout: "/api/v1/checkout",
      authorization: "/api/v1/authorization",
      verification: "/api/v1/payments/razorpay/verify-signature",
      audit_ledger: "/api/v1/audit/events",
    },
    transaction_rules: {
      max_delegated_amount_minor: 7000000,
      currency: "INR",
      signature_scheme: "HMAC-SHA256",
      supported_methods: ["UPI_INTENT", "RAZORPAY_TEST_CARD", "NET_BANKING"],
    },
  };

  return NextResponse.json(uapManifest, {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
