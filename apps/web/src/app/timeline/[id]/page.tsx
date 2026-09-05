"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { formatMinorToMajor } from "@/lib/money";
import { apiGet, type ApiError } from "@/lib/api";

/**
 * Audit ledger timeline for one aggregate.
 *
 * Reads `GET /api/v1/audit/aggregates/{aggregate_type}/{aggregate_id}`, which
 * answers `{ events: AuditEventRow[] }`. The row shape is the `audit_event` table
 * projection in `services/audit/repository.py::list_events`: every column is
 * returned, nothing is computed.
 *
 * **Order is the ledger's, not ours.** `list_events` orders by
 * `created_at ASC, event_id ASC` and the aggregate route runs the same query, so
 * events arrive in the causal order the gateway appended them. This screen renders
 * that array as it came back; there is no client-side sort anywhere in this file,
 * because re-sorting the ledger would make it a rendering of our assumptions rather
 * than a rendering of the record.
 *
 * Two things the ledger row does not carry, and which are therefore not invented
 * here:
 *
 * * **A currency.** `audit_event.amount_minor` is a bare integer. Where the
 *   appending service happened to put a currency in `metadata` it is used; where it
 *   did not, the display falls back to the tenant default and the page says so.
 * * **A human-readable description.** The previous version of this screen wrote
 *   sentences like "HMAC webhook signature verified" next to each event. Those were
 *   hardcoded prose, not audit data. What replaces them is the row's own
 *   `decision`, `reason_code`, `policy_version`, `model_version`, and `metadata`.
 *
 * The endpoint is merchant-scoped (`require_roles(MERCHANT_ADMIN, MERCHANT_OPERATOR,
 * PLATFORM_ADMIN)` in `apps/api/routers/audit.py`) and filters by the caller's own
 * `merchant_id`. Scoping is therefore the server's; this screen passes no tenant and
 * could not widen one if it tried. A buyer session is refused, and that refusal is
 * rendered as its own state rather than as a generic failure.
 */

/** One row of `audit_event`, exactly as `list_events` projects it. */
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

/**
 * The aggregate types the gateway actually appends events for. Taken from the
 * `aggregate_type=` arguments in `services/**`; the endpoint accepts any string, so
 * offering a fixed list keeps the control from inviting a query that cannot match.
 */
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

type AggregateType = (typeof AGGREGATE_TYPES)[number];

/**
 * Identifier prefixes are assigned by `new_id()` in
 * `packages/observability/context.py`, so the reference itself says which aggregate
 * it names. The payment screen links here with a `checkout_id`, which is why
 * `checkout` is both a prefix mapping and the fallback.
 */
const PREFIX_TO_AGGREGATE: Record<string, AggregateType> = {
  chk: "checkout",
  pay: "payment",
  ath: "authorization",
  ord: "order",
  run: "agent_run",
  // Inventory events are appended against the offer they moved stock for, so an
  // offer reference is the inventory aggregate's identifier.
  off: "inventory",
  idm: "idempotency",
  cat: "catalog_version",
};

function inferAggregateType(reference: string): AggregateType {
  const prefix = reference.split("_")[0]?.toLowerCase() ?? "";
  return PREFIX_TO_AGGREGATE[prefix] ?? "checkout";
}

/** The currency for an amount, when the appending service recorded one. */
function currencyFromMetadata(metadata: Record<string, unknown> | null): string | null {
  const raw = metadata?.["currency"];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

const FALLBACK_CURRENCY = "INR";

/** Timestamps arrive as the database rendered them; shown as-is plus a local reading. */
function localTimestamp(raw: string): string | null {
  const parsed = new Date(raw.endsWith("Z") || raw.includes("+") ? raw : `${raw}Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "medium" });
}

/** Metadata entries worth surfacing inline, with secrets already redacted server-side. */
function metadataEntries(metadata: Record<string, unknown> | null): [string, string][] {
  if (!metadata) return [];
  return Object.entries(metadata)
    .filter(([key]) => key !== "currency")
    .map(([key, value]) => [
      key,
      typeof value === "string" ? value : JSON.stringify(value),
    ]) as [string, string][];
}

type Phase = "loading" | "loaded" | "failed";

export default function TimelinePage({ params }: { params?: { id: string } }) {
  const routeParams = useParams<{ id: string }>();
  const reference = routeParams?.id || params?.id || "";
  const inferred = useMemo(() => inferAggregateType(reference), [reference]);

  const [aggregateType, setAggregateType] = useState<AggregateType>(inferred);
  const [events, setEvents] = useState<AuditEventRow[]>([]);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setAggregateType(inferred);
  }, [inferred]);

  const load = useCallback(async () => {
    if (!reference) {
      setPhase("failed");
      return;
    }
    setPhase("loading");
    setError(null);

    const result = await apiGet<{ events: AuditEventRow[] }>(
      `/api/v1/audit/aggregates/${encodeURIComponent(aggregateType)}/${encodeURIComponent(reference)}`
    );

    if (!result.ok) {
      setError(result.error);
      setEvents([]);
      setPhase("failed");
      return;
    }

    // Trusted as returned. See the note at the top of this file.
    setEvents(Array.isArray(result.data?.events) ? result.data.events : []);
    setPhase("loaded");
  }, [reference, aggregateType]);

  useEffect(() => {
    void load();
  }, [load]);

  // ---- Empty state: no reference in the route -----------------------------
  if (!reference) {
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm text-center space-y-3">
          <h1 className="text-2xl font-black text-slate-900">No reference given</h1>
          <p className="text-sm text-slate-500">
            This address does not name an aggregate, so there is no ledger to read.
          </p>
          <Link
            href="/orders"
            className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
          >
            View My Orders &rarr;
          </Link>
        </div>
      </div>
    );
  }

  const forbidden = error?.code === "FORBIDDEN";
  const unauthenticated = error?.code === "UNAUTHENTICATED";

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-black text-slate-900">Audit Ledger Timeline</h1>
          <p className="text-sm text-slate-500">
            Append-only audit record for aggregate{" "}
            <span className="font-mono text-slate-800">{reference}</span>
          </p>
          <p className="text-[11px] text-slate-400 mt-1">
            Rendered in the ledger&rsquo;s own order &mdash; the gateway returns these events
            ordered by append time, and this page does not re-sort them.
          </p>
        </div>

        {/* Wired to the path segment the endpoint actually takes. */}
        <label className="text-[11px] font-bold text-slate-500 space-y-1 block shrink-0">
          <span className="block uppercase tracking-wider">Aggregate type</span>
          <select
            value={aggregateType}
            onChange={(event) => setAggregateType(event.target.value as AggregateType)}
            className="p-2 rounded-xl border border-slate-300 text-xs font-semibold text-slate-700 bg-white"
          >
            {AGGREGATE_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* ---- Loading state ---- */}
      {phase === "loading" ? (
        <div
          className="bg-white p-10 rounded-2xl border border-slate-200 shadow-sm text-center space-y-3"
          aria-live="polite"
        >
          <div className="w-10 h-10 border-3 border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm font-semibold text-slate-700">Reading the audit ledger&hellip;</p>
          <p className="text-[11px] text-slate-400 font-mono">
            {aggregateType} / {reference}
          </p>
        </div>
      ) : null}

      {/* ---- Error state ---- */}
      {phase === "failed" && error ? (
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-5">
          <div className="text-center space-y-2">
            <div className="w-14 h-14 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">
              !
            </div>
            <h2 className="text-xl font-black text-slate-900">
              {forbidden || unauthenticated
                ? "The audit ledger is a merchant-side surface"
                : "We could not read the audit ledger"}
            </h2>
            <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
              {forbidden || unauthenticated
                ? "The gateway serves this ledger only to a signed-in merchant operator or administrator, and only for their own tenant. Sign in with a merchant account to read it."
                : error.message}
            </p>
          </div>

          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 text-xs space-y-2">
            <div className="flex justify-between">
              <span className="text-slate-500">Aggregate:</span>
              <span className="font-mono text-slate-700">
                {aggregateType}:{reference}
              </span>
            </div>
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

          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <button
              type="button"
              onClick={() => void load()}
              className="px-6 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl transition-all"
            >
              Try again
            </button>
            <Link
              href="/orders"
              className="px-6 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all text-center"
            >
              View My Orders
            </Link>
          </div>
        </div>
      ) : null}

      {/* ---- Empty state ---- */}
      {phase === "loaded" && events.length === 0 ? (
        <div className="bg-white p-10 rounded-2xl border border-slate-200 shadow-sm text-center space-y-3">
          <h2 className="text-lg font-black text-slate-900">No events recorded for this reference</h2>
          <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
            The ledger holds nothing for{" "}
            <span className="font-mono text-slate-700">
              {aggregateType}:{reference}
            </span>
            . Either no money action has touched this reference yet, or it is recorded under a
            different aggregate type &mdash; switch the selector above to look.
          </p>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-block px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] text-white font-bold text-xs rounded-xl shadow-sm transition-all"
          >
            Check again
          </button>
        </div>
      ) : null}

      {/* ---- The ledger ---- */}
      {phase === "loaded" && events.length > 0 ? (
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4">
          <p className="text-[11px] text-slate-400">
            {events.length} {events.length === 1 ? "event" : "events"}, oldest first, as returned by
            the gateway.
          </p>

          <div className="relative border-l-2 border-slate-200 ml-4 pl-6 space-y-6">
            {events.map((event) => {
              const currency = currencyFromMetadata(event.metadata);
              const hasAmount = typeof event.amount_minor === "number";
              const local = localTimestamp(event.created_at);
              const extras = metadataEntries(event.metadata);

              return (
                <div key={event.event_id} className="relative">
                  <div className="absolute -left-[31px] top-1 w-4 h-4 rounded-full bg-indigo-600 border-2 border-white ring-2 ring-indigo-200" />

                  <div className="space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-mono font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">
                        {event.event_type}
                      </span>
                      <span className="text-xs text-slate-400 font-mono">{event.created_at}</span>
                      {local ? (
                        <span className="text-[11px] text-slate-400">({local} local)</span>
                      ) : null}
                    </div>

                    <div className="text-sm font-semibold text-slate-900 flex flex-wrap items-center gap-2">
                      <span>
                        Actor:{" "}
                        <span className="font-medium text-slate-700">
                          {event.actor_type}
                          {event.actor_id ? (
                            <span className="font-mono text-slate-500"> ({event.actor_id})</span>
                          ) : null}
                        </span>
                      </span>
                      {event.decision ? (
                        <span className="px-2 py-0.5 text-xs bg-slate-100 rounded font-mono text-slate-600">
                          Decision: {event.decision}
                        </span>
                      ) : null}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs pt-1">
                      <div className="flex justify-between gap-3">
                        <span className="text-slate-500">Aggregate:</span>
                        <span className="font-mono text-slate-700 truncate">
                          {event.aggregate_type}:{event.aggregate_id}
                        </span>
                      </div>
                      <div className="flex justify-between gap-3">
                        <span className="text-slate-500">Amount:</span>
                        {hasAmount ? (
                          <span
                            className="font-bold text-slate-900"
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
                          <span className="text-slate-400">not applicable</span>
                        )}
                      </div>
                      <div className="flex justify-between gap-3">
                        <span className="text-slate-500">Reason code:</span>
                        <span className="font-mono text-slate-700">
                          {event.reason_code ?? "\u2014"}
                        </span>
                      </div>
                      <div className="flex justify-between gap-3">
                        <span className="text-slate-500">Policy version:</span>
                        <span className="font-mono text-slate-700">
                          {event.policy_version ?? "\u2014"}
                        </span>
                      </div>
                      <div className="flex justify-between gap-3">
                        <span className="text-slate-500">Model version:</span>
                        <span className="font-mono text-slate-700">
                          {event.model_version ?? "\u2014"}
                        </span>
                      </div>
                      <div className="flex justify-between gap-3">
                        <span className="text-slate-500">Event ID:</span>
                        <span className="font-mono text-slate-700 truncate">{event.event_id}</span>
                      </div>
                    </div>

                    {/* The correlation identifiers. This is what makes a money action
                        traceable back to the request that caused it. */}
                    <div className="bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-[11px] font-mono text-slate-500 space-y-0.5 mt-1">
                      <div>
                        <span className="font-bold text-slate-700">request_id: </span>
                        {event.request_id ?? "\u2014"}
                      </div>
                      <div>
                        <span className="font-bold text-slate-700">trace_id: </span>
                        {event.trace_id ?? "\u2014"}
                      </div>
                      <div>
                        <span className="font-bold text-slate-700">agent_run_id: </span>
                        {event.agent_run_id ?? "\u2014"}
                      </div>
                      {event.input_hash ? (
                        <div className="break-all">
                          <span className="font-bold text-slate-700">input_hash: </span>
                          {event.input_hash}
                        </div>
                      ) : null}
                    </div>

                    {extras.length > 0 ? (
                      <dl className="text-[11px] text-slate-500 grid grid-cols-1 sm:grid-cols-2 gap-x-6">
                        {extras.map(([key, value]) => (
                          <div key={key} className="flex justify-between gap-3">
                            <dt className="text-slate-400">{key}:</dt>
                            <dd className="font-mono text-slate-600 truncate">{value}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>

          <p className="text-[11px] text-slate-400 leading-relaxed border-t border-slate-100 pt-3">
            * The ledger row stores an integer minor amount with no currency column, so where the
            appending service did not record one in its metadata the amount is shown in{" "}
            {FALLBACK_CURRENCY}. Every figure above is the stored integer, formatted only for
            display.
          </p>
        </div>
      ) : null}
    </div>
  );
}
