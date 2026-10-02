"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  connectStore,
  disconnectStore,
  listConnections,
  listOrderPushes,
  listSyncRuns,
  syncNow,
  type ChannelConnection,
  type OrderPush,
  type SyncRun,
} from "@/console/channels";
import { Caveat, EmptyCard, ErrorCard, LoadingCard, SourceNote } from "@/console/ui";
import type { ApiError } from "@/lib/api";

/**
 * Store channels: Shopify, WooCommerce, custom REST, and catalog feeds.
 *
 * The screen before this was a form that posted to `/api/v1/connectors/register`,
 * fired a sync, and threw the result away. Connections lived in an in-memory
 * registry, so they vanished on restart, and nothing recorded whether a sync had
 * ever run. This screen reads `channel_connection` and the two append-only logs
 * behind it, so what it shows is what the server did rather than what a
 * registration call claimed.
 *
 * What it will not show: the store access token. There is no endpoint that
 * returns one, so a merchant who loses their Shopify token revokes and reissues
 * it in the Shopify admin.
 */

const PLATFORMS = [
  { id: "shopify", label: "Shopify", hint: "Admin API · products, variants, inventory, orders" },
  { id: "woocommerce", label: "WooCommerce", hint: "REST API v3 · products, stock, categories" },
  { id: "generic_rest", label: "Custom REST", hint: "Your own JSON API" },
  { id: "catalog_feed", label: "Catalog Feed", hint: "CSV or JSONL, uploaded as the token body" },
] as const;

function statusBadge(status: ChannelConnection["last_sync_status"]) {
  if (status === "success") {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold uppercase text-emerald-700">
        <CheckCircle2 className="h-3 w-3" />
        Synced
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[10px] font-bold uppercase text-rose-700">
        <XCircle className="h-3 w-3" />
        Failed
      </span>
    );
  }
  if (status === "partial") {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-700">
        <AlertTriangle className="h-3 w-3" />
        Partial
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-500">
      <CircleSlash className="h-3 w-3" />
      Never synced
    </span>
  );
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function ConnectForm({ onDone }: { onDone: () => void }) {
  const [platform, setPlatform] = useState<string>("shopify");
  const [storeUrl, setStoreUrl] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [label, setLabel] = useState("");
  const [syncNowAfter, setSyncNowAfter] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    const result = await connectStore({
      platform_type: platform,
      store_url: storeUrl.trim(),
      access_token: accessToken,
      label: label.trim(),
      sync_now: syncNowAfter,
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // The token is dropped from component state the moment it is no longer
    // needed. It is never persisted, never logged, and never sent anywhere
    // except this one call.
    setAccessToken("");
    setStoreUrl("");
    setLabel("");
    setNote(
      result.data.sync_result
        ? `Saved. Sync ${result.data.sync_result.status}: ${result.data.sync_result.product_count} products, ${result.data.sync_result.offer_count} offers.`
        : "Saved. Run a sync to pull the catalog."
    );
    onDone();
  }

  const selected = PLATFORMS.find((item) => item.id === platform);

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <Plus className="h-4 w-4 text-[#174c3c]" />
        <h2 className="text-sm font-black text-slate-900">Connect a store</h2>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label htmlFor="platform" className="block text-xs font-bold text-slate-700">
            Platform
          </label>
          <select
            id="platform"
            value={platform}
            onChange={(event) => setPlatform(event.target.value)}
            className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#174c3c]"
          >
            {PLATFORMS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
          {selected ? (
            <p className="text-[11px] text-slate-500">{selected.hint}</p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="store-url" className="block text-xs font-bold text-slate-700">
            Store domain
          </label>
          <input
            id="store-url"
            required
            value={storeUrl}
            onChange={(event) => setStoreUrl(event.target.value)}
            placeholder="mystore.myshopify.com"
            className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#174c3c]"
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="access-token" className="block text-xs font-bold text-slate-700">
          Access token
        </label>
        <input
          id="access-token"
          required
          type="password"
          autoComplete="off"
          value={accessToken}
          onChange={(event) => setAccessToken(event.target.value)}
          placeholder={platform === "catalog_feed" ? "Paste CSV or JSONL content" : "shpat_…"}
          className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#174c3c]"
        />
        <p className="text-[11px] text-slate-500">
          Encrypted at rest and never returned by the API. Re-entering the same store
          updates the existing connection rather than creating a second one.
        </p>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="channel-label" className="block text-xs font-bold text-slate-700">
          Label <span className="font-normal text-slate-400">(optional)</span>
        </label>
        <input
          id="channel-label"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder="Main storefront"
          className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-[#174c3c]"
        />
      </div>

      <label className="flex items-start gap-2 text-xs text-slate-700">
        <input
          type="checkbox"
          checked={syncNowAfter}
          onChange={(event) => setSyncNowAfter(event.target.checked)}
          className="mt-0.5"
        />
        <span>
          Pull the catalog immediately. A live sync is a multi-second store API call made
          inside this request, so it is off by default.
        </span>
      </label>

      {error ? (
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-800">
          {error.message}
        </p>
      ) : null}
      {note ? (
        <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800">
          {note}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={busy}
        className="rounded-xl bg-[#174c3c] px-5 py-2.5 text-xs font-bold text-white shadow-sm transition-colors hover:bg-[#103c2f] disabled:opacity-50"
      >
        {busy ? "Saving…" : "Save connection"}
      </button>
    </form>
  );
}

function ConnectionDetail({ connection }: { connection: ChannelConnection }) {
  const [runs, setRuns] = useState<SyncRun[] | null>(null);
  const [pushes, setPushes] = useState<OrderPush[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    const [syncResult, pushResult] = await Promise.all([
      listSyncRuns(connection.connection_id),
      listOrderPushes(connection.connection_id),
    ]);
    if (!syncResult.ok) {
      setError(syncResult.error);
      setRuns([]);
      setPushes([]);
      return;
    }
    setRuns(syncResult.data);
    setPushes(pushResult.ok ? pushResult.data : []);
    if (!pushResult.ok) setError(pushResult.error);
  }, [connection.connection_id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSync() {
    setBusy(true);
    const result = await syncNow(connection.connection_id);
    setBusy(false);
    if (result.ok) {
      await load();
    } else {
      setError(result.error);
    }
  }

  return (
    <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-black text-slate-900">
              {connection.label || connection.store_domain}
            </h3>
            {statusBadge(connection.last_sync_status)}
            {connection.status === "disabled" ? (
              <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-500">
                Disabled
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate font-mono text-[11px] text-slate-500">
            {connection.platform_type} · {connection.store_domain}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleSync}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`} />
            {busy ? "Syncing" : "Sync now"}
          </button>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Products
          </dt>
          <dd className="text-base font-black text-slate-900" data-count={connection.product_count}>
            {connection.product_count}
          </dd>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Offers
          </dt>
          <dd className="text-base font-black text-slate-900" data-count={connection.offer_count}>
            {connection.offer_count}
          </dd>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Last sync
          </dt>
          <dd className="text-xs font-bold text-slate-700">
            {when(connection.last_synced_at)}
          </dd>
        </div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <dt className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
            Connected
          </dt>
          <dd className="text-xs font-bold text-slate-700">{when(connection.created_at)}</dd>
        </div>
      </dl>

      {connection.last_error ? (
        <Caveat>
          <strong className="font-bold">Last sync reported:</strong>{" "}
          <span className="font-mono break-all">{connection.last_error}</span>
        </Caveat>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <h4 className="text-xs font-black uppercase tracking-wider text-slate-500">
            Sync history
          </h4>
          {runs === null ? (
            <p className="text-xs text-slate-400">Loading…</p>
          ) : runs.length === 0 ? (
            <p className="text-xs text-slate-500">
              No sync has been recorded for this connection yet.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {runs.map((run) => (
                <li
                  key={run.sync_run_id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-xs"
                >
                  <span className="font-mono text-slate-500">{when(run.started_at)}</span>
                  <span className="font-bold text-slate-700">
                    {run.product_count} products · {run.offer_count} offers
                  </span>
                  <span
                    className={`font-bold uppercase ${
                      run.status === "success"
                        ? "text-emerald-600"
                        : run.status === "failed"
                          ? "text-rose-600"
                          : "text-amber-600"
                    }`}
                  >
                    {run.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {runError(runs)}
        </div>

        <div className="space-y-2">
          <h4 className="text-xs font-black uppercase tracking-wider text-slate-500">
            Order pushes
          </h4>
          {pushes === null ? (
            <p className="text-xs text-slate-400">Loading…</p>
          ) : pushes.length === 0 ? (
            <p className="text-xs text-slate-500">No order has been pushed to this store.</p>
          ) : (
            <ul className="space-y-1.5">
              {pushes.map((push) => (
                <li
                  key={push.push_id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 text-xs"
                >
                  <span className="font-mono text-slate-500">{push.order_id}</span>
                  <span className="truncate text-slate-500">
                    {push.remote_reference ?? "—"}
                  </span>
                  <span
                    className={`font-bold uppercase ${
                      push.status === "success"
                        ? "text-emerald-600"
                        : push.status === "duplicate"
                          ? "text-slate-500"
                          : "text-rose-600"
                    }`}
                  >
                    {push.status}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {error ? (
        <p className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-800">
          {error.message}
        </p>
      ) : null}
    </div>
  );
}

function runError(runs: SyncRun[] | null): React.ReactNode {
  const failed = (runs ?? []).filter((run) => run.status === "failed" && run.error_message);
  if (failed.length === 0) return null;
  return (
    <p className="text-[11px] text-rose-700">
      {failed.length} failed {failed.length === 1 ? "attempt" : "attempts"} recorded. The
      most recent reason is shown on the connection above.
    </p>
  );
}

export default function MerchantChannelsPage() {
  const [connections, setConnections] = useState<ChannelConnection[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    const result = await listConnections();
    if (!result.ok) {
      setError(result.error);
      setConnections([]);
      return;
    }
    setConnections(result.data.connections);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleDisconnect(connectionId: string) {
    const result = await disconnectStore(connectionId);
    if (result.ok) {
      await load();
    } else {
      setError(result.error);
    }
  }

  if (connections === null) {
    return <LoadingCard message="Reading stored channel connections…" />;
  }

  if (error && connections.length === 0) {
    return (
      <ErrorCard
        error={error}
        title="Could not read channel connections"
        credentialGap={error.code === "UNAUTHENTICATED" || error.code === "FORBIDDEN"}
        credentialGapNote="This screen needs a signed-in merchant session."
        onRetry={load}
      />
    );
  }

  const active = connections.filter((item) => item.status === "active");
  const disabled = connections.filter((item) => item.status !== "active");

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display text-2xl font-black tracking-tight text-slate-900">
              Store Channels
            </h1>
            <p className="text-sm text-slate-600">
              Connect your Shopify, WooCommerce, or custom store so AI agents can sell from
              your real catalog.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowForm((value) => !value)}
            className="inline-flex items-center gap-1.5 rounded-xl bg-[#174c3c] px-4 py-2.5 text-xs font-bold text-white shadow-sm hover:bg-[#103c2f]"
          >
            <Plug className="h-3.5 w-3.5" />
            {showForm ? "Close" : "Connect a store"}
          </button>
        </div>
        <SourceNote>
          Connection state from <code className="font-mono">GET /api/v1/channels/connections</code>
          ; sync history from <code className="font-mono">GET /api/v1/channels/sync-runs</code>;
          order pushes from{" "}
          <code className="font-mono">GET /api/v1/channels/order-pushes</code>. Product and
          offer counts are written only by a clean sync, so a failed run never overwrites a
          real count with a truncated one.
        </SourceNote>
      </header>

      {showForm ? <ConnectForm onDone={load} /> : null}

      {error && connections.length > 0 ? (
        <Caveat>{error.message}</Caveat>
      ) : null}

      {connections.length === 0 ? (
        <EmptyCard
          title="No store is connected"
          action={
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="mt-2 rounded-xl bg-[#174c3c] px-5 py-2.5 text-xs font-bold text-white shadow-sm hover:bg-[#103c2f]"
            >
              Connect your first store
            </button>
          }
        >
          AgentPay can pull products, variants, and inventory from a Shopify or WooCommerce
          store and push confirmed orders back. Nothing is connected to this tenant yet.
        </EmptyCard>
      ) : (
        <div className="space-y-4">
          {active.map((connection) => (
            <div key={connection.connection_id} className="space-y-2">
              <ConnectionDetail connection={connection} />
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => handleDisconnect(connection.connection_id)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-bold text-slate-600 hover:bg-slate-50"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Disconnect
                </button>
              </div>
            </div>
          ))}

          {disabled.length > 0 ? (
            <section className="space-y-2">
              <h2 className="text-xs font-black uppercase tracking-wider text-slate-500">
                Disconnected
              </h2>
              <ul className="space-y-1.5">
                {disabled.map((connection) => (
                  <li
                    key={connection.connection_id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs"
                  >
                    <span className="font-mono text-slate-600">
                      {connection.platform_type} · {connection.store_domain}
                    </span>
                    <span className="text-slate-500">
                      Disabled {when(connection.created_at)} · sync history kept
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      )}

      <Caveat>
        Disconnecting keeps the connection row, so its sync and order-push history survives
        for audit. Re-entering the same store domain restores it and clears the stored
        credential error.
      </Caveat>
    </div>
  );
}
