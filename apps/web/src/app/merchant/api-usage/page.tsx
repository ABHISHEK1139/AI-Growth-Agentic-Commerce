"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  actorActivity,
  APPENDED_EVENT_TYPES,
  countBy,
  fetchAuditEvents,
  hoursAgoInstant,
  isCredentialGap,
  ledgerTotals,
  localTimestamp,
  MAX_AUDIT_LIMIT,
  SESSION_GAP_NOTE,
  UNWRITTEN_EVENT_TYPES,
  type AuditEventRow,
} from "@/console/audit";
import {
  Caveat,
  EmptyCard,
  ErrorCard,
  LoadingCard,
  NotConnected,
  SourceNote,
  consolePrimaryButton,
} from "@/console/ui";
import type { ApiError } from "@/lib/api";

/**
 * Agent traffic, as far as the gateway records it.
 *
 * **There is no telemetry endpoint.** Nothing in `apps/api` serves request
 * counts, error rates or latency percentiles: the middleware logs them
 * (`apps/api/middleware/context.py`) and no route reads the logs back. So the six
 * KPI tiles the previous version showed — 148,290 calls, 99.84% success, 24.6ms
 * average, 142 GuardLLM intercepts, 849 duplicate charges saved, 34 active agents
 * — had no possible source, and neither did the per-endpoint P50/P95 table.
 *
 * What *is* recorded is the audit ledger, so this screen counts that and says so.
 * Two figures survive as real measurements: the number of distinct `request_id`
 * values the ledger observed, and the number of `IDEMPOTENCY_REPLAYED` rows, which
 * is literally the count of duplicate money-moving requests the gateway refused to
 * execute twice.
 *
 * **The time range control is wired.** `GET /api/v1/audit/events` accepts
 * `start_at` and `end_at`, so the range buttons set `start_at` and re-query. The
 * previous version's identical-looking control set a `useState` and re-rendered the
 * same constants.
 */

type Phase = "loading" | "loaded" | "failed";

const RANGES = [
  { id: "1h", label: "1h", hours: 1 },
  { id: "24h", label: "24h", hours: 24 },
  { id: "7d", label: "7d", hours: 24 * 7 },
  { id: "30d", label: "30d", hours: 24 * 30 },
] as const;

type RangeId = (typeof RANGES)[number]["id"];

const STREAM_ROWS = 20;

export default function ApiUsageDashboardPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [range, setRange] = useState<RangeId>("24h");
  const [appliedRange, setAppliedRange] = useState<RangeId>("24h");
  const [appliedStart, setAppliedStart] = useState<string | null>(null);
  const [events, setEvents] = useState<AuditEventRow[]>([]);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (rangeId: RangeId) => {
    setPhase("loading");
    setError(null);

    const hours = RANGES.filter((entry) => entry.id === rangeId)[0].hours;
    const startAt = hoursAgoInstant(hours);

    const result = await fetchAuditEvents({ startAt, limit: MAX_AUDIT_LIMIT });
    if (!result.ok) {
      setEvents([]);
      setError(result.error);
      setPhase("failed");
      return;
    }

    setEvents(Array.isArray(result.data?.events) ? result.data.events : []);
    setAppliedRange(rangeId);
    setAppliedStart(startAt);
    setPhase("loaded");
  }, []);

  useEffect(() => {
    void load("24h");
  }, [load]);

  const totals = ledgerTotals(events);
  const byType = countBy(events, (event) => event.event_type);
  const actors = actorActivity(events);
  const stream = events.slice(Math.max(events.length - STREAM_ROWS, 0)).reverse();
  const atLimit = events.length >= MAX_AUDIT_LIMIT;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-6 rounded-2xl border border-slate-200 shadow-xs">
        <div>
          <div className="inline-flex items-center gap-2 px-2.5 py-1 bg-[#174c3c]/10 text-[#174c3c] rounded-full text-xs font-bold uppercase tracking-wider mb-2">
            <span>Gateway activity</span>
          </div>
          <h1 className="text-2xl font-black text-slate-900">Agent Traffic &amp; Ledger Volume</h1>
          <p className="text-sm text-slate-500 mt-1">
            What the append-only ledger recorded in the selected window. Request-level telemetry is
            not served by any endpoint and is marked as such.
          </p>
        </div>

        <div className="flex items-center gap-2 bg-slate-100 p-1 rounded-xl">
          {RANGES.map((entry) => (
            <button
              key={entry.id}
              type="button"
              disabled={phase === "loading"}
              onClick={() => {
                setRange(entry.id);
                void load(entry.id);
              }}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors disabled:opacity-50 ${
                range === entry.id
                  ? "bg-white text-slate-900 shadow-xs"
                  : "text-slate-500 hover:text-slate-900"
              }`}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>

      {phase === "loading" ? <LoadingCard message="Reading the ledger window&hellip;" /> : null}

      {phase === "failed" && error ? (
        <ErrorCard
          error={error}
          title={
            isCredentialGap(error)
              ? "Traffic figures need a merchant session"
              : "The ledger window could not be read"
          }
          credentialGap={isCredentialGap(error)}
          credentialGapNote={SESSION_GAP_NOTE}
          onRetry={() => void load(range)}
        />
      ) : null}

      {phase === "loaded" ? (
        <>
          {/* ---- What can be counted ---- */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">
                Ledger events
              </span>
              <span className="text-2xl font-black text-slate-900">{totals.events}</span>
              <span className="text-[11px] text-slate-400 block">
                In the last {appliedRange}
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">
                Distinct requests
              </span>
              <span className="text-2xl font-black text-[#174c3c]">
                {totals.distinctRequests}
              </span>
              <span className="text-[11px] text-slate-400 block">
                Distinct request_id values that produced a ledger row
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">
                Duplicate charges prevented
              </span>
              <span className="text-2xl font-black text-cyan-700">
                {totals.idempotentReplays}
              </span>
              <span className="text-[11px] text-slate-400 block">
                IDEMPOTENCY_REPLAYED rows: a repeat request answered from the stored response
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">Refusals</span>
              <span className="text-2xl font-black text-rose-600">
                {totals.policyBlocks + totals.authorizationsRejected}
              </span>
              <span className="text-[11px] text-slate-400 block">
                {totals.policyBlocks} policy blocks, {totals.authorizationsRejected}{" "}
                authorizations rejected
              </span>
            </div>
          </div>

          {/* ---- What cannot ---- */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <NotConnected
              label="Total API calls"
              reason="No endpoint serves a request counter. The ledger only records requests that produced a domain event, so it undercounts traffic and must not be presented as a call total."
            />
            <NotConnected
              label="Success rate"
              reason="Response statuses are logged by the middleware and read back by nothing. There is no endpoint that reports them."
            />
            <NotConnected
              label="Latency P50 / P95"
              reason="No endpoint serves latency. Per-request duration is emitted to the log stream and never aggregated for a client."
            />
            <NotConnected
              label="GuardLLM interceptions"
              reason="PROMPT_SAFETY_CHECKED and TOOL_BLOCKED are declared event types that no service appends, so a guard refusal leaves no ledger row to count."
            />
          </div>

          {/* ---- Event type breakdown ---- */}
          <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-xs">
            <div className="p-5 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-black text-slate-900">
                  Ledger volume by event type
                </h2>
                <p className="text-xs text-slate-500">
                  Counts of rows in the window. An event type is a domain transition, not an HTTP
                  route.
                </p>
              </div>
              <Link
                href="/merchant/audit"
                className="text-xs px-3 py-1.5 bg-slate-100 text-slate-700 hover:bg-slate-200 font-bold rounded-lg transition-colors"
              >
                Open the audit explorer &rarr;
              </Link>
            </div>

            {events.length === 0 ? (
              <EmptyCard title="No events in this window">
                Widen the range, or transact through the gateway to produce ledger rows.
              </EmptyCard>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-slate-500 font-semibold text-xs uppercase tracking-wider border-b border-slate-200">
                    <tr>
                      <th className="px-5 py-3">Event type</th>
                      <th className="px-5 py-3">Rows</th>
                      <th className="px-5 py-3">Appended by</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {APPENDED_EVENT_TYPES.map((type) => (
                      <tr key={type} className="hover:bg-slate-50/60 transition-colors">
                        <td className="px-5 py-3 font-mono font-bold text-xs text-slate-800">
                          {type}
                        </td>
                        <td className="px-5 py-3 font-bold text-xs text-slate-900">
                          {byType[type] ?? 0}
                        </td>
                        <td className="px-5 py-3 text-xs text-slate-500">
                          {type === "CATALOG_SEARCHED"
                            ? "the catalog importer (metadata.action = import), not the search path"
                            : type === "OFFERS_RETURNED"
                              ? "the catalog publisher (metadata.action = publish)"
                              : "the owning domain service"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ---- Actors ---- */}
          {actors.length > 0 ? (
            <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-xs">
              <div className="p-5 border-b border-slate-100">
                <h2 className="text-base font-black text-slate-900">Activity by actor</h2>
                <p className="text-xs text-slate-500">
                  Grouped by <span className="font-mono">actor_type:actor_id</span> as recorded on
                  each row.
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-slate-500 font-semibold text-xs uppercase tracking-wider border-b border-slate-200">
                    <tr>
                      <th className="px-5 py-3">Actor</th>
                      <th className="px-5 py-3">Type</th>
                      <th className="px-5 py-3">Events</th>
                      <th className="px-5 py-3">Checkouts</th>
                      <th className="px-5 py-3">Orders</th>
                      <th className="px-5 py-3">Refused</th>
                      <th className="px-5 py-3">Runs</th>
                      <th className="px-5 py-3">Last seen</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-xs">
                    {actors.map((actor) => (
                      <tr
                        key={`${actor.actorType}:${actor.actorId ?? ""}`}
                        className="hover:bg-slate-50/60 transition-colors"
                      >
                        <td className="px-5 py-3 font-mono font-bold text-slate-800">
                          {actor.actorId ?? "\u2014"}
                        </td>
                        <td className="px-5 py-3 text-slate-600">{actor.actorType}</td>
                        <td className="px-5 py-3 font-bold text-slate-900">{actor.events}</td>
                        <td className="px-5 py-3">{actor.checkouts}</td>
                        <td className="px-5 py-3 text-emerald-700 font-bold">
                          {actor.ordersConfirmed}
                        </td>
                        <td className="px-5 py-3 text-rose-700 font-bold">{actor.blocked}</td>
                        <td className="px-5 py-3">{actor.agentRuns}</td>
                        <td className="px-5 py-3 text-slate-400">
                          {localTimestamp(actor.lastSeen) ?? actor.lastSeen}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {/* ---- Recent rows ---- */}
          {stream.length > 0 ? (
            <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-xs">
              <div className="p-5 border-b border-slate-100 flex items-center justify-between">
                <h2 className="text-base font-black text-slate-900">
                  Newest rows in the window
                </h2>
                <button
                  type="button"
                  onClick={() => void load(range)}
                  className={consolePrimaryButton}
                >
                  Refresh
                </button>
              </div>

              <div className="divide-y divide-slate-100">
                {stream.map((event) => (
                  <div
                    key={event.event_id}
                    className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs"
                  >
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="font-mono text-slate-400 font-bold">
                        {event.request_id ?? "no request id"}
                      </span>
                      <span className="font-mono font-bold text-slate-900">
                        {event.event_type}
                      </span>
                      <span className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded font-mono">
                        {event.actor_type}
                        {event.actor_id ? `:${event.actor_id}` : ""}
                      </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-4">
                      {event.decision ? (
                        <span
                          className={`font-semibold px-2 py-0.5 rounded ${
                            event.decision === "BLOCK"
                              ? "bg-rose-50 text-rose-700"
                              : event.decision === "REQUIRE_APPROVAL"
                                ? "bg-amber-50 text-amber-700"
                                : "bg-emerald-50 text-emerald-700"
                          }`}
                        >
                          {event.decision}
                        </span>
                      ) : null}
                      {event.reason_code ? (
                        <span className="font-mono text-slate-500">{event.reason_code}</span>
                      ) : null}
                      <Link
                        href={`/timeline/${encodeURIComponent(event.aggregate_id)}`}
                        className="font-bold text-[#174c3c] underline"
                      >
                        {event.aggregate_type}:{event.aggregate_id}
                      </Link>
                      <span className="text-slate-400">
                        {localTimestamp(event.created_at) ?? event.created_at}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <Caveat>
            <strong className="block mb-1">Event types that can never appear.</strong>
            {UNWRITTEN_EVENT_TYPES.join(", ")} are declared in{" "}
            <span className="font-mono">services/audit/repository.py</span> and no service appends
            them. Inventory events are appended without a merchant identifier, so the
            merchant-scoped read cannot return them. Both are backend gaps.
          </Caveat>

          <SourceNote>
            {events.length} rows from{" "}
            <span className="font-mono">
              GET /api/v1/audit/events?start_at={appliedStart ?? ""}&amp;limit={MAX_AUDIT_LIMIT}
            </span>
            . The endpoint returns the <strong>oldest</strong> matching rows within the window and
            has no offset
            {atLimit
              ? `, and the ${MAX_AUDIT_LIMIT}-row limit was reached, so later rows in this window were not read`
              : ""}
            . Merchant scoping is the endpoint&rsquo;s.
          </SourceNote>
        </>
      ) : null}
    </div>
  );
}
