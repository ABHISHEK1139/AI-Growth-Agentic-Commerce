"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  activateCampaign,
  ANALYTICS_LITERALS_NOTE,
  approveCampaign,
  campaignAnalytics,
  CAMPAIGN_STORE_NOTE,
  CANDIDATE_SELECTION_NOTE,
  completeCampaign,
  listCampaigns,
  pauseCampaign,
  proposeCampaign,
  PROPOSE_BOUNDS,
  rejectCampaign,
  submitCampaignForReview,
  type Campaign,
  type CampaignAnalytics,
} from "@/console/campaigns";
import { isCredentialGap, localTimestamp } from "@/console/audit";
import { Amount, ErrorCard, LoadingCard, SourceNote } from "@/console/ui";
import type { ApiError } from "@/lib/api";

/**
 * The campaign orchestrator, wired to the campaign endpoints.
 *
 * `GET /api/v1/campaigns` and `/analytics` for state, `POST /propose`,
 * `/{id}/approve`, `/{id}/reject`, `/{id}/activate`, `/{id}/pause`, and `/{id}/complete` for the transitions. The
 * previous version of this screen ran the whole state machine in `useState`: a
 * `setTimeout(600)` "AI analysis" that keyword-matched the prompt in the browser,
 * client-side price arithmetic on rupee floats, and an "Approve & Launch" button
 * that moved an object between two arrays.
 *
 * The transitions are now the service's, which matters because the service
 * enforces things the client version could not: an approve is refused for a
 * campaign whose policy check returned `block`, and an activate is refused unless
 * the campaign is approved. Those refusals surface here as errors rather than as
 * successful-looking local state changes.
 *
 * Three limitations are printed on the page rather than left to be discovered:
 * the store is in-memory, candidate selection is a fixed set inside the service
 * rather than a catalog query, and most analytics figures are literals in the
 * router. All money is rendered from the integer minor fields.
 *
 * Every scope-gated call here requires `catalog:read`, which a browser can only
 * present with a session cookie the gateway does not issue yet, so the credential
 * wall is a first-class state on this screen.
 */

type Phase = "loading" | "loaded" | "failed";

const CONSTANT_MARK = "fixed value from the service, not a measurement";

const QUICK_GOALS = [
  {
    label: "Audio weekend (10% max)",
    prompt:
      "Increase sales of slow-moving headphones this weekend without discounting more than 10%",
  },
  {
    label: "Developer laptop bundle (8% max)",
    prompt: "Boost developer laptop sales with 8% discount and companion accessory cross-sells",
  },
  {
    label: "Phone and charger attach (5% max)",
    prompt: "Drive phone sales with 5% discount and highlight compatible 45W fast chargers",
  },
  {
    label: "Accessory surplus clearance",
    prompt: "Accelerate high-stock mouse surplus inventory with 10% promotional pricing",
  },
] as const;

const DECISION_STYLE: Record<string, string> = {
  allow: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  require_approval: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  block: "bg-rose-500/10 text-rose-300 border-rose-500/30",
};

const STATUS_STYLE: Record<string, string> = {
  proposed: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  review: "bg-amber-600/10 text-amber-200 border-amber-600/30",
  approved: "bg-blue-500/10 text-blue-300 border-blue-500/30",
  active: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  paused: "bg-yellow-500/10 text-yellow-300 border-yellow-500/30",
  rejected: "bg-rose-500/10 text-rose-300 border-rose-500/30",
  completed: "bg-slate-500/10 text-slate-300 border-slate-500/30",
  cancelled: "bg-slate-500/10 text-slate-300 border-slate-500/30",
  draft: "bg-slate-500/10 text-slate-300 border-slate-500/30",
};

export default function MerchantCampaignsPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [analytics, setAnalytics] = useState<CampaignAnalytics | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  const [goalPrompt, setGoalPrompt] = useState(
    "Increase sales of slow-moving headphones this weekend without discounting more than 10%"
  );
  const [maxDiscount, setMaxDiscount] = useState(10);
  const [durationDays, setDurationDays] = useState(3);
  /** Held in major units for the control, sent in minor units. */
  const [budgetMajor, setBudgetMajor] = useState(50000);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    setPhase((current) => (current === "loaded" ? current : "loading"));
    setError(null);

    const [list, metrics] = await Promise.all([listCampaigns(), campaignAnalytics()]);

    if (!list.ok) {
      setCampaigns([]);
      setAnalytics(null);
      setError(list.error);
      setPhase("failed");
      return;
    }

    setCampaigns(Array.isArray(list.data?.campaigns) ? list.data.campaigns : []);
    setAnalytics(metrics.ok ? metrics.data : null);
    setPhase("loaded");
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = useCallback(
    async (key: string, action: () => Promise<{ ok: boolean; error?: ApiError }>) => {
      setPending(key);
      setActionError(null);
      const result = (await action()) as { ok: boolean; error?: ApiError };
      if (!result.ok && result.error) setActionError(result.error);
      await load();
      setPending(null);
    },
    [load]
  );

  const proposals = campaigns.filter((campaign) => campaign.status === "proposed");
  const approved = campaigns.filter((campaign) => campaign.status === "approved");
  const live = campaigns.filter((campaign) => campaign.status === "active");
  const paused = campaigns.filter((campaign) => campaign.status === "paused");
  const closed = campaigns.filter(
    (campaign) =>
      campaign.status === "rejected" ||
      campaign.status === "completed" ||
      campaign.status === "cancelled"
  );

  const busy = pending !== null;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-6 md:p-10 font-sans">
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 border-b border-slate-800/80 pb-6">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="px-2.5 py-0.5 rounded text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                Merchant Growth Engine
              </span>
              <span className="text-xs text-slate-500">Campaign orchestrator</span>
            </div>
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-white mt-1">
              Campaign Orchestrator
            </h1>
            <p className="text-sm text-slate-400 mt-1">
              Proposals, the deterministic policy gate, and the approve/activate transitions — all
              performed by the gateway.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/merchant"
              className="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition"
            >
              &larr; Back to the console
            </Link>
          </div>
        </div>

        {/* Console navigation. Kept because the global navbar carries no merchant links. */}
        <div className="flex items-center gap-2 overflow-x-auto pb-2 border-b border-slate-800 text-sm">
          {(
            [
              ["/merchant", "Overview"],
              ["/merchant/catalog", "Catalog"],
              ["/merchant/inventory", "Inventory"],
              ["/merchant/policy", "Policy"],
              ["/merchant/transactions", "Transactions"],
              ["/merchant/agents", "Agents"],
              ["/merchant/api-usage", "Traffic"],
              ["/merchant/audit", "Audit"],
              ["/merchant/integrations", "Integrations"],
            ] as [string, string][]
          ).map(([href, label]) => (
            <Link
              key={href}
              href={href}
              className="px-3 py-1.5 rounded text-slate-400 hover:text-white transition whitespace-nowrap"
            >
              {label}
            </Link>
          ))}
          <span className="px-3 py-1.5 rounded bg-emerald-500/10 text-emerald-400 font-semibold border border-emerald-500/30 whitespace-nowrap">
            Campaigns
          </span>
        </div>

        {phase === "loading" ? (
          <LoadingCard message="Reading campaigns&hellip;" tone="dark" />
        ) : null}

        {phase === "failed" && error ? (
          <ErrorCard
            error={error}
            tone="dark"
            title={
              isCredentialGap(error)
                ? "The campaign endpoints require a catalog:read credential"
                : "Campaigns could not be read"
            }
            credentialGap={isCredentialGap(error)}
            credentialGapNote="Every campaign route declares require_scopes(Scope.CATALOG_READ). A browser satisfies that with the agentpay_session cookie, and no endpoint in this gateway issues one yet, so the campaign surface is unreachable from here rather than empty."
            onRetry={() => void load()}
          />
        ) : null}

        {phase === "loaded" ? (
          <>
            {/* Analytics. Two measurements, four literals. */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-xl">
                <div className="text-xs text-slate-400 font-medium">Active campaigns</div>
                <div className="text-2xl font-bold text-white mt-1">
                  {analytics ? analytics.active_campaigns : live.length}
                </div>
                <div className="text-xs text-emerald-400 mt-1">Counted from the store</div>
              </div>
              <div className="bg-slate-900/60 border border-slate-800 p-4 rounded-xl">
                <div className="text-xs text-slate-400 font-medium">Completed campaigns</div>
                <div className="text-2xl font-bold text-white mt-1">
                  {analytics ? analytics.completed_campaigns : 0}
                </div>
                <div className="text-xs text-slate-500 mt-1">Counted from the store</div>
              </div>
              <div className="bg-slate-900/40 border border-dashed border-slate-700 p-4 rounded-xl">
                <div className="text-xs text-slate-400 font-medium">Average sales lift</div>
                <div className="text-2xl font-bold text-slate-400 mt-1">
                  {analytics ? `${analytics.average_sales_lift_pct}%` : "\u2014"}
                </div>
                <div className="text-xs text-slate-500 mt-1">{CONSTANT_MARK}</div>
              </div>
              <div className="bg-slate-900/40 border border-dashed border-slate-700 p-4 rounded-xl">
                <div className="text-xs text-slate-400 font-medium">Incremental revenue</div>
                <div className="text-2xl font-bold text-slate-400 mt-1">
                  {analytics ? (
                    <Amount
                      minor={analytics.incremental_revenue_minor}
                      currency={analytics.currency}
                    />
                  ) : (
                    "\u2014"
                  )}
                </div>
                <div className="text-xs text-slate-500 mt-1">{CONSTANT_MARK}</div>
              </div>
            </div>

            <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-[11px] leading-relaxed space-y-1">
              <p>
                <strong>{ANALYTICS_LITERALS_NOTE}</strong>
              </p>
              <p>{CAMPAIGN_STORE_NOTE}</p>
              <p>{CANDIDATE_SELECTION_NOTE}</p>
            </div>

            {/* Proposal console */}
            <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-6">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-base font-semibold text-white">
                    1. Goal and constraints
                  </span>
                  <span className="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                    POST /api/v1/campaigns/propose
                  </span>
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  The gateway builds the proposal and runs the policy gate. Nothing about the
                  proposal is computed in this browser.
                </p>
              </div>

              <div className="space-y-3">
                <textarea
                  value={goalPrompt}
                  onChange={(event) => setGoalPrompt(event.target.value)}
                  rows={3}
                  disabled={busy}
                  className="w-full bg-slate-950 border border-slate-700/80 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 transition disabled:opacity-50"
                  placeholder="Describe the business outcome you want"
                />

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <span className="text-xs text-slate-500">Quick goals:</span>
                  {QUICK_GOALS.map((preset) => (
                    <button
                      key={preset.label}
                      type="button"
                      disabled={busy}
                      onClick={() => setGoalPrompt(preset.prompt)}
                      className="px-2.5 py-1 rounded-lg bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 text-xs text-slate-300 hover:text-white transition disabled:opacity-50"
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Controls bounded by the request model, not by taste. */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-2 border-t border-slate-800/80">
                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-400 font-medium">Max discount ceiling</span>
                    <span className="text-emerald-400 font-bold">{maxDiscount}%</span>
                  </div>
                  <input
                    type="range"
                    min={PROPOSE_BOUNDS.minDiscountPct}
                    max={PROPOSE_BOUNDS.maxDiscountPct}
                    step={1}
                    value={maxDiscount}
                    disabled={busy}
                    onChange={(event) => setMaxDiscount(Number(event.target.value))}
                    className="w-full accent-emerald-500 bg-slate-800 cursor-pointer disabled:opacity-50"
                  />
                  <p className="text-[11px] text-slate-500 mt-1">
                    Endpoint accepts {PROPOSE_BOUNDS.minDiscountPct}&ndash;
                    {PROPOSE_BOUNDS.maxDiscountPct}%
                  </p>
                </div>

                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-400 font-medium">Campaign duration</span>
                    <span className="text-blue-400 font-bold">{durationDays} days</span>
                  </div>
                  <input
                    type="range"
                    min={PROPOSE_BOUNDS.minDurationDays}
                    max={PROPOSE_BOUNDS.maxDurationDays}
                    step={1}
                    value={durationDays}
                    disabled={busy}
                    onChange={(event) => setDurationDays(Number(event.target.value))}
                    className="w-full accent-blue-500 bg-slate-800 cursor-pointer disabled:opacity-50"
                  />
                  <p className="text-[11px] text-slate-500 mt-1">
                    Endpoint accepts {PROPOSE_BOUNDS.minDurationDays}&ndash;
                    {PROPOSE_BOUNDS.maxDurationDays} days
                  </p>
                </div>

                <div>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-400 font-medium">Promotional budget</span>
                    <Amount
                      minor={budgetMajor * 100}
                      currency="INR"
                      className="text-purple-400 font-bold"
                    />
                  </div>
                  <input
                    type="range"
                    min={10000}
                    max={200000}
                    step={10000}
                    value={budgetMajor}
                    disabled={busy}
                    onChange={(event) => setBudgetMajor(Number(event.target.value))}
                    className="w-full accent-purple-500 bg-slate-800 cursor-pointer disabled:opacity-50"
                  />
                  <p className="text-[11px] text-slate-500 mt-1">
                    Sent as budget_minor; the endpoint&rsquo;s floor is{" "}
                    {PROPOSE_BOUNDS.minBudgetMinor} minor units
                  </p>
                </div>
              </div>

              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  disabled={busy || goalPrompt.trim().length === 0}
                  onClick={() =>
                    void runAction("propose", () =>
                      proposeCampaign({
                        goal_prompt: goalPrompt.trim(),
                        max_discount_pct: maxDiscount,
                        duration_days: durationDays,
                        budget_minor: budgetMajor * 100,
                      })
                    )
                  }
                  className="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium text-sm flex items-center gap-2 shadow-lg shadow-emerald-900/20 transition"
                >
                  {pending === "propose" ? (
                    <>
                      <span className="inline-block w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                      Asking the gateway&hellip;
                    </>
                  ) : (
                    <span>Request a campaign proposal</span>
                  )}
                </button>
              </div>
            </div>

            {actionError ? (
              <ErrorCard
                error={actionError}
                tone="dark"
                title="The gateway refused that action"
                credentialGap={isCredentialGap(actionError)}
                credentialGapNote="The campaign routes require catalog:read, which this browser cannot present."
              />
            ) : null}

            {/* Proposals awaiting a decision */}
            {proposals.length === 0 && approved.length === 0 && live.length === 0 ? (
              <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-10 text-center space-y-2">
                <h2 className="text-lg font-bold text-white">No campaigns yet</h2>
                <p className="text-xs text-slate-400 max-w-lg mx-auto">
                  Ask the gateway for a proposal above. The store is empty for this tenant, which is
                  also what you will see after a gateway restart.
                </p>
              </div>
            ) : null}

            {proposals.concat(approved).map((campaign) => (
              <div
                key={campaign.campaign_id}
                className="bg-slate-900 border-2 border-emerald-500/30 rounded-2xl p-6 space-y-6 shadow-xl"
              >
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-4">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wider border ${
                          STATUS_STYLE[campaign.status] ?? STATUS_STYLE.draft
                        }`}
                      >
                        {campaign.status}
                      </span>
                      <span className="text-xs text-slate-400 font-mono">
                        {campaign.campaign_id}
                      </span>
                    </div>
                    <h2 className="text-xl font-bold text-white mt-1">{campaign.title}</h2>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Objective: &quot;{campaign.goal}&quot;
                    </p>
                  </div>
                  <CampaignWorkflowStepper status={campaign.status} />

                  <div className="flex flex-wrap items-center gap-3 bg-slate-950 border border-slate-800 p-3 rounded-xl">
                    <div className="text-right">
                      <div className="text-[11px] text-slate-400">Estimated lift</div>
                      <div className="text-sm font-bold text-slate-400">
                        {campaign.estimated_sales_lift_pct}%
                      </div>
                      <div className="text-[10px] text-slate-500">{CONSTANT_MARK}</div>
                    </div>
                    <div className="h-6 w-px bg-slate-800" />
                    <div className="text-right">
                      <div className="text-[11px] text-slate-400">Projected revenue</div>
                      <Amount
                        minor={campaign.estimated_revenue_minor}
                        currency="INR"
                        className="text-sm font-bold text-white"
                      />
                      <div className="text-[10px] text-slate-500">service projection</div>
                    </div>
                    <div className="h-6 w-px bg-slate-800" />
                    <div className="text-right">
                      <div className="text-[11px] text-slate-400">Discount cost</div>
                      <Amount
                        minor={campaign.estimated_discount_cost_minor}
                        currency="INR"
                        className="text-sm font-bold text-white"
                      />
                      <div className="text-[10px] text-slate-500">service projection</div>
                    </div>
                  </div>
                </div>

                {/* The policy gate, as the service decided it */}
                <div className="bg-slate-950/80 border border-slate-800/80 rounded-xl p-4 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                      Deterministic policy gate
                    </span>
                    <span
                      className={`text-xs px-2 py-0.5 rounded font-medium border ${
                        DECISION_STYLE[campaign.policy_check.decision] ?? DECISION_STYLE.block
                      }`}
                    >
                      {campaign.policy_check.decision.toUpperCase()} &mdash;{" "}
                      {campaign.policy_check.passed_rules.length} passed,{" "}
                      {campaign.policy_check.violated_rules.length} violated
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">{campaign.policy_check.reason}</p>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 text-xs">
                    {campaign.policy_check.passed_rules.map((rule) => (
                      <div
                        key={rule}
                        className="flex items-start gap-2 p-2 rounded bg-slate-900/60 border border-slate-800 text-slate-300"
                      >
                        <span className="text-emerald-400 font-bold">+</span>
                        <span className="break-words">{rule}</span>
                      </div>
                    ))}
                    {campaign.policy_check.violated_rules.map((rule) => (
                      <div
                        key={rule}
                        className="flex items-start gap-2 p-2 rounded bg-rose-950/40 border border-rose-900 text-rose-200"
                      >
                        <span className="font-bold">!</span>
                        <span className="break-words">{rule}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Item detail */}
                <div className="space-y-3">
                  <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                    Products in the proposal
                  </h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs border border-slate-800 rounded-xl overflow-hidden">
                      <thead className="bg-slate-950 text-slate-400 font-medium">
                        <tr>
                          <th className="p-3">Product</th>
                          <th className="p-3">Inventory</th>
                          <th className="p-3">Original</th>
                          <th className="p-3">Discount</th>
                          <th className="p-3">Promotional</th>
                          <th className="p-3">Margin kept</th>
                          <th className="p-3">Pairings</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800 bg-slate-950/40">
                        {campaign.products.map((item) => (
                          <tr key={item.offer_id} className="hover:bg-slate-900/40 transition">
                            <td className="p-3">
                              <div className="font-semibold text-slate-200">{item.title}</div>
                              <div className="text-[11px] text-slate-500">
                                {item.selection_rationale}
                              </div>
                              <div className="text-[10px] text-slate-600 font-mono">
                                {item.product_id} &middot; {item.offer_id}
                              </div>
                            </td>
                            <td className="p-3">
                              <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-medium">
                                {item.available_inventory}
                              </span>
                            </td>
                            <td className="p-3 text-slate-400">
                              <Amount minor={item.original_price_minor} currency="INR" />
                            </td>
                            <td className="p-3">
                              <span className="text-emerald-400 font-bold">
                                {item.discount_pct}%
                              </span>
                            </td>
                            <td className="p-3 font-bold text-white">
                              <Amount minor={item.promotional_price_minor} currency="INR" />
                            </td>
                            <td className="p-3">
                              <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                                {item.margin_pct_preserved}%
                              </span>
                            </td>
                            <td className="p-3">
                              <div className="flex flex-wrap gap-1">
                                {item.cross_sell_pairings.map((pairing) => (
                                  <span
                                    key={pairing}
                                    className="px-1.5 py-0.5 rounded bg-slate-800 text-[10px] text-slate-300"
                                  >
                                    {pairing}
                                  </span>
                                ))}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Transitions, performed by the gateway */}
                <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-4 border-t border-slate-800">
                  <div className="text-xs text-slate-400">
                    Created {localTimestamp(campaign.created_at) ?? campaign.created_at}
                    {campaign.approved_at
                      ? `, approved ${localTimestamp(campaign.approved_at) ?? campaign.approved_at}`
                      : ""}
                    . The service refuses an approve on a blocked policy check and an activate on
                    anything not yet approved.
                  </div>
                  <div className="flex flex-wrap items-center gap-3 w-full sm:w-auto">
                    {(campaign.status === "draft" || campaign.status === "proposed") ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void runAction(`submit-review:${campaign.campaign_id}`, () =>
                            submitCampaignForReview(campaign.campaign_id)
                          )
                        }
                        className="flex-1 sm:flex-none px-4 py-2 rounded-xl border border-amber-700 bg-amber-950/40 hover:bg-amber-950/70 text-xs font-bold text-amber-300 transition disabled:opacity-50"
                      >
                        {pending === `submit-review:${campaign.campaign_id}`
                          ? "Submitting\u2026"
                          : "Submit for review"}
                      </button>
                    ) : null}
                    {campaign.status === "proposed" ? (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void runAction(`reject:${campaign.campaign_id}`, () =>
                              rejectCampaign(campaign.campaign_id, "Merchant declined proposal")
                            )
                          }
                          className="flex-1 sm:flex-none px-4 py-2 rounded-xl border border-slate-700 bg-slate-900 hover:bg-slate-800 text-xs font-medium text-rose-400 hover:text-rose-300 transition disabled:opacity-50"
                        >
                          {pending === `reject:${campaign.campaign_id}`
                            ? "Rejecting\u2026"
                            : "Decline proposal"}
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void runAction(`approve:${campaign.campaign_id}`, () =>
                              approveCampaign(campaign.campaign_id)
                            )
                          }
                          className="flex-1 sm:flex-none px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white shadow-lg shadow-emerald-900/30 transition disabled:opacity-50"
                        >
                          {pending === `approve:${campaign.campaign_id}`
                            ? "Approving\u2026"
                            : "Approve"}
                        </button>
                      </>
                    ) : null}
                    {campaign.status === "approved" ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void runAction(`activate:${campaign.campaign_id}`, () =>
                            activateCampaign(campaign.campaign_id)
                          )
                        }
                        className="flex-1 sm:flex-none px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-xs font-bold text-white shadow-lg shadow-emerald-900/30 transition disabled:opacity-50"
                      >
                        {pending === `activate:${campaign.campaign_id}`
                          ? "Activating\u2026"
                          : "Activate campaign"}
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            ))}

            {/* Live and closed */}
            <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-white">Active campaigns</h2>
                <span className="text-xs text-slate-500">{live.length} active</span>
              </div>

              {live.length === 0 && paused.length === 0 ? (
                <p className="text-xs text-slate-500">
                  No campaign is active. Approve a proposal and then activate it; both transitions
                  are the service&rsquo;s.
                </p>
              ) : (
                <div className="space-y-3">
                  {live.map((campaign) => (
                    <div
                      key={campaign.campaign_id}
                      className="bg-slate-950 border border-slate-800 rounded-xl p-4 flex flex-col md:flex-row md:items-center justify-between gap-4"
                    >
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 uppercase">
                            Active
                          </span>
                          <span className="font-bold text-white text-sm">{campaign.title}</span>
                        </div>
                        <p className="text-xs text-slate-400">{campaign.goal}</p>
                        <div className="text-[11px] text-slate-500 flex flex-wrap items-center gap-3 pt-1">
                          <span>Category: {campaign.target_category}</span>
                          <span>Max discount: {campaign.max_discount_pct}%</span>
                          <span>Duration: {campaign.duration_days} days</span>
                          <span>
                            Budget:{" "}
                            <Amount minor={campaign.budget_minor} currency="INR" />
                          </span>
                          {campaign.activated_at ? (
                            <span>
                              Activated{" "}
                              {localTimestamp(campaign.activated_at) ?? campaign.activated_at}
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void runAction(`pause:${campaign.campaign_id}`, () =>
                              pauseCampaign(campaign.campaign_id)
                            )
                          }
                          className="px-3 py-1.5 rounded-lg border border-yellow-600/50 bg-yellow-900/20 hover:bg-yellow-900/40 text-xs font-medium text-yellow-300 transition disabled:opacity-50"
                        >
                          {pending === `pause:${campaign.campaign_id}` ? "Pausing\u2026" : "Pause"}
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void runAction(`complete:${campaign.campaign_id}`, () =>
                              completeCampaign(campaign.campaign_id)
                            )
                          }
                          className="px-3 py-1.5 rounded-lg border border-slate-600/50 bg-slate-800/40 hover:bg-slate-800 text-xs font-medium text-slate-300 transition disabled:opacity-50"
                        >
                          {pending === `complete:${campaign.campaign_id}` ? "Completing\u2026" : "Complete"}
                        </button>
                      </div>
                    </div>
                  ))}
                  {paused.map((campaign) => (
                    <div
                      key={campaign.campaign_id}
                      className="bg-slate-950 border border-yellow-600/30 rounded-xl p-4 flex flex-col md:flex-row md:items-center justify-between gap-4"
                    >
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-yellow-500/10 text-yellow-300 border border-yellow-500/30 uppercase">
                            Paused
                          </span>
                          <span className="font-bold text-white text-sm">{campaign.title}</span>
                        </div>
                        <p className="text-xs text-slate-400">{campaign.goal}</p>
                        <div className="text-[11px] text-slate-500 flex flex-wrap items-center gap-3 pt-1">
                          <span>Category: {campaign.target_category}</span>
                          <span>Max discount: {campaign.max_discount_pct}%</span>
                          {campaign.paused_at ? (
                            <span>
                              Paused{" "}
                              {localTimestamp(campaign.paused_at) ?? campaign.paused_at}
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            void runAction(`complete:${campaign.campaign_id}`, () =>
                              completeCampaign(campaign.campaign_id)
                            )
                          }
                          className="px-3 py-1.5 rounded-lg border border-slate-600/50 bg-slate-800/40 hover:bg-slate-800 text-xs font-medium text-slate-300 transition disabled:opacity-50"
                        >
                          {pending === `complete:${campaign.campaign_id}` ? "Completing\u2026" : "Complete"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {closed.length > 0 ? (
                <div className="pt-3 border-t border-slate-800 space-y-2">
                  <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                    Closed
                  </h3>
                  {closed.map((campaign) => (
                    <div
                      key={campaign.campaign_id}
                      className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400"
                    >
                      <span className="font-mono">{campaign.campaign_id}</span>
                      <span>{campaign.title}</span>
                      <span className="uppercase font-bold">{campaign.status}</span>
                      <span>{campaign.rejection_reason ?? ""}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>

            <SourceNote tone="dark">
              Read from <span className="font-mono">GET /api/v1/campaigns</span> and{" "}
              <span className="font-mono">GET /api/v1/campaigns/analytics</span>; transitions via{" "}
              <span className="font-mono">POST /api/v1/campaigns/propose</span>,{" "}
              <span className="font-mono">/&#123;id&#125;/approve</span>,{" "}
              <span className="font-mono">/&#123;id&#125;/reject</span>,{" "}
              <span className="font-mono">/&#123;id&#125;/submit-for-review</span> and{" "}
              <span className="font-mono">/&#123;id&#125;/activate</span>. Every route is scoped to
              the caller&rsquo;s merchant by the service, which filters on
              <span className="font-mono"> principal.merchant_id</span>. The explicit{" "}
              <span className="font-mono">/submit-for-review</span> transition
              (DRAFT|PROPOSED &rarr; REVIEW) is the safety gate: the AI is
              allowed to <em>propose</em>; only a merchant operator may publish.
            </SourceNote>
          </>
        ) : null}
      </div>
    </div>
  );
}

/**
 * A compact horizontal stepper that shows the deterministic lifecycle every
 * campaign passes through. The current step is highlighted; steps the campaign
 * has cleared are dimmed; steps not yet reached are outlined.
 *
 * This is a UI sugar for the gateway's enforcement — the service is the
 * authority on whether a transition is legal. The stepper just makes the
 * state visible.
 */
function CampaignWorkflowStepper({ status }: { status: string }) {
  const STEPS: { key: string; label: string }[] = [
    { key: "draft", label: "Draft" },
    { key: "proposed", label: "Proposed" },
    { key: "review", label: "Review" },
    { key: "approved", label: "Approved" },
    { key: "active", label: "Active" },
    { key: "completed", label: "Completed" },
  ];

  const currentIndex = STEPS.findIndex((s) => s.key === status);
  const isTerminal = status === "rejected" || status === "cancelled";

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider">
      {STEPS.map((step, idx) => {
        const reached = !isTerminal && currentIndex >= 0 && idx <= currentIndex;
        const isCurrent = !isTerminal && idx === currentIndex;
        return (
          <div key={step.key} className="flex items-center gap-1.5">
            <span
              className={`px-2 py-0.5 rounded border ${
                isCurrent
                  ? "bg-amber-500 text-slate-900 border-amber-300"
                  : reached
                  ? "bg-emerald-700/20 text-emerald-300 border-emerald-700/30"
                  : "bg-slate-800 text-slate-500 border-slate-700"
              }`}
            >
              {step.label}
            </span>
            {idx < STEPS.length - 1 ? (
              <span className="text-slate-600">&rarr;</span>
            ) : null}
          </div>
        );
      })}
      {isTerminal ? (
        <span className="ml-2 px-2 py-0.5 rounded border bg-rose-700/20 text-rose-300 border-rose-700/30">
          {status}
        </span>
      ) : null}
    </div>
  );
}
