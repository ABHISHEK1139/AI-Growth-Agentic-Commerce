/**
 * The merchant's machine-readable capability document, as served by the web
 * tier's own API shim.
 *
 * `CapabilityDocumentV1` (`packages/schemas/v1.py`) is the contract an external
 * AI agent reads before it buys anything, and it is reachable on three paths:
 *
 *   GET /api/v1/capability
 *   GET /api/v1/agent/capability
 *   GET /.well-known/agent-capability.json
 *
 * The last one is the discovery path, and it is *not* under `/api/*`, so the
 * `api/[...path]` fallback never saw it: the merchant console and the scenarios
 * page fetched it and got a 404, which silently reduced their "do the three
 * surfaces agree?" check to comparing two documents and one empty response.
 * One builder, three routes — the three have to be the same document, or the
 * agreement check is theatre.
 *
 * Server-only: it reads nothing secret, but it is the same code path as the
 * merchant policy surface, so keep it out of client components.
 */

export interface CapabilityDocumentShim {
  schema_version: string;
  authentication: { method: string; token_endpoint: string; scopes: string[] };
  capabilities: string[];
  limits: {
    max_results: number;
    max_quantity: number;
    max_transaction_minor: number;
    auto_approval_limit_minor: number;
    currency: string;
  };
  endpoints: Record<string, string>;
  policy: {
    policy_version: string;
    allowed_categories: string[];
    blocked_categories: string[];
    explicit_approval_required: boolean;
  };
  payment_provider: string;
  test_mode: boolean;
  external_protocol_certification: string;
  protocol_notice: string;
}

/**
 * Hard outer bounds on the merchant policy numbers. These are the values the
 * rest of the codebase already assumes (the ₹70,000 autonomous ceiling) and
 * the ones published to third-party agents, so a write cannot raise them.
 */
export const MAX_TRANSACTION_CEILING_MINOR = 7000000; // ₹70,000.00
export const MAX_DISCOUNT_BPS = 1500; // 15%

const ALLOWED_CATEGORIES = [
  "laptop",
  "smartphone",
  "audio",
  "camera",
  "monitor",
  "computer_accessory",
  "phone_accessory",
  "home_electronics",
  "appliance",
];

const BLOCKED_CATEGORIES = ["weapons", "tobacco", "adult"];

/**
 * Build the document from the live merchant rules.
 *
 * The limits are derived, never copied: a published ceiling wider than the one
 * the merchant actually set is how an agent is told it may autonomously charge
 * an amount the policy engine would then refuse.
 */
export function buildCapabilityDocument(
  rules: {
    max_transaction_ceiling_minor: number;
    auto_approve_limit_minor: number;
    allowed_categories?: string[];
  }
): CapabilityDocumentShim {
  const ceiling = Math.min(rules.max_transaction_ceiling_minor, MAX_TRANSACTION_CEILING_MINOR);
  const autoApprove = Math.min(
    rules.auto_approve_limit_minor,
    rules.max_transaction_ceiling_minor,
    MAX_TRANSACTION_CEILING_MINOR
  );

  return {
    schema_version: "1.0",
    authentication: {
      method: "api_key_exchange",
      token_endpoint: "/v1/auth/token",
      scopes: ["catalog:read", "checkout:write", "payment:write"],
    },
    capabilities: [
      "catalog_search",
      "offer_query",
      "checkout",
      "authorization",
      "payment",
      "payment_status",
      "order_lookup",
    ],
    limits: {
      max_results: 50,
      max_quantity: 10,
      max_transaction_minor: ceiling,
      auto_approval_limit_minor: autoApprove,
      currency: "INR",
    },
    endpoints: {
      search: "/v1/catalog/search",
      offers_query: "/v1/catalog/offers",
      checkout: "/v1/checkout",
      authorization: "/v1/authorizations",
      payment: "/v1/payments",
      payment_status: "/v1/payments/{payment_id}",
      order: "/v1/orders/{order_id}",
    },
    policy: {
      policy_version: "1.0",
      allowed_categories: rules.allowed_categories?.length
        ? rules.allowed_categories
        : ALLOWED_CATEGORIES,
      blocked_categories: BLOCKED_CATEGORIES,
      explicit_approval_required: true,
    },
    payment_provider: "razorpay",
    test_mode: true,
    external_protocol_certification: "none",
    protocol_notice: "APCP/1.0 - Razorpay Agentic Autonomous Commerce Protocol",
  };
}
