/**
 * The campaign orchestrator client.
 *
 * Wire shapes are transcribed from `_campaign_to_dict` in
 * `apps/api/routers/campaigns.py`; the state machine is
 * `services/campaigns/service.py`.
 *
 * Three properties of this endpoint family a screen has to be honest about,
 * because they are not obvious from the response:
 *
 * 1. **Campaign state is process-local and in memory.**
 *    `CampaignService._CAMPAIGNS_STORE` is a class-level dict. Records are real —
 *    a proposal you approve is the record the service holds and the transitions
 *    are enforced server-side — but they do not survive a gateway restart and are
 *    not in the database.
 * 2. **Candidate selection is not a catalog query.** `propose_campaign` branches
 *    on keywords in the goal prompt and returns one of four fixed product items
 *    with fixed inventory and margin figures. Those numbers come from the service,
 *    not from the catalog, so a screen must not present them as live catalog
 *    facts.
 * 3. **Most analytics figures are constants.** `get_analytics` computes
 *    `active_campaigns` and `completed_campaigns` from the store and returns
 *    `average_sales_lift_pct`, `incremental_revenue_minor`, `discount_spent_minor`
 *    and `net_roi_multiplier` as literals. Only the two counts are measurements.
 */

import { apiGet, apiPost, type ApiResult } from "@/lib/api";

export interface CampaignProduct {
  product_id: string;
  offer_id: string;
  title: string;
  category: string;
  original_price_minor: number;
  discount_pct: number;
  promotional_price_minor: number;
  available_inventory: number;
  margin_pct_preserved: number;
  cross_sell_pairings: string[];
  selection_rationale: string;
}

export interface CampaignPolicyCheck {
  decision: "allow" | "require_approval" | "block";
  passed_rules: string[];
  violated_rules: string[];
  reason: string;
}

export interface Campaign {
  campaign_id: string;
  merchant_id: string;
  title: string;
  goal: string;
  target_category: string;
  status: "draft" | "proposed" | "approved" | "rejected" | "active" | "paused" | "completed" | "cancelled";
  max_discount_pct: number;
  duration_days: number;
  budget_minor: number;
  products: CampaignProduct[];
  policy_check: CampaignPolicyCheck;
  estimated_sales_lift_pct: number;
  estimated_revenue_minor: number;
  estimated_discount_cost_minor: number;
  created_at: string;
  approved_at: string | null;
  activated_at: string | null;
  paused_at: string | null;
  completed_at: string | null;
  rejection_reason: string | null;
}

export interface CampaignAnalytics {
  merchant_id: string;
  /** Counted from the store. */
  active_campaigns: number;
  completed_campaigns: number;
  /** Literals in the router, not measurements. Rendered as such. */
  average_sales_lift_pct: number;
  incremental_revenue_minor: number;
  discount_spent_minor: number;
  net_roi_multiplier: number;
  currency: string;
}

/** Bounds from `ProposeCampaignRequest`. Controls must not offer more. */
export const PROPOSE_BOUNDS = {
  minDiscountPct: 1,
  maxDiscountPct: 50,
  minDurationDays: 1,
  maxDurationDays: 14,
  /** `budget_minor: int = Field(default=5000000, ge=100000)`. */
  minBudgetMinor: 100000,
} as const;

export function listCampaigns(
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<{ campaigns: Campaign[] }>> {
  return apiGet<{ campaigns: Campaign[] }>("/api/v1/campaigns", options);
}

export function campaignAnalytics(
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<CampaignAnalytics>> {
  return apiGet<CampaignAnalytics>("/api/v1/campaigns/analytics", options);
}

export function proposeCampaign(
  request: {
    goal_prompt: string;
    max_discount_pct: number;
    duration_days: number;
    budget_minor: number;
    category?: string | null;
  },
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  const body: Record<string, unknown> = {
    goal_prompt: request.goal_prompt,
    max_discount_pct: request.max_discount_pct,
    duration_days: request.duration_days,
    budget_minor: Math.max(request.budget_minor, PROPOSE_BOUNDS.minBudgetMinor),
  };
  if (request.category) body.category = request.category;
  return apiPost<Campaign>("/api/v1/campaigns/propose", body, options);
}

export function approveCampaign(
  campaignId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  return apiPost<Campaign>(
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}/approve`,
    undefined,
    options
  );
}

export function rejectCampaign(
  campaignId: string,
  reason: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  // The router declares a body model, so one has to be sent even though every
  // field of it has a default.
  return apiPost<Campaign>(
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}/reject`,
    { reason },
    options
  );
}

export function activateCampaign(
  campaignId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  return apiPost<Campaign>(
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}/activate`,
    undefined,
    options
  );
}

export function pauseCampaign(
  campaignId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  return apiPost<Campaign>(
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}/pause`,
    undefined,
    options
  );
}

export function completeCampaign(
  campaignId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  return apiPost<Campaign>(
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}/complete`,
    undefined,
    options
  );
}

/**
 * Submit a draft or proposed campaign into the merchant review queue.
 * Transitions DRAFT|PROPOSED → REVIEW. The merchant operator must then
 * explicitly approve, reject, or send it back to DRAFT — the AI may propose,
 * only a human can publish.
 */
export function submitCampaignForReview(
  campaignId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<Campaign>> {
  return apiPost<Campaign>(
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}/submit-for-review`,
    undefined,
    options
  );
}

export const CAMPAIGN_STORE_NOTE =
  "Campaign records live in CampaignService._CAMPAIGNS_STORE, a process-local dictionary. Transitions are enforced by the service, but nothing is persisted: a gateway restart empties this list.";

export const CANDIDATE_SELECTION_NOTE =
  "propose_campaign picks one of four fixed product items by keyword-matching the goal prompt, with inventory and margin figures written into services/campaigns/service.py. Those are the service's numbers, not a live catalog read, so they are shown as the proposal's own contents rather than as catalog state.";

export const ANALYTICS_LITERALS_NOTE =
  "Only active_campaigns and completed_campaigns are computed. Sales lift, incremental revenue, discount spend and ROI are literals returned by the router, so they are labelled rather than displayed as measurements.";
