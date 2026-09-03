/**
 * The audit ledger as the merchant console's only source of activity figures.
 *
 * `GET /api/v1/audit/events` (`apps/api/routers/audit.py`) answers
 * `{ events: AuditEventRow[] }` — the `audit_event` projection from
 * `services/audit/repository.py::list_events`. Merchant scoping is the
 * endpoint's: it filters on the signed-in principal's own `merchant_id`, this
 * module sends no tenant, and there is no parameter through which it could name
 * another one.
 *
 * ### What the ledger actually contains
 *
 * Only these services append, so only these event types can appear (verified by
 * grepping `append_event` / `append_transition_event` callers):
 *
 * | Event                                        | Written by                        |
 * | -------------------------------------------- | --------------------------------- |
 * | `CHECKOUT_CREATED`                           | `services/checkout/service.py`    |
 * | `POLICY_EVALUATED` (+ decision, reason_code) | `services/policy/service.py`      |
 * | `AUTHORIZATION_REQUESTED/GRANTED/REJECTED`   | `services/authorization/service.py` |
 * | `PAYMENT_CREATED/VERIFIED/FAILED`            | `services/payments/service.py`    |
 * | `ORDER_CONFIRMED`                            | `services/orders/service.py`      |
 * | `IDEMPOTENCY_REPLAYED`                       | `services/payments/idempotency.py` |
 * | `CATALOG_SEARCHED`, `OFFERS_RETURNED`        | `services/catalog/service.py`     |
 * | `INVENTORY_CHANGE_DETECTED`                  | `services/inventory/service.py`   |
 *
 * `PROMPT_SAFETY_CHECKED`, `INTENT_EXTRACTED`, `RESEARCH_PERFORMED`,
 * `TOOL_BLOCKED`, `PRICE_CHANGE_DETECTED`, `OFFER_SELECTED` and
 * `OFFER_REVALIDATED` are declared in `EventType` and **no service appends
 * them**. A screen that counted guard interceptions from this ledger would
 * therefore always report zero and look like a working instrument, so the
 * screens say the surface is not instrumented instead.
 *
 * `INVENTORY_CHANGE_DETECTED` is appended through `append_transition_event`
 * *without* a `merchant_id` (`services/inventory/service.py` passes none), and
 * `list_events` filters `WHERE merchant_id = :merchant_id`. Those rows can
 * therefore never come back from this endpoint. That is a backend gap, reported
 * rather than worked around.
 *
 * ### The paging limitation, stated once here
 *
 * The endpoint takes a `limit` of 1–200 and has no offset, and it orders by
 * `created_at ASC, event_id ASC`. A capped read returns the *oldest* matching
 * rows, so every figure derived here is a figure over the window that was read,
 * not over all history. Screens print the window and whether the cap was reached.
 */

import { apiGet, type ApiError, type ApiResult } from "@/lib/api";

/** One row of `audit_event`, exactly as `list_events` projects it. */
export interface AuditEventRow {
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

/** The event types a service really appends. See the table above. */
export const APPENDED_EVENT_TYPES = [
  "CATALOG_SEARCHED",
  "OFFERS_RETURNED",
  "CHECKOUT_CREATED",
  "POLICY_EVALUATED",
  "AUTHORIZATION_REQUESTED",
  "AUTHORIZATION_GRANTED",
  "AUTHORIZATION_REJECTED",
  "PAYMENT_CREATED",
  "PAYMENT_VERIFIED",
  "PAYMENT_FAILED",
  "ORDER_CONFIRMED",
  "IDEMPOTENCY_REPLAYED",
] as const;

/** Declared in `EventType` but written by nothing today. */
export const UNWRITTEN_EVENT_TYPES = [
  "PROMPT_SAFETY_CHECKED",
  "INTENT_EXTRACTED",
  "OFFER_SELECTED",
  "OFFER_REVALIDATED",
  "PAYMENT_STATUS_CHECKED",
  "PRICE_CHANGE_DETECTED",
  "RESEARCH_PERFORMED",
  "TOOL_BLOCKED",
] as const;

/** `Query(ge=1, le=200)` on the router. */
export const MAX_AUDIT_LIMIT = 200;

/** No currency column exists on `audit_event`; the tenant default is assumed. */
export const FALLBACK_CURRENCY = "INR";

export interface AuditQuery {
  aggregateType?: string | null;
  aggregateId?: string | null;
  eventType?: string | null;
  /** ISO-8601 instants. */
  startAt?: string | null;
  endAt?: string | null;
  limit?: number;
}

export function buildAuditQuery(query: AuditQuery): string {
  const params = new URLSearchParams();
  if (query.aggregateType) params.set("aggregate_type", query.aggregateType);
  if (query.aggregateId) params.set("aggregate_id", query.aggregateId);
  if (query.eventType) params.set("event_type", query.eventType);
  if (query.startAt) params.set("start_at", query.startAt);
  if (query.endAt) params.set("end_at", query.endAt);
  params.set("limit", String(Math.min(Math.max(query.limit ?? 100, 1), MAX_AUDIT_LIMIT)));
  return params.toString();
}

export function fetchAuditEvents(
  query: AuditQuery = {},
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<{ events: AuditEventRow[] }>> {
  return apiGet<{ events: AuditEventRow[] }>(
    `/api/v1/audit/events?${buildAuditQuery(query)}`,
    options
  );
}

export function fetchAggregateEvents(
  aggregateType: string,
  aggregateId: string,
  options: { signal?: AbortSignal } = {}
): Promise<ApiResult<{ events: AuditEventRow[] }>> {
  return apiGet<{ events: AuditEventRow[] }>(
    `/api/v1/audit/aggregates/${encodeURIComponent(aggregateType)}/${encodeURIComponent(
      aggregateId
    )}`,
    options
  );
}

/**
 * True when a call failed because this browser holds no credential the endpoint
 * accepts, rather than because anything went wrong.
 *
 * The audit routes require a *session* role (`require_roles(MERCHANT_ADMIN,
 * MERCHANT_OPERATOR, PLATFORM_ADMIN)`), and no endpoint in `apps/api` calls
 * `start_session`, so a browser cannot obtain one today. Screens name that
 * condition instead of showing a generic failure.
 */
export function isCredentialGap(error: ApiError): boolean {
  return (
    error.code === "UNAUTHENTICATED" ||
    error.code === "FORBIDDEN" ||
    error.status === 401 ||
    error.status === 403
  );
}

export const SESSION_GAP_NOTE =
  "The audit ledger is served only to a signed-in merchant operator or administrator, scoped to their own tenant. No endpoint in this gateway issues a browser session yet, so this read is refused rather than silently filled in.";

// ---------------------------------------------------------------------------
// Derivations. Pure functions over rows the endpoint returned.
// ---------------------------------------------------------------------------

/** The currency an appending service happened to record. Usually absent. */
export function currencyFromMetadata(metadata: Record<string, unknown> | null): string | null {
  const raw = metadata?.["currency"];
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

export function metadataString(
  metadata: Record<string, unknown> | null,
  key: string
): string | null {
  const raw = metadata?.[key];
  if (typeof raw === "string" && raw.length > 0) return raw;
  if (typeof raw === "number") return String(raw);
  return null;
}

export function metadataNumber(
  metadata: Record<string, unknown> | null,
  key: string
): number | null {
  const raw = metadata?.[key];
  return typeof raw === "number" ? raw : null;
}

/** How a transaction ended, judged only from events the ledger holds. */
export type TransactionOutcome =
  | "confirmed"
  | "recovered"
  | "failed"
  | "blocked"
  | "awaiting_approval"
  | "in_progress";

/**
 * One transaction, assembled from the events of a single checkout.
 *
 * The checkout is the join key because it is the one identifier every later
 * event carries: `POLICY_EVALUATED` is appended against the checkout aggregate,
 * and the payment and order events record `checkout_id` in their metadata. An
 * event that names no checkout is kept as its own row rather than dropped, so a
 * blocked or orphaned action stays visible.
 */
export interface TransactionGroup {
  /** The checkout identifier, or the aggregate reference when there is none. */
  key: string;
  /** What `/timeline/{id}` should be pointed at. */
  timelineReference: string;
  events: AuditEventRow[];
  outcome: TransactionOutcome;
  /** Latest event type in the group. */
  latestEventType: string;
  /** The policy decision, when one was recorded. */
  decision: string | null;
  reasonCode: string | null;
  policyVersion: string | null;
  /** The largest amount the group carries; null when no event carried one. */
  amountMinor: number | null;
  currency: string;
  actorType: string;
  actorId: string | null;
  paymentId: string | null;
  orderId: string | null;
  agentRunId: string | null;
  firstSeen: string;
  lastSeen: string;
  /** True when a failure was followed by a verified payment or a confirmed order. */
  recoveredFromFailure: boolean;
  /** True when an idempotent replay spared a duplicate charge. */
  idempotencyReplayed: boolean;
}

function checkoutKeyFor(event: AuditEventRow): string | null {
  if (event.aggregate_type === "checkout") return event.aggregate_id;
  const fromMetadata = metadataString(event.metadata, "checkout_id");
  return fromMetadata;
}

/**
 * Group rows into transactions, preserving ledger order within each group.
 *
 * Deliberately not a `Map`: the build targets ES5 and iterating a `Map` there
 * needs `downlevelIteration`, which is not enabled in `apps/web/tsconfig.json`.
 */
export function groupTransactions(events: AuditEventRow[]): TransactionGroup[] {
  const order: string[] = [];
  const buckets: Record<string, AuditEventRow[]> = {};

  for (let i = 0; i < events.length; i += 1) {
    const event = events[i];
    const key = checkoutKeyFor(event) ?? `${event.aggregate_type}:${event.aggregate_id}`;
    if (!buckets[key]) {
      buckets[key] = [];
      order.push(key);
    }
    buckets[key].push(event);
  }

  return order.map((key) => summariseGroup(key, buckets[key]));
}

function summariseGroup(key: string, rows: AuditEventRow[]): TransactionGroup {
  const types = rows.map((row) => row.event_type);
  const has = (type: string) => types.indexOf(type) >= 0;

  const failedAt = types.indexOf("PAYMENT_FAILED");
  const verifiedAt = types.indexOf("PAYMENT_VERIFIED");
  const confirmedAt = types.indexOf("ORDER_CONFIRMED");
  const recoveredFromFailure =
    failedAt >= 0 &&
    ((verifiedAt >= 0 && verifiedAt > failedAt) || (confirmedAt >= 0 && confirmedAt > failedAt));

  const blockingDecision = rows.filter(
    (row) => row.decision === "BLOCK" || row.event_type === "AUTHORIZATION_REJECTED"
  );
  const approvalDecision = rows.filter((row) => row.decision === "REQUIRE_APPROVAL");

  let outcome: TransactionOutcome;
  if (recoveredFromFailure) outcome = "recovered";
  else if (has("ORDER_CONFIRMED") || has("PAYMENT_VERIFIED")) outcome = "confirmed";
  else if (has("PAYMENT_FAILED")) outcome = "failed";
  else if (blockingDecision.length > 0) outcome = "blocked";
  else if (approvalDecision.length > 0 && !has("AUTHORIZATION_GRANTED"))
    outcome = "awaiting_approval";
  else outcome = "in_progress";

  const withAmount = rows.filter((row) => typeof row.amount_minor === "number");
  const amountMinor =
    withAmount.length > 0
      ? withAmount.reduce(
          (largest, row) => Math.max(largest, row.amount_minor as number),
          0
        )
      : null;

  const decisionRow =
    rows.filter((row) => row.decision !== null).slice(-1)[0] ?? null;
  const currencyRow = rows.filter((row) => currencyFromMetadata(row.metadata) !== null)[0];

  const paymentRow = rows.filter((row) => row.aggregate_type === "payment")[0];
  const orderRow = rows.filter((row) => row.aggregate_type === "order")[0];
  const actorRow = rows.filter((row) => row.actor_type !== "system")[0] ?? rows[0];
  const runRow = rows.filter((row) => row.agent_run_id !== null)[0];

  // Point the timeline at the checkout when we have one: it is the aggregate the
  // policy decision was recorded against, so it carries the most of the story.
  const checkoutRow = rows.filter((row) => row.aggregate_type === "checkout")[0];
  const timelineReference =
    checkoutRow?.aggregate_id ??
    (key.indexOf(":") >= 0 ? key.split(":")[1] : key) ??
    rows[0].aggregate_id;

  return {
    key,
    timelineReference,
    events: rows,
    outcome,
    latestEventType: types[types.length - 1],
    decision: decisionRow ? decisionRow.decision : null,
    reasonCode: decisionRow ? decisionRow.reason_code : null,
    policyVersion: decisionRow ? decisionRow.policy_version : null,
    amountMinor,
    currency: currencyRow ? (currencyFromMetadata(currencyRow.metadata) as string) : FALLBACK_CURRENCY,
    actorType: actorRow.actor_type,
    actorId: actorRow.actor_id,
    paymentId: paymentRow ? paymentRow.aggregate_id : null,
    orderId: orderRow ? orderRow.aggregate_id : null,
    agentRunId: runRow ? runRow.agent_run_id : null,
    firstSeen: rows[0].created_at,
    lastSeen: rows[rows.length - 1].created_at,
    recoveredFromFailure,
    idempotencyReplayed: has("IDEMPOTENCY_REPLAYED"),
  };
}

/** A count per key, as a plain object so no iterator is needed. */
export function countBy(
  events: AuditEventRow[],
  pick: (event: AuditEventRow) => string | null
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (let i = 0; i < events.length; i += 1) {
    const key = pick(events[i]);
    if (key === null) continue;
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

export function distinctCount(
  events: AuditEventRow[],
  pick: (event: AuditEventRow) => string | null
): number {
  return Object.keys(countBy(events, pick)).length;
}

/** Ledger figures the console may state, each traceable to counted rows. */
export interface LedgerTotals {
  events: number;
  catalogSearches: number;
  checkoutsCreated: number;
  policyEvaluations: number;
  authorizationsRequested: number;
  authorizationsGranted: number;
  authorizationsRejected: number;
  policyBlocks: number;
  approvalsRequired: number;
  paymentsCreated: number;
  paymentsVerified: number;
  paymentsFailed: number;
  ordersConfirmed: number;
  idempotentReplays: number;
  /** Sum of `amount_minor` on `ORDER_CONFIRMED` rows. */
  confirmedAmountMinor: number;
  /** How many `ORDER_CONFIRMED` rows carried no amount, so the sum is partial. */
  confirmedWithoutAmount: number;
  distinctActors: number;
  distinctAgentRuns: number;
  distinctRequests: number;
  earliest: string | null;
  latest: string | null;
}

export function ledgerTotals(events: AuditEventRow[]): LedgerTotals {
  const byType = countBy(events, (event) => event.event_type);
  const confirmed = events.filter((event) => event.event_type === "ORDER_CONFIRMED");
  const confirmedWithAmount = confirmed.filter(
    (event) => typeof event.amount_minor === "number"
  );

  return {
    events: events.length,
    catalogSearches: byType["CATALOG_SEARCHED"] ?? 0,
    checkoutsCreated: byType["CHECKOUT_CREATED"] ?? 0,
    policyEvaluations: byType["POLICY_EVALUATED"] ?? 0,
    authorizationsRequested: byType["AUTHORIZATION_REQUESTED"] ?? 0,
    authorizationsGranted: byType["AUTHORIZATION_GRANTED"] ?? 0,
    authorizationsRejected: byType["AUTHORIZATION_REJECTED"] ?? 0,
    policyBlocks: events.filter((event) => event.decision === "BLOCK").length,
    approvalsRequired: events.filter((event) => event.decision === "REQUIRE_APPROVAL").length,
    paymentsCreated: byType["PAYMENT_CREATED"] ?? 0,
    paymentsVerified: byType["PAYMENT_VERIFIED"] ?? 0,
    paymentsFailed: byType["PAYMENT_FAILED"] ?? 0,
    ordersConfirmed: confirmed.length,
    idempotentReplays: byType["IDEMPOTENCY_REPLAYED"] ?? 0,
    confirmedAmountMinor: confirmedWithAmount.reduce(
      (total, event) => total + (event.amount_minor as number),
      0
    ),
    confirmedWithoutAmount: confirmed.length - confirmedWithAmount.length,
    distinctActors: distinctCount(events, (event) =>
      event.actor_id === null ? null : `${event.actor_type}:${event.actor_id}`
    ),
    distinctAgentRuns: distinctCount(events, (event) => event.agent_run_id),
    distinctRequests: distinctCount(events, (event) => event.request_id),
    earliest: events.length > 0 ? events[0].created_at : null,
    latest: events.length > 0 ? events[events.length - 1].created_at : null,
  };
}

/** One observed actor, for the connected-agents and traffic screens. */
export interface ActorActivity {
  actorType: string;
  actorId: string | null;
  events: number;
  checkouts: number;
  ordersConfirmed: number;
  blocked: number;
  agentRuns: number;
  lastSeen: string;
}

export function actorActivity(events: AuditEventRow[]): ActorActivity[] {
  const order: string[] = [];
  const buckets: Record<string, AuditEventRow[]> = {};

  for (let i = 0; i < events.length; i += 1) {
    const event = events[i];
    const key = `${event.actor_type}:${event.actor_id ?? ""}`;
    if (!buckets[key]) {
      buckets[key] = [];
      order.push(key);
    }
    buckets[key].push(event);
  }

  return order.map((key) => {
    const rows = buckets[key];
    return {
      actorType: rows[0].actor_type,
      actorId: rows[0].actor_id,
      events: rows.length,
      checkouts: rows.filter((row) => row.event_type === "CHECKOUT_CREATED").length,
      ordersConfirmed: rows.filter((row) => row.event_type === "ORDER_CONFIRMED").length,
      blocked: rows.filter(
        (row) => row.decision === "BLOCK" || row.event_type === "AUTHORIZATION_REJECTED"
      ).length,
      agentRuns: distinctCount(rows, (row) => row.agent_run_id),
      lastSeen: rows[rows.length - 1].created_at,
    };
  });
}

/** A `datetime-local` value read as an unambiguous UTC instant. */
export function toUtcInstant(localValue: string): string | null {
  if (!localValue) return null;
  const parsed = new Date(localValue);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

/** An instant this many hours before now, for a relative window control. */
export function hoursAgoInstant(hours: number, now: number = Date.now()): string {
  return new Date(now - hours * 3600 * 1000).toISOString();
}

/** Timestamps arrive as the database rendered them; add a local reading. */
export function localTimestamp(raw: string): string | null {
  const parsed = new Date(raw.endsWith("Z") || raw.indexOf("+") >= 0 ? raw : `${raw}Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}
