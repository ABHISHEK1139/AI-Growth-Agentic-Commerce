"use client";

/**
 * Transport for the channel console.
 *
 * Separate from `@/console/api` because every channel endpoint answers with the
 * standard success envelope, which `apiGet`/`apiPost` already read. What this
 * module adds is the *typed* shape of the responses, so the screen cannot invent
 * a field the backend does not send.
 *
 * The access token is write-only. There is deliberately no read function for it:
 * `GET /api/v1/channels/connections` has no token in its response at all, and a
 * client that cannot ask for one cannot leak one.
 */

import { apiDelete, apiGet, apiPost, type ApiError } from "@/lib/api";

export interface ChannelConnection {
  connection_id: string;
  merchant_id: string;
  platform_type: string;
  store_domain: string;
  label: string;
  status: "active" | "disabled";
  product_count: number;
  offer_count: number;
  last_sync_status: "success" | "partial" | "failed" | null;
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface SyncRun {
  sync_run_id: string;
  connection_id: string;
  status: "running" | "success" | "partial" | "failed";
  product_count: number;
  offer_count: number;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface OrderPush {
  push_id: string;
  order_id: string;
  status: "pending" | "success" | "failed" | "duplicate";
  remote_reference: string | null;
  error_message: string | null;
  created_at: string;
}

export interface ConnectionsResponse {
  merchant_id: string;
  connections: ChannelConnection[];
}

export interface ConnectRequest {
  platform_type: string;
  store_url: string;
  access_token: string;
  label?: string;
  sync_now?: boolean;
}

export interface ConnectResponse {
  connection_id: string;
  connection: ChannelConnection | null;
  sync_result: SyncRun | null;
}

export function listConnections(): Promise<
  { ok: true; data: ConnectionsResponse } | { ok: false; error: ApiError }
> {
  return apiGet<ConnectionsResponse>("/api/v1/channels/connections");
}

export function connectStore(
  body: ConnectRequest
): Promise<{ ok: true; data: ConnectResponse } | { ok: false; error: ApiError }> {
  return apiPost<ConnectResponse>("/api/v1/channels/connections", body);
}

export function syncNow(
  connectionId: string,
  limit = 100
): Promise<{ ok: true; data: SyncRun } | { ok: false; error: ApiError }> {
  return apiPost<SyncRun>("/api/v1/channels/sync", {
    connection_id: connectionId,
    limit,
  });
}

export function listSyncRuns(
  connectionId: string
): Promise<{ ok: true; data: SyncRun[] } | { ok: false; error: ApiError }> {
  return unwrapList<SyncRun>(
    `/api/v1/channels/sync-runs?connection_id=${encodeURIComponent(connectionId)}`,
    "runs"
  );
}

export function listOrderPushes(
  connectionId: string
): Promise<{ ok: true; data: OrderPush[] } | { ok: false; error: ApiError }> {
  return unwrapList<OrderPush>(
    `/api/v1/channels/order-pushes?connection_id=${encodeURIComponent(connectionId)}`,
    "pushes"
  );
}

/**
 * These two endpoints wrap their array in a named member (`{"runs": [...]}`
 * rather than a bare array), so the caller has to reach through one level.
 *
 * Normalising here rather than in each component is what stops
 * `listSyncRuns(...).map(...)` from throwing when a caller forgets: a screen that
 * crashes on a shape it did not expect blanks the whole page, and the operator
 * sees "Application error" instead of "this store has no sync history yet".
 */
async function unwrapList<T>(
  path: string,
  member: string
): Promise<{ ok: true; data: T[] } | { ok: false; error: ApiError }> {
  const result = await apiGet<Record<string, unknown>>(path);
  if (!result.ok) return result;
  const value = result.data?.[member];
  return {
    ok: true,
    // An absent or non-array member is an empty list, not a crash. The endpoint
    // either returns the named array or an error envelope, and anything else is
    // not worth blanking a console screen over.
    data: Array.isArray(value) ? (value as T[]) : [],
  };
}

export function disconnectStore(
  connectionId: string
): Promise<{ ok: true; data: unknown } | { ok: false; error: ApiError }> {
  return apiDelete<unknown>(`/api/v1/channels/connections/${connectionId}`);
}
