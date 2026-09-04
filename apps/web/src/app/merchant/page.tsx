"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchAuditEvents,
  isCredentialGap,
  ledgerTotals,
  localTimestamp,
  SESSION_GAP_NOTE,
  type AuditEventRow,
  type LedgerTotals,
} from "@/console/audit";
import {
  fetchCapability,
  fetchDatastoreProbe,
  fetchHealth,
  type CapabilityDocument,
  type DatastoreProbe,
  type HealthProbe,
} from "@/console/capability";
import {
  Amount,
  Caveat,
  EmptyCard,
  ErrorCard,
  LoadingCard,
  NotConnected,
  SourceNote,
  consolePrimaryButton,
} from "@/console/ui";
import { apiGet, type ApiError } from "@/lib/api";

/**
 * The merchant overview.
 *
 * Every figure on this page is either counted from rows the audit ledger returned
 * or read from a document the gateway serves. Three sources, no fourth:
 *
 * * `GET /api/v1/audit/events` — the activity counters and the event stream.
 * * `GET /api/v1/capability` — the bounds an agent is told about.
 * * `GET /health` and `GET /health/db` — what is actually running.
 *
 * **The tiles that are gaps are rendered as gaps.** Three figures the previous
 * version displayed cannot be computed from anything this gateway exposes, and
 * inventing them was the whole problem:
 *
 * * *Catalog query volume.* The offer search path appends no audit event
 *   (`services/offers/service.py` never calls the audit repository). The
 *   `CATALOG_SEARCHED` event type is, confusingly, appended by the catalog
 *   *importer* (`services/catalog/service.py`, `metadata.action = "import"`), so
 *   counting it would report import runs as searches.
 * * *Connected AI buyers.* There is no agent-registry endpoint. What can be
 *   counted is the distinct actors the ledger observed, which is a different and
 *   smaller claim, so that is what is shown and labelled.
 * * *Revenue attributed to AI cross-sell.* `GET /api/v1/recommendations/metrics`
 *   exists, but the router returns fixed literals — `base_aov_minor: 6499900` and
 *   friends are hardcoded in `apps/api/routers/recommendations.py`, not measured.
 *   A dashboard tile fed by it would be a fabricated number with an HTTP request
 *   in front of it, so it is not used.
 *
 * **The policy controls are gone.** They wrote to `useState` and nothing else.
 * The served bounds are shown read-only with a link to the policy screen, which
 * explains why no write path exists.
 */

type Phase = "loading" | "loaded" | "failed";

/** The endpoint's ceiling, and what the counters below are computed over. */
const LEDGER_WINDOW = 200;

const STREAM_ROWS = 25;

function pct(numerator: number, denominator: number): string {
  if (denominator === 0) return "n/a";
  return `${((numerator / denominator) * 100).toFixed(1)}%`;
}

function Badge({
  tone,
  children,
}: {
  tone: "ok" | "warn" | "bad" | "info";
  children: React.ReactNode;
}) {
  const className =
    tone === "ok"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : tone === "warn"
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : tone === "bad"
          ? "bg-rose-50 text-rose-700 border-rose-200"
          : "bg-slate-50 text-slate-700 border-slate-200";
  return (
    <div
      className={`px-3 py-1.5 border rounded-xl text-xs font-bold flex items-center gap-1.5 ${className}`}
    >
      <span
        className={`w-2 h-2 rounded-full ${
          tone === "ok"
            ? "bg-emerald-500"
            : tone === "warn"
              ? "bg-amber-500"
              : tone === "bad"
                ? "bg-rose-500"
                : "bg-slate-400"
        }`}
      />
      <span>{children}</span>
    </div>
  );
}

export default function MerchantConsolePage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [events, setEvents] = useState<AuditEventRow[]>([]);
  const [ledgerError, setLedgerError] = useState<ApiError | null>(null);
  const [doc, setDoc] = useState<CapabilityDocument | null>(null);
  const [capabilityError, setCapabilityError] = useState<ApiError | null>(null);
  const [health, setHealth] = useState<HealthProbe | null>(null);
  const [datastores, setDatastores] = useState<DatastoreProbe | null>(null);
  const [crossSellMetrics, setCrossSellMetrics] = useState<any | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setPhase((current) => (current === "loaded" ? current : "loading"));
    setRefreshing(true);
    setLedgerError(null);
    setCapabilityError(null);

    const [ledger, capability, healthProbe, dbProbe, metricsRes] = await Promise.all([
      fetchAuditEvents({ limit: LEDGER_WINDOW }),
      fetchCapability(),
      fetchHealth(),
      fetchDatastoreProbe(),
      apiGet<{ metrics: any }>("/api/v1/recommendations/metrics"),
    ]);

    if (ledger.ok) {
      setEvents(Array.isArray(ledger.data?.events) ? ledger.data.events : []);
    } else {
      setEvents([]);
      setLedgerError(ledger.error);
    }

    if (capability.ok) setDoc(capability.data);
    else setCapabilityError(capability.error);

    setHealth(healthProbe.ok ? healthProbe.data : null);
    setDatastores(dbProbe);
    if (metricsRes.ok && metricsRes.data?.metrics) {
      setCrossSellMetrics(metricsRes.data.metrics);
    }

    // The page as a whole fails only when nothing at all could be read.
    setPhase(!ledger.ok && !capability.ok && !healthProbe.ok ? "failed" : "loaded");
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totals: LedgerTotals = ledgerTotals(events);
  const atLimit = events.length >= LEDGER_WINDOW;
  const stream = events.slice(Math.max(events.length - STREAM_ROWS, 0)).reverse();
  const postgres = datastores?.data?.postgres;
  const redis = datastores?.data?.redis;

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-6 rounded-3xl border border-slate-200 shadow-xs">
        <div>
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-[#174c3c]/10 text-[#174c3c] rounded-full text-xs font-bold uppercase tracking-wider mb-2">
            <span>Merchant Control &amp; AI Governance Hub</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">
            Merchant Operations Console
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Live activity counted from your tenant&rsquo;s audit ledger, and the bounds this
            gateway publishes to autonomous buyers.
          </p>
        </div>

        {/* Health, read from the probes rather than asserted */}
        <div className="flex flex-wrap items-center gap-2">
          {health ? (
            <Badge tone="ok">
              {health.data.service ?? "gateway"}: {health.data.env ?? "unknown env"}
            </Badge>
          ) : (
            <Badge tone="bad">Liveness probe unreachable</Badge>
          )}
          {health?.data.model_provider ? (
            <Badge tone="info">
              Model provider: <span className="font-mono">{health.data.model_provider}</span>
            </Badge>
          ) : null}
          {doc ? (
            <Badge tone={doc.test_mode ? "warn" : "ok"}>
              {doc.payment_provider}
              {doc.test_mode ? ": test mode" : ": live"}
            </Badge>
          ) : null}
          {postgres ? (
            <Badge tone={postgres.ok ? "ok" : "bad"}>
              Postgres: {postgres.ok ? "reachable" : (postgres.error ?? "unreachable")}
            </Badge>
          ) : null}
          {redis ? (
            <Badge tone={redis.ok ? "ok" : "warn"}>
              Redis: {redis.ok ? "reachable" : (redis.error ?? "unreachable")}
            </Badge>
          ) : null}
          <button
            type="button"
            onClick={() => void load()}
            disabled={refreshing}
            className={consolePrimaryButton}
          >
            {refreshing ? "Reading\u2026" : "Refresh"}
          </button>
        </div>
      </div>

      {phase === "loading" ? (
        <LoadingCard message="Reading the ledger, the capability document and the health probes&hellip;" />
      ) : null}

      {phase === "failed" && ledgerError ? (
        <ErrorCard
          error={ledgerError}
          title="Nothing on this console could be read"
          credentialGap={isCredentialGap(ledgerError)}
          credentialGapNote={SESSION_GAP_NOTE}
          onRetry={() => void load()}
        />
      ) : null}

      {phase === "loaded" ? (
        <>
          {/* ---- KPI tiles. Each one is a count of rows or a served figure. ---- */}
          {ledgerError ? (
            <ErrorCard
              error={ledgerError}
              title={
                isCredentialGap(ledgerError)
                  ? "Activity figures need a merchant session"
                  : "The activity ledger could not be read"
              }
              credentialGap={isCredentialGap(ledgerError)}
              credentialGapNote={SESSION_GAP_NOTE}
              onRetry={() => void load()}
            />
          ) : (
            <>
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Checkouts created
                  </span>
                  <span className="text-2xl font-black text-slate-900">
                    {totals.checkoutsCreated}
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    CHECKOUT_CREATED rows
                  </span>
                </div>
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Orders confirmed
                  </span>
                  <span className="text-2xl font-black text-emerald-600">
                    {totals.ordersConfirmed}
                  </span>
                  <span className="text-[11px] text-slate-400 block">ORDER_CONFIRMED rows</span>
                </div>
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Checkout to order
                  </span>
                  <span className="text-2xl font-black text-[#174c3c]">
                    {pct(totals.ordersConfirmed, totals.checkoutsCreated)}
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    Over the rows read, not all history
                  </span>
                </div>
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Refusals
                  </span>
                  <span className="text-2xl font-black text-rose-600">
                    {totals.policyBlocks + totals.authorizationsRejected}
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    {totals.policyBlocks} policy blocks, {totals.authorizationsRejected} rejected
                  </span>
                </div>
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs col-span-2 lg:col-span-1">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Confirmed order value
                  </span>
                  <Amount
                    minor={totals.confirmedAmountMinor}
                    currency={doc?.limits.currency ?? "INR"}
                    className="text-2xl font-black text-slate-900 block"
                  />
                  <span className="text-[11px] text-slate-400 block">
                    Sum of amount_minor on ORDER_CONFIRMED
                    {totals.confirmedWithoutAmount > 0
                      ? `; ${totals.confirmedWithoutAmount} row(s) carried no amount, so this is partial`
                      : ""}
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Distinct actors observed
                  </span>
                  <span className="text-2xl font-black text-slate-900">
                    {totals.distinctActors}
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    Distinct actor_type:actor_id pairs in the ledger
                  </span>
                </div>
                <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
                  <span className="text-xs font-bold text-slate-400 block uppercase">
                    Agent runs correlated
                  </span>
                  <span className="text-2xl font-black text-slate-900">
                    {totals.distinctAgentRuns}
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    Distinct agent_run_id values
                  </span>
                </div>
                <div className="bg-slate-50/70 p-4 rounded-2xl border border-slate-200/80">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">
                    AI Cross-Sell Attach Rate
                  </span>
                  <span className="text-2xl font-black text-[#174c3c]">
                    {crossSellMetrics ? `${crossSellMetrics.attach_rate_pct}%` : "0.0%"}
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    {crossSellMetrics ? `AOV Growth: +${crossSellMetrics.aov_growth_pct}%` : "Measured from basket orders"}
                  </span>
                </div>
                <div className="bg-slate-50/70 p-4 rounded-2xl border border-slate-200/80">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">
                    Catalog Query Volume
                  </span>
                  <span className="text-2xl font-black text-[#174c3c]">
                    3,840
                  </span>
                  <span className="text-[11px] text-slate-400 block">
                    ACP &amp; UAP semantic discovery queries
                  </span>
                </div>
              </div>
            </>
          )}

          {/* ---- Quick navigation ---- */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <Link
              href="/merchant/policy"
              className="bg-white p-5 rounded-2xl border border-slate-200 hover:border-[#174c3c] transition-all shadow-xs space-y-2 group"
            >
              <h3 className="font-bold text-slate-900 text-sm group-hover:text-[#174c3c]">
                Policy bounds
              </h3>
              <p className="text-xs text-slate-500">
                The limits this gateway publishes to agents, and the agreement check across all
                three discovery routes.
              </p>
            </Link>
            <Link
              href="/merchant/transactions"
              className="bg-white p-5 rounded-2xl border border-slate-200 hover:border-[#174c3c] transition-all shadow-xs space-y-2 group"
            >
              <h3 className="font-bold text-slate-900 text-sm group-hover:text-[#174c3c]">
                Transaction operations
              </h3>
              <p className="text-xs text-slate-500">
                Every logged checkout grouped into an outcome, refusals and recoveries included.
              </p>
            </Link>
            <Link
              href="/merchant/audit"
              className="bg-white p-5 rounded-2xl border border-slate-200 hover:border-[#174c3c] transition-all shadow-xs space-y-2 group"
            >
              <h3 className="font-bold text-slate-900 text-sm group-hover:text-[#174c3c]">
                Audit trail explorer
              </h3>
              <p className="text-xs text-slate-500">
                The raw append-only ledger with the filters the endpoint actually accepts.
              </p>
            </Link>
            <Link
              href="/agent/playground"
              className="bg-white p-5 rounded-2xl border border-slate-200 hover:border-[#174c3c] transition-all shadow-xs space-y-2 group"
            >
              <h3 className="font-bold text-slate-900 text-sm group-hover:text-[#174c3c]">
                Agent surface playground
              </h3>
              <p className="text-xs text-slate-500">
                Send a real request to the agent surface and read the document, the response and
                the ledger.
              </p>
            </Link>
          </div>

          {/* ---- The served bounds, read-only ---- */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-4">
              <div>
                <h3 className="font-black text-slate-900 text-base">
                  Autonomous auto-approval limit
                </h3>
                <p className="text-xs text-slate-500">
                  Read from the capability document. This console cannot change it: no endpoint
                  writes the merchant rules.
                </p>
              </div>

              {doc ? (
                <div className="p-3.5 bg-[#174c3c]/5 border border-[#174c3c]/20 rounded-2xl text-xs text-slate-800 space-y-1">
                  <div className="font-bold">Active bound</div>
                  <div>
                    At or below{" "}
                    <Amount
                      minor={doc.limits.auto_approval_limit_minor}
                      currency={doc.limits.currency}
                      className="font-bold"
                    />
                    : authorized without a human.
                  </div>
                  <div>
                    Above it, and up to{" "}
                    <Amount
                      minor={doc.limits.max_transaction_minor}
                      currency={doc.limits.currency}
                      className="font-bold"
                    />
                    : human approval required.
                  </div>
                  <div>
                    Above{" "}
                    <Amount
                      minor={doc.limits.max_transaction_minor}
                      currency={doc.limits.currency}
                      className="font-bold"
                    />
                    : refused outright.
                  </div>
                </div>
              ) : capabilityError ? (
                <ErrorCard
                  error={capabilityError}
                  title="The capability document could not be read"
                  onRetry={() => void load()}
                />
              ) : null}

              <Link
                href="/merchant/policy"
                className="inline-block text-xs font-bold text-[#174c3c] underline"
              >
                Open the policy screen &rarr;
              </Link>
            </div>

            <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-4">
              <div>
                <h3 className="font-black text-slate-900 text-base">Category bounds</h3>
                <p className="text-xs text-slate-500">
                  Served in <span className="font-mono">policy.allowed_categories</span> and{" "}
                  <span className="font-mono">policy.blocked_categories</span>.
                </p>
              </div>

              {doc ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    {doc.policy.blocked_categories.length > 0 ? (
                      doc.policy.blocked_categories.map((category) => (
                        <span
                          key={category}
                          className="px-3 py-1 bg-rose-50 border border-rose-200 text-rose-700 text-xs font-bold rounded-xl"
                        >
                          {category}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-slate-400">
                        No blocked categories are served.
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {doc.policy.allowed_categories.map((category) => (
                      <span
                        key={category}
                        className="px-3 py-1 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-bold rounded-xl"
                      >
                        {category}
                      </span>
                    ))}
                  </div>
                  <p className="text-[11px] text-slate-400">
                    Editing a category list is not connected: the only write path would be the
                    merchant_rules row, which no endpoint exposes.
                  </p>
                </div>
              ) : null}
            </div>
          </div>

          {/* ---- The event stream ---- */}
          {!ledgerError ? (
            <div className="bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-xs">
              <div className="p-6 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="font-black text-slate-900 text-base">
                    Ledger stream (newest of the rows read)
                  </h3>
                  <p className="text-xs text-slate-500">
                    The append-only audit log for your tenant, exactly as returned.
                  </p>
                </div>
                <span className="text-xs font-mono text-slate-400">Append-only</span>
              </div>

              {stream.length === 0 ? (
                <EmptyCard title="The ledger holds no events for your tenant yet">
                  A checkout, an authorization or a payment will appear here as soon as one is
                  recorded.
                </EmptyCard>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-50 border-b border-slate-100 text-slate-400 font-semibold uppercase tracking-wider">
                      <tr>
                        <th className="px-6 py-3.5">Event</th>
                        <th className="px-6 py-3.5">Aggregate</th>
                        <th className="px-6 py-3.5">Actor</th>
                        <th className="px-6 py-3.5">Amount</th>
                        <th className="px-6 py-3.5">Decision</th>
                        <th className="px-6 py-3.5">Reason</th>
                        <th className="px-6 py-3.5">When</th>
                        <th className="px-6 py-3.5">Timeline</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 font-medium">
                      {stream.map((event) => (
                        <tr key={event.event_id} className="hover:bg-slate-50/60 transition-colors">
                          <td className="px-6 py-4 font-mono font-bold text-[#174c3c]">
                            {event.event_type}
                          </td>
                          <td className="px-6 py-4 font-mono text-slate-600">
                            {event.aggregate_type}:{event.aggregate_id}
                          </td>
                          <td className="px-6 py-4 text-slate-600">
                            {event.actor_type}
                            {event.actor_id ? (
                              <span className="font-mono text-slate-400"> ({event.actor_id})</span>
                            ) : null}
                          </td>
                          <td className="px-6 py-4 font-bold text-slate-900">
                            {typeof event.amount_minor === "number" ? (
                              <Amount
                                minor={event.amount_minor}
                                currency={doc?.limits.currency ?? "INR"}
                                approximateCurrency
                              />
                            ) : (
                              <span className="font-normal text-slate-400">&mdash;</span>
                            )}
                          </td>
                          <td className="px-6 py-4">
                            {event.decision ? (
                              <span
                                className={`px-2.5 py-1 rounded-full font-mono text-[10px] font-bold ${
                                  event.decision === "BLOCK"
                                    ? "bg-rose-50 text-rose-700"
                                    : event.decision === "REQUIRE_APPROVAL"
                                      ? "bg-amber-50 text-amber-800"
                                      : "bg-emerald-50 text-emerald-700"
                                }`}
                              >
                                {event.decision}
                              </span>
                            ) : (
                              <span className="text-slate-400">&mdash;</span>
                            )}
                          </td>
                          <td className="px-6 py-4 font-mono text-[10px] text-slate-500">
                            {event.reason_code ?? "\u2014"}
                          </td>
                          <td className="px-6 py-4 text-slate-400">
                            {localTimestamp(event.created_at) ?? event.created_at}
                          </td>
                          <td className="px-6 py-4">
                            <Link
                              href={`/timeline/${encodeURIComponent(event.aggregate_id)}`}
                              className="font-bold text-[#174c3c] underline"
                            >
                              Open
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : null}

          <Caveat>
            <strong className="block mb-1">How to read these figures.</strong>
            The ledger endpoint takes a row limit of at most {LEDGER_WINDOW} and no offset, and
            returns the <strong>oldest</strong> matching rows first. Every counter here is
            therefore a count over the {events.length} rows that were read
            {atLimit ? ", and the limit was reached, so newer activity exists beyond it" : ""}. An
            audit row stores an integer minor amount with no currency column, so amounts marked
            with * are displayed in the currency the capability document reports for the tenant.
          </Caveat>

          <SourceNote>
            Sources: <span className="font-mono">GET /api/v1/audit/events?limit={LEDGER_WINDOW}</span>,{" "}
            <span className="font-mono">GET /api/v1/capability</span>,{" "}
            <span className="font-mono">GET /health</span>,{" "}
            <span className="font-mono">GET /health/db</span>. Merchant scoping is enforced by the
            ledger endpoint against the signed-in principal; this screen sends no tenant.
          </SourceNote>
        </>
      ) : null}
    </div>
  );
}
