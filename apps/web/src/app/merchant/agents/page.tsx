"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchCapability, type CapabilityDocument } from "@/console/capability";
import {
  actorActivity,
  fetchAuditEvents,
  isCredentialGap,
  localTimestamp,
  SESSION_GAP_NOTE,
  type ActorActivity,
} from "@/console/audit";
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
import type { ApiError } from "@/lib/api";

/**
 * Connected agents, backed by the live capability document and the ledger.
 *
 * Two real sources, and one honest absence:
 *
 * * **The contract** is `GET /api/v1/capability`: the authentication method and
 *   token endpoint, the scopes a token may carry, the capability list, the
 *   endpoint map, and the limits every agent is bound by. This is exactly what an
 *   external agent reads before it decides what to spend, so it is what a merchant
 *   should be looking at on this screen.
 * * **Observed activity** is `GET /api/v1/audit/events`, grouped by
 *   `actor_type:actor_id`. This is who actually transacted, with counts of
 *   checkouts, confirmed orders and refusals per actor.
 * * **There is no agent registry endpoint.** `ApiClientRegistry` is installed
 *   in-memory by `install_auth` and, as its own comment says, holds nothing; no
 *   route lists API clients. So the previous page's three named agents with request
 *   and order counts, and its per-agent permission toggles, described nothing that
 *   exists. The permission matrix is now derived from the served scope list rather
 *   than asserted.
 */

type Phase = "loading" | "loaded" | "failed";

const LEDGER_WINDOW = 200;

/**
 * Write actions a merchant will ask about, checked against the served capability
 * list rather than declared here. `capabilities` is a closed enum in
 * `packages/schemas/v1.py`, and none of these are in it.
 */
const WRITE_ACTIONS = [
  { id: "offer_pricing", label: "Modify offer pricing", capability: "offer_price_write" },
  { id: "inventory", label: "Modify inventory counts", capability: "inventory_write" },
  { id: "refund", label: "Issue refunds", capability: "refund_write" },
  { id: "policy", label: "Change merchant policy", capability: "policy_write" },
] as const;

export default function MerchantAgentsPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [doc, setDoc] = useState<CapabilityDocument | null>(null);
  const [capabilityError, setCapabilityError] = useState<ApiError | null>(null);
  const [actors, setActors] = useState<ActorActivity[]>([]);
  const [ledgerError, setLedgerError] = useState<ApiError | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setPhase((current) => (current === "loaded" ? current : "loading"));
    setRefreshing(true);
    setCapabilityError(null);
    setLedgerError(null);

    const [capability, ledger] = await Promise.all([
      fetchCapability(),
      fetchAuditEvents({ limit: LEDGER_WINDOW }),
    ]);

    if (capability.ok) setDoc(capability.data);
    else {
      setDoc(null);
      setCapabilityError(capability.error);
    }

    if (ledger.ok) {
      const rows = actorActivity(Array.isArray(ledger.data?.events) ? ledger.data.events : []);
      setActors(rows);
      setSelected(rows.length > 0 ? `${rows[0].actorType}:${rows[0].actorId ?? ""}` : null);
    } else {
      setActors([]);
      setLedgerError(ledger.error);
    }

    setPhase(!capability.ok && !ledger.ok ? "failed" : "loaded");
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedActor =
    actors.filter((actor) => `${actor.actorType}:${actor.actorId ?? ""}` === selected)[0] ?? null;

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 text-[#174c3c] font-mono text-xs font-bold uppercase">
            <span>Merchant AI Governance</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">
            Agent Surface &amp; Observed Buyers
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">
            The contract this gateway publishes to autonomous buyers, and the actors that actually
            appear in your tenant&rsquo;s ledger.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 self-start md:self-auto">
          <button
            type="button"
            onClick={() => void load()}
            disabled={refreshing}
            className={consolePrimaryButton}
          >
            {refreshing ? "Reading\u2026" : "Refresh"}
          </button>
          <Link
            href="/agent/playground"
            className="px-4 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs rounded-xl transition-all"
          >
            Test in the playground &rarr;
          </Link>
        </div>
      </div>

      {phase === "loading" ? (
        <LoadingCard message="Reading the capability document and the ledger&hellip;" />
      ) : null}

      {phase === "failed" ? (
        ledgerError ? (
          <ErrorCard
            error={ledgerError}
            title="Neither the contract nor the ledger could be read"
            credentialGap={isCredentialGap(ledgerError)}
            credentialGapNote={SESSION_GAP_NOTE}
            onRetry={() => void load()}
          />
        ) : capabilityError ? (
          <ErrorCard
            error={capabilityError}
            title="The capability document could not be read"
            onRetry={() => void load()}
          />
        ) : null
      ) : null}

      {phase === "loaded" ? (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* ---- Observed actors ---- */}
          <div className="lg:col-span-5 bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-black text-slate-900 text-sm">
                Observed actors ({actors.length})
              </h3>
              <span className="text-[11px] font-mono text-slate-400">From the audit ledger</span>
            </div>

            {ledgerError ? (
              <ErrorCard
                error={ledgerError}
                title={
                  isCredentialGap(ledgerError)
                    ? "Observed activity needs a merchant session"
                    : "The ledger could not be read"
                }
                credentialGap={isCredentialGap(ledgerError)}
                credentialGapNote={SESSION_GAP_NOTE}
                onRetry={() => void load()}
              />
            ) : actors.length === 0 ? (
              <EmptyCard title="No actor has transacted yet">
                An actor appears here once it has an event in your tenant&rsquo;s ledger. Nothing is
                listed before that, because a list of agents that have never called is a list this
                gateway does not keep.
              </EmptyCard>
            ) : (
              <div className="space-y-2.5">
                {actors.map((actor) => {
                  const key = `${actor.actorType}:${actor.actorId ?? ""}`;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setSelected(key)}
                      className={`w-full text-left p-4 rounded-2xl border transition-all ${
                        selected === key
                          ? "border-[#174c3c] bg-[#174c3c]/5 ring-2 ring-[#174c3c]/20"
                          : "border-slate-200 hover:border-slate-300"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold text-slate-900 text-xs">
                          {actor.actorId ?? "(no actor id recorded)"}
                        </span>
                        <span className="text-[10px] font-bold px-2 py-0.5 bg-slate-100 text-slate-600 rounded-full">
                          {actor.actorType}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-600 font-medium mt-2 pt-2 border-t border-slate-100">
                        <span>
                          Events: <strong>{actor.events}</strong>
                        </span>
                        <span>
                          Checkouts: <strong>{actor.checkouts}</strong>
                        </span>
                        <span>
                          Orders: <strong>{actor.ordersConfirmed}</strong>
                        </span>
                        <span>
                          Refused: <strong>{actor.blocked}</strong>
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            <NotConnected
              label="Registered API clients"
              reason="ApiClientRegistry is installed in memory and holds nothing, and no endpoint lists API clients, so a roster of credentials issued to agents cannot be shown."
            />
          </div>

          {/* ---- The contract ---- */}
          <div className="lg:col-span-7 bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs space-y-6 text-xs">
            {doc ? (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
                  <div>
                    <h2 className="text-lg font-black text-slate-900">
                      Published agent contract
                    </h2>
                    <p className="text-slate-500 font-mono text-[11px]">
                      schema {doc.schema_version} &middot; policy {doc.policy.policy_version}
                    </p>
                  </div>
                  <span className="font-mono text-[#174c3c] font-bold bg-[#174c3c]/10 px-3 py-1 rounded-xl">
                    {doc.payment_provider}
                    {doc.test_mode ? " (test)" : ""}
                  </span>
                </div>

                {selectedActor ? (
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-1">
                    <span className="font-bold text-slate-900 block">
                      Selected actor: {selectedActor.actorId ?? "(none recorded)"}
                    </span>
                    <span className="text-slate-500 block">
                      {selectedActor.events} events, {selectedActor.agentRuns} correlated agent run
                      {selectedActor.agentRuns === 1 ? "" : "s"}, last seen{" "}
                      {localTimestamp(selectedActor.lastSeen) ?? selectedActor.lastSeen}.
                    </span>
                    <span className="text-slate-400 block text-[11px]">
                      The ledger records what an actor did, not which credential it held: an audit
                      row carries no client identifier, so an actor cannot be tied to an API key
                      here.
                    </span>
                  </div>
                ) : null}

                {/* Authentication */}
                <div className="space-y-2">
                  <h4 className="font-bold text-slate-900 uppercase text-[11px]">
                    How an agent authenticates
                  </h4>
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-1 font-mono">
                    <div>method: {doc.authentication.method}</div>
                    <div>token endpoint: {doc.authentication.token_endpoint}</div>
                    <div>scopes: {doc.authentication.scopes.join(", ")}</div>
                  </div>
                </div>

                {/* Capabilities */}
                <div className="space-y-2">
                  <h4 className="font-bold text-slate-900 uppercase text-[11px]">
                    Capabilities the document grants
                  </h4>
                  <div className="space-y-1.5 font-mono">
                    {doc.capabilities.map((capability) => (
                      <div
                        key={capability}
                        className="p-2.5 bg-slate-50 rounded-xl border border-slate-200 text-slate-700 flex items-center gap-2"
                      >
                        <span className="text-emerald-600 font-bold">+</span>
                        <span>{capability}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Endpoints */}
                <div className="space-y-2">
                  <h4 className="font-bold text-slate-900 uppercase text-[11px]">
                    Endpoints advertised
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 font-mono text-[11px]">
                    {(
                      [
                        ["search", doc.endpoints.search],
                        ["offers_query", doc.endpoints.offers_query],
                        ["checkout", doc.endpoints.checkout],
                        ["authorization", doc.endpoints.authorization],
                        ["payment", doc.endpoints.payment],
                        ["payment_status", doc.endpoints.payment_status],
                        ["order", doc.endpoints.order],
                      ] as [string, string][]
                    ).map(([name, path]) => (
                      <div
                        key={name}
                        className="p-2 bg-slate-50 rounded-lg border border-slate-200 flex items-center justify-between gap-2"
                      >
                        <span className="text-slate-500">{name}</span>
                        <span className="text-slate-800">{path}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Bounds */}
                <div className="space-y-2 pt-2 border-t border-slate-100">
                  <h4 className="font-bold text-slate-900 uppercase text-[11px]">
                    Bounds every agent is held to
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 flex items-center justify-between gap-2">
                      <span className="text-slate-600">Maximum transaction</span>
                      <Amount
                        minor={doc.limits.max_transaction_minor}
                        currency={doc.limits.currency}
                        className="font-black text-slate-900"
                      />
                    </div>
                    <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 flex items-center justify-between gap-2">
                      <span className="text-slate-600">Auto-approval limit</span>
                      <Amount
                        minor={doc.limits.auto_approval_limit_minor}
                        currency={doc.limits.currency}
                        className="font-black text-slate-900"
                      />
                    </div>
                    <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 flex items-center justify-between gap-2">
                      <span className="text-slate-600">Maximum units</span>
                      <span className="font-black text-slate-900">{doc.limits.max_quantity}</span>
                    </div>
                    <div className="p-3 bg-slate-50 rounded-xl border border-slate-200 flex items-center justify-between gap-2">
                      <span className="text-slate-600">Maximum results</span>
                      <span className="font-black text-slate-900">{doc.limits.max_results}</span>
                    </div>
                  </div>
                </div>

                {/* What the document does not grant */}
                <div className="space-y-2 pt-2 border-t border-slate-100">
                  <h4 className="font-bold text-slate-900 uppercase text-[11px]">
                    Not granted by the published contract
                  </h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 font-medium">
                    {WRITE_ACTIONS.map((action) => (
                      <div
                        key={action.id}
                        className="p-3 bg-rose-50/70 border border-rose-200 rounded-xl flex items-center justify-between gap-2 text-rose-950"
                      >
                        <span>{action.label}</span>
                        <span className="font-black text-rose-700">
                          {doc.capabilities.indexOf(action.capability) >= 0
                            ? "GRANTED"
                            : "ABSENT"}
                        </span>
                      </div>
                    ))}
                  </div>
                  <p className="text-[11px] text-slate-400 leading-relaxed">
                    Read from the served <span className="font-mono">capabilities</span> array: each
                    row is marked absent because that capability is not in the document, not because
                    this screen asserts a denial. The scope ceiling behind it is
                    `ROLE_SCOPES` in <span className="font-mono">packages/security/principals.py</span>
                    , where neither merchant role may hold `checkout:write` or `payment:write`.
                  </p>
                </div>

                <Caveat>
                  {doc.protocol_notice} External protocol certification:{" "}
                  <span className="font-mono">{doc.external_protocol_certification}</span>.
                </Caveat>
              </>
            ) : capabilityError ? (
              <ErrorCard
                error={capabilityError}
                title="The capability document could not be read"
                onRetry={() => void load()}
              />
            ) : null}

            <SourceNote>
              Contract from <span className="font-mono">GET /api/v1/capability</span>; observed
              actors grouped from{" "}
              <span className="font-mono">GET /api/v1/audit/events?limit={LEDGER_WINDOW}</span>,
              which the gateway scopes to your tenant.
            </SourceNote>
          </div>
        </div>
      ) : null}
    </div>
  );
}
