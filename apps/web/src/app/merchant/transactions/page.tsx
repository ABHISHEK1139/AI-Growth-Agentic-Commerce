"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchAuditEvents,
  groupTransactions,
  isCredentialGap,
  ledgerTotals,
  localTimestamp,
  metadataString,
  SESSION_GAP_NOTE,
  UNWRITTEN_EVENT_TYPES,
  type AuditEventRow,
  type TransactionGroup,
  type TransactionOutcome,
} from "@/console/audit";
import {
  Amount,
  Caveat,
  EmptyCard,
  ErrorCard,
  LoadingCard,
  SourceNote,
  consolePrimaryButton,
} from "@/console/ui";
import type { ApiError } from "@/lib/api";

/**
 * Merchant transaction operations, assembled from the audit ledger.
 *
 * Reads `GET /api/v1/audit/events` and groups the rows by checkout, which is the
 * one identifier every later event carries: `POLICY_EVALUATED` is appended against
 * the checkout aggregate, and the payment and order events record `checkout_id` in
 * their metadata. The grouping and the outcome classification live in
 * `@/console/audit` as pure functions over the returned rows.
 *
 * **Failed, recovered and blocked are distinct outcomes here, not omissions.**
 * `PAYMENT_FAILED` is a failure; a `PAYMENT_FAILED` followed by a
 * `PAYMENT_VERIFIED` or `ORDER_CONFIRMED` on the same checkout is a recovery and
 * says so; a `decision: "BLOCK"` on any row, or an `AUTHORIZATION_REJECTED`, is a
 * blocked action and stays on the list rather than being filtered out. The
 * refusals are the interesting rows on a screen that exists to show bounds
 * working.
 *
 * **What this screen cannot show.** The ledger holds no product title — the
 * checkout audit row records identifiers, not catalogue text — so there is no
 * product column. `TOOL_BLOCKED` and `PROMPT_SAFETY_CHECKED` are declared event
 * types that no service appends, so a guard interception never reaches this list;
 * that is stated rather than implied by an always-empty counter.
 *
 * Every row links to `/timeline/{reference}`, which reads
 * `GET /api/v1/audit/aggregates/{type}/{id}` and infers the aggregate type from
 * the identifier prefix.
 */

type Phase = "loading" | "loaded" | "failed";

const OUTCOME_STYLE: Record<TransactionOutcome, { label: string; className: string }> = {
  confirmed: {
    label: "CONFIRMED",
    className: "bg-emerald-100 text-emerald-800",
  },
  recovered: {
    label: "RECOVERED AFTER FAILURE",
    className: "bg-amber-100 text-amber-900",
  },
  failed: {
    label: "PAYMENT FAILED",
    className: "bg-rose-100 text-rose-800",
  },
  blocked: {
    label: "BLOCKED",
    className: "bg-rose-100 text-rose-800",
  },
  awaiting_approval: {
    label: "AWAITING APPROVAL",
    className: "bg-indigo-100 text-indigo-800",
  },
  in_progress: {
    label: "IN PROGRESS",
    className: "bg-slate-100 text-slate-700",
  },
};

/** The row limit the endpoint accepts (`Query(ge=1, le=200)`). */
const ROW_LIMITS = [50, 100, 200] as const;

const OUTCOME_FILTERS: { id: "all" | TransactionOutcome; label: string }[] = [
  { id: "all", label: "All" },
  { id: "confirmed", label: "Confirmed" },
  { id: "failed", label: "Failed" },
  { id: "recovered", label: "Recovered" },
  { id: "blocked", label: "Blocked" },
  { id: "awaiting_approval", label: "Awaiting approval" },
  { id: "in_progress", label: "In progress" },
];

export default function MerchantTransactionsPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [events, setEvents] = useState<AuditEventRow[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [limit, setLimit] = useState<number>(200);
  const [appliedLimit, setAppliedLimit] = useState<number>(200);
  const [outcomeFilter, setOutcomeFilter] = useState<"all" | TransactionOutcome>("all");
  const [selected, setSelected] = useState<TransactionGroup | null>(null);

  const load = useCallback(async (rows: number) => {
    setPhase("loading");
    setError(null);
    setSelected(null);

    const result = await fetchAuditEvents({ limit: rows });
    if (!result.ok) {
      setError(result.error);
      setEvents([]);
      setPhase("failed");
      return;
    }

    setEvents(Array.isArray(result.data?.events) ? result.data.events : []);
    setAppliedLimit(rows);
    setPhase("loaded");
  }, []);

  useEffect(() => {
    void load(200);
  }, [load]);

  const groups = groupTransactions(events);
  const totals = ledgerTotals(events);
  const visible =
    outcomeFilter === "all"
      ? groups
      : groups.filter((group) => group.outcome === outcomeFilter);
  const atLimit = events.length >= appliedLimit;

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 text-[#174c3c] font-mono text-xs font-bold uppercase">
            <span>Transaction State Machine</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">
            Merchant Transaction Operations
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">
            Every checkout your tenant logged, grouped from the append-only audit ledger, with
            refusals and recoveries shown rather than filtered out.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 self-start md:self-auto">
          <label className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
            Rows
            <select
              value={limit}
              onChange={(event) => {
                const next = Number(event.target.value);
                setLimit(next);
                void load(next);
              }}
              disabled={phase === "loading"}
              className="ml-2 p-2 rounded-xl border border-slate-300 text-xs font-semibold text-slate-700 bg-white"
            >
              {ROW_LIMITS.map((rows) => (
                <option key={rows} value={rows}>
                  {rows}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void load(limit)}
            disabled={phase === "loading"}
            className={consolePrimaryButton}
          >
            {phase === "loading" ? "Reading\u2026" : "Refresh"}
          </button>
        </div>
      </div>

      {phase === "loading" ? <LoadingCard message="Reading the audit ledger&hellip;" /> : null}

      {phase === "failed" && error ? (
        <ErrorCard
          error={error}
          title={
            isCredentialGap(error)
              ? "Sign in as a merchant operator to read your transactions"
              : "We could not read the transaction ledger"
          }
          credentialGap={isCredentialGap(error)}
          credentialGapNote={SESSION_GAP_NOTE}
          onRetry={() => void load(limit)}
        />
      ) : null}

      {phase === "loaded" && groups.length === 0 ? (
        <EmptyCard title="Your tenant has logged no transactions yet">
          The ledger returned no events. A checkout, an authorization or a payment on this tenant
          will appear here as soon as one is recorded.
        </EmptyCard>
      ) : null}

      {phase === "loaded" && groups.length > 0 ? (
        <>
          {/* Outcome counters, each a count of grouped ledger rows */}
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">
                Transactions
              </span>
              <span className="text-2xl font-black text-slate-900">{groups.length}</span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">Confirmed</span>
              <span className="text-2xl font-black text-emerald-600">
                {groups.filter((group) => group.outcome === "confirmed").length}
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">Failed</span>
              <span className="text-2xl font-black text-rose-600">
                {groups.filter((group) => group.outcome === "failed").length}
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">Recovered</span>
              <span className="text-2xl font-black text-amber-600">
                {groups.filter((group) => group.outcome === "recovered").length}
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">Blocked</span>
              <span className="text-2xl font-black text-rose-600">
                {groups.filter((group) => group.outcome === "blocked").length}
              </span>
            </div>
          </div>

          {/* Outcome filter. Client-side over the rows already read, and it says so. */}
          <div className="flex flex-wrap items-center gap-2">
            {OUTCOME_FILTERS.map((filter) => (
              <button
                key={filter.id}
                type="button"
                onClick={() => setOutcomeFilter(filter.id)}
                className={`px-3.5 py-1.5 rounded-xl text-xs font-bold transition-colors ${
                  outcomeFilter === filter.id
                    ? "bg-slate-900 text-white"
                    : "bg-white text-slate-600 hover:bg-slate-100 border border-slate-200"
                }`}
              >
                {filter.label}
              </button>
            ))}
            <span className="text-[11px] text-slate-400">
              Outcome is derived here from the rows already read; the endpoint has no outcome
              filter, so this narrows the {groups.length} groups on screen and does not re-query.
            </span>
          </div>

          {/* Transactions Table */}
          <div className="bg-white rounded-3xl border border-slate-200 shadow-xs overflow-x-auto">
            <table className="w-full text-xs text-left">
              <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[11px]">
                <tr>
                  <th className="p-4">Checkout</th>
                  <th className="p-4">Actor</th>
                  <th className="p-4">Amount</th>
                  <th className="p-4">Policy Decision</th>
                  <th className="p-4">Reason Code</th>
                  <th className="p-4">Payment / Order</th>
                  <th className="p-4">Outcome</th>
                  <th className="p-4">Last Event</th>
                  <th className="p-4">Timeline</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 font-medium text-slate-700">
                {visible.map((group) => (
                  <tr
                    key={group.key}
                    onClick={() => setSelected(group)}
                    className="hover:bg-slate-50/80 cursor-pointer transition-colors align-top"
                  >
                    <td className="p-4 font-mono font-bold text-[#174c3c]">{group.key}</td>
                    <td className="p-4">
                      <div>{group.actorType}</div>
                      {group.actorId ? (
                        <div className="font-mono text-[10px] text-slate-400">
                          {group.actorId}
                        </div>
                      ) : null}
                    </td>
                    <td className="p-4 font-black text-slate-900">
                      {group.amountMinor !== null ? (
                        <Amount minor={group.amountMinor} currency={group.currency} />
                      ) : (
                        <span className="font-normal text-slate-400">&mdash;</span>
                      )}
                    </td>
                    <td className="p-4 font-mono text-[11px]">{group.decision ?? "\u2014"}</td>
                    <td className="p-4 font-mono text-[11px]">{group.reasonCode ?? "\u2014"}</td>
                    <td className="p-4 font-mono text-[10px] text-slate-500">
                      <div>{group.paymentId ?? "\u2014"}</div>
                      <div>{group.orderId ?? "\u2014"}</div>
                    </td>
                    <td className="p-4">
                      <span
                        className={`px-2.5 py-0.5 rounded-full font-bold text-[10px] ${
                          OUTCOME_STYLE[group.outcome].className
                        }`}
                      >
                        {OUTCOME_STYLE[group.outcome].label}
                      </span>
                      {group.idempotencyReplayed ? (
                        <span className="block mt-1 text-[10px] font-bold text-cyan-700">
                          idempotent replay
                        </span>
                      ) : null}
                    </td>
                    <td className="p-4">
                      <div className="font-mono text-[10px]">{group.latestEventType}</div>
                      <div className="text-slate-400">
                        {localTimestamp(group.lastSeen) ?? group.lastSeen}
                      </div>
                    </td>
                    <td className="p-4">
                      <Link
                        href={`/timeline/${encodeURIComponent(group.timelineReference)}`}
                        onClick={(clickEvent) => clickEvent.stopPropagation()}
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

          {visible.length === 0 ? (
            <EmptyCard
              title="No transaction in the rows read has that outcome"
              action={
                <button
                  type="button"
                  onClick={() => setOutcomeFilter("all")}
                  className={consolePrimaryButton}
                >
                  Show all outcomes
                </button>
              }
            >
              The ledger returned {groups.length} grouped transactions, none of them with this
              outcome. Raise the row count to read further back.
            </EmptyCard>
          ) : null}

          <Caveat>
            <strong className="block mb-1">What is not on this list.</strong>
            {UNWRITTEN_EVENT_TYPES.join(", ")} are declared event types that no service appends
            today, so a guard interception or a price-change detection cannot appear here.
            Inventory events are appended without a merchant identifier
            (`services/inventory/service.py`) and the ledger read filters on one, so reservation
            activity is invisible to this endpoint. Both are backend gaps.
          </Caveat>

          <SourceNote>
            {events.length} ledger rows read from{" "}
            <span className="font-mono">GET /api/v1/audit/events?limit={appliedLimit}</span>,
            grouped into {groups.length} transactions. The endpoint pages by row count only — it
            takes a limit and no offset — and returns the <strong>oldest</strong> matching events
            first.
            {atLimit
              ? ` The limit of ${appliedLimit} was reached, so newer activity may exist beyond it.`
              : ""}{" "}
            Counters on this page are counts of those rows: {totals.policyEvaluations} policy
            evaluations, {totals.authorizationsGranted} authorizations granted,{" "}
            {totals.authorizationsRejected} rejected, {totals.paymentsVerified} payments verified,{" "}
            {totals.ordersConfirmed} orders confirmed. Merchant scoping is the endpoint&rsquo;s: it
            filters on the signed-in principal&rsquo;s own tenant and this screen sends none.
          </SourceNote>
        </>
      ) : null}

      {/* Event trace for one transaction. Real rows, in ledger order. */}
      {selected ? (
        <div
          onClick={() => setSelected(null)}
          className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4"
        >
          <div
            onClick={(event) => event.stopPropagation()}
            className="bg-white rounded-3xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-6 sm:p-8 shadow-2xl space-y-6 text-xs"
          >
            <div className="flex items-start justify-between border-b border-slate-100 pb-3 gap-4">
              <div>
                <h3 className="font-black text-slate-900 text-base">Ledger trace</h3>
                <span className="text-slate-400 font-mono text-[11px]">{selected.key}</span>
              </div>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="text-slate-400 hover:text-slate-600 font-bold"
                aria-label="Close"
              >
                &#10005;
              </button>
            </div>

            <div className="space-y-2 font-mono">
              {selected.events.map((event) => (
                <div
                  key={event.event_id}
                  className="p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-1"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-bold text-slate-900">{event.event_type}</span>
                    <span className="text-[10px] text-slate-400">
                      {localTimestamp(event.created_at) ?? event.created_at}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500">
                    {event.aggregate_type}:{event.aggregate_id}
                  </div>
                  {event.decision || event.reason_code ? (
                    <div className="text-[11px] text-slate-700">
                      decision={event.decision ?? "\u2014"} reason={event.reason_code ?? "\u2014"}
                    </div>
                  ) : null}
                  {typeof event.amount_minor === "number" ? (
                    <div className="text-[11px]">
                      amount=
                      <Amount
                        minor={event.amount_minor}
                        currency={selected.currency}
                        className="font-bold text-slate-900"
                      />
                    </div>
                  ) : null}
                  {metadataString(event.metadata, "reason") ? (
                    <div className="text-[11px] text-rose-700">
                      {metadataString(event.metadata, "reason")}
                    </div>
                  ) : null}
                  <div className="text-[10px] text-slate-400">{event.event_id}</div>
                </div>
              ))}
            </div>

            <div className="flex flex-wrap gap-3">
              <Link
                href={`/timeline/${encodeURIComponent(selected.timelineReference)}`}
                className={consolePrimaryButton}
              >
                Open full timeline
              </Link>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="px-5 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
