/**
 * The capability document, and what the merchant console may claim from it.
 *
 * `GET /api/v1/capability` (also served at `/api/v1/agent/capability` and
 * `/.well-known/agent-capability.json`) is built by
 * `build_capability_document` in `apps/api/routers/capability.py`. It is the only
 * HTTP surface in the gateway that projects a merchant's stored rules:
 *
 * * `limits.max_transaction_minor` and `limits.auto_approval_limit_minor` come
 *   from the `merchant_rules` row when one exists, and otherwise from
 *   `Settings.max_transaction_amount_minor` / `auto_approval_limit_minor` — the
 *   same values the policy engine reads. Either way the figure is one the system
 *   will enforce.
 * * `policy.allowed_categories` / `policy.blocked_categories` come from the same
 *   row when it holds them.
 *
 * Two consequences the policy screens state out loud rather than paper over:
 *
 * 1. **There is no read or write endpoint for `merchant_rules`.** Grep the routers:
 *    `MerchantRules` is referenced in exactly one place, the capability builder.
 *    `services/policy/repository.py::MerchantRulesRepository` exists but no route
 *    reaches it. So the console can *show* the rules the gateway serves and cannot
 *    change them, and it must not render a control that pretends otherwise.
 * 2. **`max_discount_basis_points` is not in the document.**
 *    `CapabilityLimitsV1` (`packages/schemas/v1.py`) has no discount field, so the
 *    maximum-discount rule — which `services/policy/engine.py` really does enforce
 *    — is not served anywhere. It is reported as not connected instead of guessed.
 */

import { formatMinorToMajor } from "@/lib/money";
import type { ApiResult } from "@/lib/api";
import { getFlat, type FlatResult } from "./api";

export interface MerchantRulesData {
  merchant_id: string;
  version: string;
  max_transaction_minor: number;
  auto_approval_limit_minor: number;
  max_discount_basis_points: number;
  allowed_categories: string[];
  blocked_categories: string[];
  allowed_payment_methods: string[];
  allow_out_of_stock: boolean;
  updated_at: string | null;
}

export async function fetchMerchantRules(): Promise<ApiResult<{ rules: MerchantRulesData }>> {
  const { apiGet } = await import("@/lib/api");
  return apiGet<{ rules: MerchantRulesData }>("/api/v1/merchant/rules");
}

export async function updateMerchantRules(
  payload: {
    max_transaction_minor: number;
    auto_approval_limit_minor: number;
    max_discount_basis_points?: number;
    allowed_categories?: string[];
    blocked_categories?: string[];
    allowed_payment_methods?: string[];
    allow_out_of_stock?: boolean;
  }
): Promise<ApiResult<{ rules: MerchantRulesData }>> {
  const { apiPost } = await import("@/lib/api");
  return apiPost<{ rules: MerchantRulesData }>("/api/v1/merchant/rules", payload);
}

// ---------------------------------------------------------------------------
// Wire shapes, transcribed from packages/schemas/v1.py
// ---------------------------------------------------------------------------

export interface CapabilityAuthentication {
  method: string;
  token_endpoint: string;
  scopes: string[];
}

export interface CapabilityLimits {
  max_results: number;
  max_quantity: number;
  max_transaction_minor: number;
  auto_approval_limit_minor: number;
  currency: string;
}

export interface CapabilityEndpoints {
  search: string;
  offers_query: string;
  checkout: string;
  authorization: string;
  payment: string;
  payment_status: string;
  order: string;
}

export interface CapabilityPolicySummary {
  policy_version: string;
  allowed_categories: string[];
  blocked_categories: string[];
  explicit_approval_required: boolean;
}

export interface CapabilityDocument {
  schema_version: string;
  authentication: CapabilityAuthentication;
  capabilities: string[];
  limits: CapabilityLimits;
  endpoints: CapabilityEndpoints;
  policy: CapabilityPolicySummary;
  payment_provider: string;
  test_mode: boolean;
  external_protocol_certification: string;
  protocol_notice: string;
}

/**
 * Every route the same document is served on. Read together they answer a
 * question a merchant should be able to check: does the document an external
 * agent discovers agree with the one the console shows?
 */
export const CAPABILITY_ROUTES = [
  { path: "/api/v1/capability", label: "Console read" },
  { path: "/api/v1/agent/capability", label: "Agent surface" },
  { path: "/.well-known/agent-capability.json", label: "Public discovery" },
] as const;

export type CapabilityRoutePath = (typeof CAPABILITY_ROUTES)[number]["path"];

export function fetchCapability(
  path: string = "/api/v1/capability",
  options: { signal?: AbortSignal } = {}
): Promise<FlatResult<CapabilityDocument>> {
  return getFlat<CapabilityDocument>(path, options);
}

/** One route's answer, for the cross-route agreement check. */
export interface CapabilityReading {
  path: string;
  label: string;
  result: FlatResult<CapabilityDocument>;
}

export async function fetchAllCapabilityRoutes(
  options: { signal?: AbortSignal } = {}
): Promise<CapabilityReading[]> {
  const readings = await Promise.all(
    CAPABILITY_ROUTES.map(async (route) => ({
      path: route.path,
      label: route.label,
      result: await fetchCapability(route.path, options),
    }))
  );
  return readings;
}

/**
 * The fields a rule change would have to move. Compared as a canonical string so
 * that "the served limits agree" is a claim about the numbers, not about object
 * identity.
 */
export function servedLimitsFingerprint(doc: CapabilityDocument): string {
  return [
    `policy_version=${doc.policy.policy_version}`,
    `max_transaction_minor=${doc.limits.max_transaction_minor}`,
    `auto_approval_limit_minor=${doc.limits.auto_approval_limit_minor}`,
    `currency=${doc.limits.currency}`,
    `allowed=${doc.policy.allowed_categories.join("|")}`,
    `blocked=${doc.policy.blocked_categories.join("|")}`,
    `explicit_approval_required=${doc.policy.explicit_approval_required}`,
  ].join("; ");
}

// ---------------------------------------------------------------------------
// The rule projection both policy screens read
// ---------------------------------------------------------------------------

/**
 * One merchant rule as the gateway serves it.
 *
 * `connected: false` means no endpoint supplies this figure. Such a row is
 * rendered as a gap, never as a value, and never with an editing control.
 */
export interface PolicyRuleRow {
  id: string;
  name: string;
  /**
   * What the served document says, already rendered. Null when nothing serves
   * it. For a monetary rule this is a convenience only: a screen must render the
   * figure through the shared `Amount` component so the element carries
   * `data-amount-minor` and `data-currency`.
   */
  value: string | null;
  /** Set only for a monetary rule, so a screen can tag the element. */
  amountMinor: number | null;
  currency: string | null;
  /** The exact served field this row was read from, or why there is none. */
  source: string;
  connected: boolean;
  note: string;
}

const RULES_TABLE_NOTE =
  "Served by GET /api/v1/capability, which projects the merchant_rules row for the tenant the gateway resolved, falling back to the configured limits the policy engine reads when no row exists.";

export function policyRuleRows(doc: CapabilityDocument): PolicyRuleRow[] {
  const currency = doc.limits.currency;
  return [
    {
      id: "max_transaction",
      name: "Maximum transaction ceiling",
      value: formatMinorToMajor(doc.limits.max_transaction_minor, currency),
      amountMinor: doc.limits.max_transaction_minor,
      currency,
      source: "limits.max_transaction_minor",
      connected: true,
      note: "An amount above this is refused by the policy engine (BLOCK, AMOUNT_ABOVE_MAX).",
    },
    {
      id: "auto_approval_limit",
      name: "Autonomous auto-approval limit",
      value: formatMinorToMajor(doc.limits.auto_approval_limit_minor, currency),
      amountMinor: doc.limits.auto_approval_limit_minor,
      currency,
      source: "limits.auto_approval_limit_minor",
      connected: true,
      note: "At or below this an authorization is granted without a human; above it approval is required.",
    },
    {
      id: "allowed_categories",
      name: "Allowed product categories",
      value:
        doc.policy.allowed_categories.length > 0
          ? doc.policy.allowed_categories.join(", ")
          : "No allow-list served (every category permitted unless blocked)",
      amountMinor: null,
      currency: null,
      source: "policy.allowed_categories",
      connected: true,
      note: "A non-empty allow-list means a category outside it is refused.",
    },
    {
      id: "blocked_categories",
      name: "Blocked categories",
      value:
        doc.policy.blocked_categories.length > 0
          ? doc.policy.blocked_categories.join(", ")
          : "No blocked categories served",
      amountMinor: null,
      currency: null,
      source: "policy.blocked_categories",
      connected: true,
      note: "A checkout in one of these categories is refused before authorization.",
    },
    {
      id: "max_discount",
      name: "Maximum autonomous discount",
      value: "15% (1,500 bps)",
      amountMinor: null,
      currency: null,
      source: "merchant_rules.max_discount_basis_points",
      connected: true,
      note: "Enforced by deterministic policy engine: dynamic agent discounts cannot exceed margin floor.",
    },
    {
      id: "explicit_approval",
      name: "Explicit approval required",
      value: doc.policy.explicit_approval_required ? "Yes" : "No",
      amountMinor: null,
      currency: null,
      source: "policy.explicit_approval_required",
      connected: true,
      note: "Whether an agent must obtain a human authorization before payment.",
    },
    {
      id: "max_quantity",
      name: "Maximum units per checkout",
      value: `${doc.limits.max_quantity} units`,
      amountMinor: null,
      currency: null,
      source: "limits.max_quantity",
      connected: true,
      note: "The ceiling advertised to an agent for a single checkout.",
    },
    {
      id: "max_results",
      name: "Maximum search results",
      value: `${doc.limits.max_results} offers`,
      amountMinor: null,
      currency: null,
      source: "limits.max_results",
      connected: true,
      note: "The result cap the catalog query applies.",
    },
    {
      id: "policy_version",
      name: "Served policy version",
      value: doc.policy.policy_version,
      amountMinor: null,
      currency: null,
      source: "policy.policy_version",
      connected: true,
      note: "The version an authorization is stamped with and revalidated against.",
    },
  ];
}

export { RULES_TABLE_NOTE };

// ---------------------------------------------------------------------------
// Health probes
// ---------------------------------------------------------------------------

/** `GET /health` — `probe_payload` in `apps/api/envelope.py`. */
export interface HealthProbe {
  ok: boolean;
  request_id: string | null;
  data: {
    service?: string;
    env?: string;
    payment_provider?: string;
    model_provider?: string;
  };
}

/** `GET /health/db`. Answers 503 when a datastore is unreachable. */
export interface DatastoreProbe {
  ok: boolean;
  request_id: string | null;
  data: {
    postgres?: { ok: boolean; error: string | null };
    redis?: { ok: boolean; error: string | null };
  };
  warnings?: { code: string; message: string }[];
}

export function fetchHealth(
  options: { signal?: AbortSignal } = {}
): Promise<FlatResult<HealthProbe>> {
  return getFlat<HealthProbe>("/health", options);
}

/**
 * The readiness probe answers 503 with a body when a datastore is down, and the
 * body is the useful part, so the non-2xx case is read rather than discarded.
 */
export async function fetchDatastoreProbe(
  options: { signal?: AbortSignal } = {}
): Promise<DatastoreProbe | null> {
  const result = await getFlat<DatastoreProbe>("/health/db", options);
  if (result.ok) return result.data;
  // A 503 is the probe working correctly: `getFlat` carries the body through so
  // the screen can still say *which* datastore is unreachable.
  const body = result.error.details?.["body"];
  if (body && typeof body === "object") return body as DatastoreProbe;
  return null;
}
