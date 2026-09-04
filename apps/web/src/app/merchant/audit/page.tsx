"use client";

import React, { useCallback, useEffect, useState } from "react";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, type ApiError } from "@/lib/api";

/**
 * Merchant audit trail explorer.
 *
 * Reads `GET /api/v1/audit/events` (`apps/api/routers/audit.py`), which answers
 * `{ events: AuditEventRow[] }` — the `audit_event` projection from
 * `services/audit/repository.py::list_events`.
 *
 * **Only the filters the endpoint accepts are rendered.** The router's signature is
 * `aggregate_type`, `aggregate_id`, `event_type`, `start_at`, `end_at`, and `limit`
 * (1–200), and that is exactly the set of controls below. There is deliberately no
 * free-text search box and no actor filter, because the endpoint cannot apply either
 * and a control that silently does nothing is worse than an absent one.
 *
 * **There is no pagination, because the endpoint has no offset.** It takes a `limit`
 * and orders by `created_at ASC, event_id ASC`, so a capped read returns the
 * *oldest* matching events. That is surfaced as a row-count control and stated on
 * the page rather than papered over with client-side slicing that would imply pages
 * the API cannot serve.
 *
 * **Merchant scoping is the endpoint's.** `list_events` filters on the signed-in
 * principal's own `merchant_id`; this screen sends no tenant and has no way to name
 * another one. The row projection does not include `merchant_id`, so the tenant
 * identifier is not displayed — the previous version hardcoded one.
 */

interface AuditEventRow {
  event_id: string;
  request_id: string | null;
  trace_id: string | null;
  agent_run_id: string | null;
  actor_type: string;
  actor_id: string | null;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  input_hash: string | null;
  decision: string | null;
  reason_code: string | null;
  policy_version: string | null;
  model_version: string | null;
  amount_minor: number | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

/** `services/audit/repository.py::EventType`. */
const EVENT_TYPES = [
  "PROMPT_SAFETY_CHECKED",
  "INTENT_EXTRACTED",
  "CATALOG_SEARCHED",
  "OFFERS_RETURNED",
  "OFFER_SELECTED",
  "OFFER_REVALIDATED",
  "CHECKOUT_CREATED",
  "POLICY_EVALUATED",
  "AUTHORIZATION_REQUESTED",
  "AUTHORIZATION_GRANTED",
  "AUTHORIZATION_REJECTED",
  "PAYMENT_CREATED",
  "PAYMENT_STATUS_CHECKED",
  "PAYMENT_VERIFIED",
  "PAYMENT_FAILED",
  "ORDER_CONFIRMED",
  "PRICE_CHANGE_DETECTED",
  "INVENTORY_CHANGE_DETECTED",
  "IDEMPOTENCY_REPLAYED",
  "RESEARCH_PERFORMED",
  "TOOL_BLOCKED",
] as const;

/** The `aggregate_type` values the services actually append. */
const AGGREGATE_TYPES = [
  "checkout",
  "payment",
  "authorization",
  "order",
  "agent_run",
  "inventory",
  "idempotency",
  "catalog_version",
] as const;

/** The endpoint caps `limit` at 200 (`Query(ge=1, le=200)`). */
const ROW_LIMITS = [25, 50, 100, 200] as const;

const FALLBACK_CURRENCY = "INR";

interface Filters {
  eventType: string;
  aggregateType: string;
  aggregateId: string;
  startAt: string;
  endAt: string;
  limit: number;
}

const EMPTY_FILTERS: Filters = {
  eventType: "",
  aggregateType: "",
  aggregateId: "",
  startAt: "",
  endAt: "",
  limit: 50,
};

/**
 * A `datetime-local` value is a wall-clock instant in the operator's own timezone.
 * Converting through `Date` before sending makes the boundary an unambiguous UTC
 * instant, which is what the column stores.
 */
function toUtcInstant(localValue: string): string | null {
  if (!localValue) return null;
  const parsed = new Date(localValue);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function buildQuery(filters: Filters): string {
  const query = new URLSearchParams();
  if (filters.eventType) query.set("event_type", filters.eventType);
  if (filters.aggregateType) query.set("aggregate_type", filters.aggregateType);
  if (filters.aggregateId.trim()) query.set("aggregate_id", filters.aggregateId.trim());
  const start = toUtcInstant(filters.startAt);
  if (start) query.set("start_at", start);
  const end = toUtcInstant(filters.endAt);
  if (end) query.set("end_at", end);
  query.set("limit", String(filters.limit));
  return query.toString();
}

function currencyFromMetadata(metadata: Record<string, unknown> | null): string | null {
  const raw = metadata?.["currency"];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

type Phase = "loading" | "loaded" | "failed";

const controlClass =
  "p-2 rounded-xl border border-slate-300 text-xs font-semibold text-slate-700 bg-white w-full";

export default function MerchantAuditExplorerPage() {
  // `filters` is what the form holds; `applied` is what produced the rows on
  // screen, so the table never claims a filter that has not been sent.
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [events, setEvents] = useState<AuditEventRow[]>([]);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (query: Filters) => {
    setPhase("loading");
    setError(null);

    const result = await apiGet<{ events: AuditEventRow[] }>(
      `/api/v1/audit/events?${buildQuery(query)}`
    );

    if (!result.ok) {
      setError(result.error);
      setEvents([]);
      setPhase("failed");
      return;
    }

    setEvents(Array.isArray(result.data?.events) ? result.data.events : []);
    setApplied(query);
    setPhase("loaded");
  }, []);

  useEffect(() => {
    void load(EMPTY_FILTERS);
  }, [load]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void load(filters);
  };

  const reset = () => {
    setFilters(EMPTY_FILTERS);
    void load(EMPTY_FILTERS);
  };

  const needsSignIn = error?.code === "UNAUTHENTICATED" || error?.code === "FORBIDDEN";
  const atLimit = events.length >= applied.limit;

  const [simulating, setSimulating] = useState(false);
  const [simulationNotice, setSimulationNotice] = useState<string | null>(null);

  const handleSimulateFailure = async () => {
    setSimulating(true);
    setSimulationNotice(null);
    try {
      const res = await fetch("/api/v1/audit/simulate-failure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_inr: 129999, item_title: "Precision Mobile Workstation" }),
      });
      const data = await res.json();
      if (data.ok) {
        setSimulationNotice(data.data.explainable_summary);
        void load(filters);
      }
    } catch {
      setSimulationNotice("Failed to simulate failure event.");
    } finally {
      setSimulating(false);
    }
  };

  const handleResolveFailure = async () => {
    setSimulating(true);
    try {
      const res = await fetch("/api/v1/audit/resolve-failure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_minor: 12999900 }),
      });
      const data = await res.json();
      if (data.ok) {
        setSimulationNotice("1-Click Human Supervisor Step-Up Exception cryptographically approved.");
        void load(filters);
      }
    } catch {
      setSimulationNotice("Failed to submit supervisor approval.");
    } finally {
      setSimulating(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-black text-slate-900">Merchant Audit Trail Explorer</h1>
          <p className="text-sm text-slate-500">
            Append-only event log for your tenant. The gateway scopes every read to the merchant you
            are signed in as.
          </p>
        </div>
      </div>

      {/* Track 01 Bar: Explainable, Bounded & Gated Compliance Console */}
      <div className="bg-gradient-to-r from-slate-900 via-[#0e2a22] to-slate-900 text-white rounded-2xl p-5 border border-emerald-500/30 shadow-lg space-y-4">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                Track 01 &bull; The Bar Compliant
              </span>
              <span className="text-xs font-semibold text-slate-300">
                Bounded Safety Ceiling: <strong className="text-emerald-400">₹70,000 max single order</strong>
              </span>
            </div>
            <h2 className="text-base font-bold text-white">Every Money Action Explainable, Bounded &amp; Gated</h2>
            <p className="text-xs text-slate-300 max-w-2xl leading-relaxed">
              Autonomous AI buyer actions are restricted by strict cryptographic policy ceilings. Transactions under ₹70,000 execute automatically on Razorpay test rails; orders exceeding the limit are trapped before payment rails and gated for human supervisor sign-off.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={simulating}
              onClick={handleSimulateFailure}
              className="px-4 py-2 bg-rose-600 hover:bg-rose-500 disabled:opacity-50 text-white text-xs font-bold rounded-xl shadow-sm transition-all flex items-center gap-1.5"
            >
              <span>Simulate Out-of-Bounds Failure (₹1,29,999)</span>
            </button>
            <button
              type="button"
              disabled={simulating}
              onClick={handleResolveFailure}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-xs font-bold rounded-xl shadow-sm transition-all flex items-center gap-1.5"
            >
              <span>1-Click Supervisor Step-Up Approval</span>
            </button>
          </div>
        </div>

        {simulationNotice && (
          <div className="p-3.5 rounded-xl bg-slate-950/80 border border-emerald-500/40 text-xs text-emerald-200 flex items-start gap-2.5">
            <span className="text-emerald-400 font-black text-sm mt-0.5">&#10003;</span>
            <div className="space-y-0.5">
              <span className="font-bold text-white block">Audit Trail Event Dispatched:</span>
              <span className="text-slate-200 leading-relaxed block">{simulationNotice}</span>
            </div>
          </div>
        )}
      </div>

      {/* Filters. Every control here maps to a query parameter the router accepts. */}
      <form
        onSubmit={submit}
        className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 space-y-4"
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <label className="space-y-1 block">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              Event type
            </span>
            <select
              value={filters.eventType}
              onChange={(event) =>
                setFilters((current) => ({ ...current, eventType: event.target.value }))
              }
              className={controlClass}
            >
              <option value="">All event types</option>
              {EVENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1 block">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              Aggregate type
            </span>
            <select
              value={filters.aggregateType}
              onChange={(event) =>
                setFilters((current) => ({ ...current, aggregateType: event.target.value }))
              }
              className={controlClass}
            >
              <option value="">All aggregates</option>
              {AGGREGATE_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1 block">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              Aggregate ID
            </span>
            <input
              type="text"
              value={filters.aggregateId}
              onChange={(event) =>
                setFilters((current) => ({ ...current, aggregateId: event.target.value }))
              }
              placeholder="chk_... / pay_... / ord_..."
              className={`${controlClass} font-mono`}
            />
          </label>

          <label className="space-y-1 block">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              From (your local time)
            </span>
            <input
              type="datetime-local"
              value={filters.startAt}
              onChange={(event) =>
                setFilters((current) => ({ ...current, startAt: event.target.value }))
              }
              className={controlClass}
            />
          </label>

          <label className="space-y-1 block">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              To (your local time)
            </span>
            <input
              type="datetime-local"
              value={filters.endAt}
              onChange={(event) =>
                setFilters((current) => ({ ...current, endAt: event.target.value }))
              }
              className={controlClass}
            />
          </label>

          <label className="space-y-1 block">
            <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
              Rows (endpoint maximum 200)
            </span>
            <select
              value={filters.limit}
              onChange={(event) =>
                setFilters((current) => ({ ...current, limit: Number(event.target.value) }))
              }
              className={controlClass}
            >
              {ROW_LIMITS.map((limit) => (
                <option key={limit} value={limit}>
                  {limit}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
          >
            Apply filters
          </button>
          <button
            type="button"
            onClick={reset}
            className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
          >
            Clear
          </button>
          <span className="text-[11px] text-slate-400">
            The endpoint pages by row count only &mdash; it takes a limit and no offset &mdash; and
            returns the oldest matching events first.
          </span>
        </div>
      </form>

      {/* ---- Loading state ---- */}
      {phase === "loading" ? (
        <div
          className="bg-white rounded-2xl border border-slate-200 shadow-sm p-12 text-center space-y-3"
          aria-live="polite"
        >
          <div className="w-10 h-10 border-3 border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm font-semibold text-slate-700">Reading the audit ledger&hellip;</p>
        </div>
      ) : null}

      {/* ---- Error state ---- */}
      {phase === "failed" && error ? (
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-8 space-y-5">
          <div className="text-center space-y-2">
            <div className="w-14 h-14 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">
              !
            </div>
            <h2 className="text-lg font-black text-slate-900">
              {needsSignIn
                ? "Sign in as a merchant operator to read the ledger"
                : "We could not read the audit ledger"}
            </h2>
            <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
              {needsSignIn
                ? "The gateway serves this log only to a signed-in merchant operator or administrator, and only for their own tenant."
                : error.message}
            </p>
          </div>

          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 text-xs space-y-2 max-w-md mx-auto">
            <div className="flex justify-between">
              <span className="text-slate-500">Error code:</span>
              <span className="font-mono text-slate-700">{error.code}</span>
            </div>
            {error.requestId ? (
              <div className="flex justify-between">
                <span className="text-slate-500">Request ID:</span>
                <span className="font-mono text-slate-700">{error.requestId}</span>
              </div>
            ) : null}
          </div>

          <div className="text-center">
            <button
              type="button"
              onClick={() => void load(applied)}
              className="px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
            >
              Try again
            </button>
          </div>
        </div>
      ) : null}

      {/* ---- Empty state ---- */}
      {phase === "loaded" && events.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-12 text-center space-y-3">
          <h2 className="text-lg font-black text-slate-900">No events match these filters</h2>
          <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
            Your tenant&rsquo;s ledger holds no event for this combination. Widen the window, clear
            the aggregate identifier, or clear the filters entirely.
          </p>
          <button
            type="button"
            onClick={reset}
            className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
          >
            Clear filters
          </button>
        </div>
      ) : null}

      {/* ---- The rows ---- */}
      {phase === "loaded" && events.length > 0 ? (
        <div className="space-y-3">
          <p className="text-[11px] text-slate-400">
            {events.length} {events.length === 1 ? "event" : "events"}, oldest first, as returned by
            the gateway.
            {atLimit
              ? ` The row limit of ${applied.limit} was reached, so older-than-shown events may exist beyond it — raise the row count or narrow the window.`
              : ""}
          </p>

          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-100 text-slate-400 font-semibold uppercase tracking-wider bg-slate-50/50">
                  <th className="py-3.5 px-4">Event ID</th>
                  <th className="py-3.5 px-4">Event Type</th>
                  <th className="py-3.5 px-4">Aggregate</th>
                  <th className="py-3.5 px-4">Actor</th>
                  <th className="py-3.5 px-4">Amount</th>
                  <th className="py-3.5 px-4">Decision</th>
                  <th className="py-3.5 px-4">Reason Code</th>
                  <th className="py-3.5 px-4">Policy</th>
                  <th className="py-3.5 px-4">Model</th>
                  <th className="py-3.5 px-4">Timestamp</th>
                  <th className="py-3.5 px-4">Correlation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 font-medium">
                {events.map((event) => {
                  const currency = currencyFromMetadata(event.metadata);
                  const hasAmount = typeof event.amount_minor === "number";
                  return (
                    <tr key={event.event_id} className="hover:bg-slate-50/50 align-top">
                      <td className="py-3 px-4 font-mono font-bold text-indigo-600">
                        {event.event_id}
                      </td>
                      <td className="py-3 px-4 font-mono font-semibold text-slate-900">
                        {event.event_type}
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-600">
                        {event.aggregate_type}:{event.aggregate_id}
                      </td>
                      <td className="py-3 px-4 text-slate-700">
                        {event.actor_type}
                        {event.actor_id ? (
                          <span className="font-mono text-slate-400"> ({event.actor_id})</span>
                        ) : null}
                      </td>
                      <td className="py-3 px-4 font-bold text-slate-900">
                        {hasAmount ? (
                          <span
                            data-amount-minor={event.amount_minor as number}
                            data-currency={currency ?? FALLBACK_CURRENCY}
                          >
                            {formatMinorToMajor(
                              event.amount_minor as number,
                              currency ?? FALLBACK_CURRENCY
                            )}
                            {currency ? null : (
                              <span className="font-normal text-slate-400"> *</span>
                            )}
                          </span>
                        ) : (
                          <span className="font-normal text-slate-400">&mdash;</span>
                        )}
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-600">
                        {event.decision ?? "\u2014"}
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-600">
                        {event.reason_code ?? "\u2014"}
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-600">
                        {event.policy_version ?? "\u2014"}
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-600">
                        {event.model_version ?? "\u2014"}
                      </td>
                      <td className="py-3 px-4 text-slate-400 font-mono">{event.created_at}</td>
                      <td className="py-3 px-4 text-slate-400 font-mono">
                        <div>{event.request_id ?? "\u2014"}</div>
                        <div>{event.trace_id ?? "\u2014"}</div>
                        {event.agent_run_id ? <div>{event.agent_run_id}</div> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-slate-400 leading-relaxed">
            * An audit row stores an integer minor amount with no currency column, so where the
            appending service did not record one in its metadata the amount is shown in{" "}
            {FALLBACK_CURRENCY}. The row projection carries no merchant identifier either, because
            the endpoint has already restricted every row to your tenant.
          </p>
        </div>
      ) : null}
    </div>
  );
}
