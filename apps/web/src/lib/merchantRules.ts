/**
 * The merchant's policy numbers, held in server-side module state.
 *
 * Two route handlers have to read the same rules — `api/[...path]` for
 * `/api/v1/merchant/rules` and the catalog/capability surfaces, and the
 * `/.well-known/agent-capability.json` discovery route. They were separate
 * module scopes before, so the discovery document could not see what the
 * merchant console had just saved. One module, one copy.
 *
 * Server-only. Never import from a client component.
 */

export interface MerchantRules {
  auto_approve_limit_minor: number;
  max_transaction_ceiling_minor: number;
  allowed_categories: string[];
  require_two_factor_for_large_purchases: boolean;
  max_discount_bps: number;
  ap2_autonomous_enabled: boolean;
  uap_protocol_enabled: boolean;
  acp_manifest_active: boolean;
}

/**
 * Hard outer bounds on the merchant policy numbers. These are the values the
 * rest of the codebase already assumes (the ₹70,000 autonomous ceiling) and the
 * ones published to third-party agents, so a write cannot raise them.
 */
export const MAX_TRANSACTION_CEILING_MINOR = 7000000; // ₹70,000.00
export const MAX_DISCOUNT_BPS = 1500; // 15%

export const BLOCKED_CATEGORIES = ["weapons", "tobacco", "adult"];

const initialRules: MerchantRules = {
  // Was 10,000,000 — above its own 7,000,000 ceiling. That is not a generous
  // limit, it is an impossible one: the capability document clamps it to the
  // ceiling while the merchant console reported the raw value, so the two
  // surfaces the "do the served limits agree?" check compares were never equal.
  // Same effective meaning, now expressible.
  auto_approve_limit_minor: MAX_TRANSACTION_CEILING_MINOR,
  max_transaction_ceiling_minor: MAX_TRANSACTION_CEILING_MINOR,
  allowed_categories: ["laptops", "phones", "audio", "monitors", "keyboards", "accessories"],
  require_two_factor_for_large_purchases: true,
  max_discount_bps: MAX_DISCOUNT_BPS,
  ap2_autonomous_enabled: true,
  uap_protocol_enabled: true,
  acp_manifest_active: true,
};

let rules: MerchantRules = { ...initialRules };
let updatedAt = new Date().toISOString();

export function getMerchantRules(): MerchantRules {
  return rules;
}

export function getMerchantRulesUpdatedAt(): string {
  return updatedAt;
}

export function setMerchantRules(next: MerchantRules): void {
  rules = next;
  updatedAt = new Date().toISOString();
}

/**
 * Apply a caller-supplied patch to the rules.
 *
 * The console's Save is unauthenticated, and these numbers are republished to
 * every third-party agent through the capability document and the catalog feed,
 * where they decide `autonomous_checkout_allowed`. An unrestricted merge let any
 * visitor raise the ceiling to 1e12 and mark every offer autonomous. Only known
 * keys are read, and the money and discount limits are clamped both ways: a
 * ceiling cannot be raised past the hard outer bound, and the auto-approval
 * limit cannot exceed the ceiling, or the published pair contradicts itself.
 */
export function applyMerchantRulesPatch(body: Record<string, unknown>): MerchantRules {
  const next: MerchantRules = { ...rules };

  const clampMinor = (value: unknown, max: number): number | null => {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Math.min(Math.round(n), max);
  };

  // The console writes the `*_minor` / `*_basis_points` aliases; agent-facing
  // callers use the shorter names. Accept either, clamp both the same way.
  const autoApprove = clampMinor(
    body.auto_approve_limit_minor ?? body.auto_approval_limit_minor,
    MAX_TRANSACTION_CEILING_MINOR
  );
  if (autoApprove !== null) next.auto_approve_limit_minor = autoApprove;

  const ceiling = clampMinor(
    body.max_transaction_ceiling_minor ?? body.max_transaction_minor,
    MAX_TRANSACTION_CEILING_MINOR
  );
  if (ceiling !== null) next.max_transaction_ceiling_minor = ceiling;

  const discountBps = Number(body.max_discount_bps ?? body.max_discount_basis_points);
  if (Number.isFinite(discountBps) && discountBps > 0) {
    next.max_discount_bps = Math.min(Math.round(discountBps), MAX_DISCOUNT_BPS);
  }

  for (const flag of [
    "require_two_factor_for_large_purchases",
    "ap2_autonomous_enabled",
    "uap_protocol_enabled",
    "acp_manifest_active",
  ] as const) {
    if (typeof body[flag] === "boolean") next[flag] = body[flag];
  }

  if (Array.isArray(body.allowed_categories)) {
    next.allowed_categories = body.allowed_categories
      .filter((c: unknown): c is string => typeof c === "string" && c.length <= 64)
      .slice(0, 50);
  }

  if (next.auto_approve_limit_minor > next.max_transaction_ceiling_minor) {
    next.auto_approve_limit_minor = next.max_transaction_ceiling_minor;
  }

  setMerchantRules(next);
  return next;
}

/**
 * The rules projected onto the field names the merchant console reads and writes
 * (`console/capability.ts::MerchantRulesData`). Without the aliases the policy
 * screen never loaded the served numbers into its form and its Save posted keys
 * the endpoint never read.
 */
export function merchantRulesView() {
  return {
    ...getMerchantRules(),
    merchant_id: "merchant_demo",
    version: "1.0",
    max_transaction_minor: rules.max_transaction_ceiling_minor,
    auto_approval_limit_minor: rules.auto_approve_limit_minor,
    max_discount_basis_points: rules.max_discount_bps,
    blocked_categories: [...BLOCKED_CATEGORIES],
    allowed_payment_methods: ["upi", "card", "netbanking", "wallet", "emi"],
    allow_out_of_stock: false,
    updated_at: getMerchantRulesUpdatedAt(),
  };
}
