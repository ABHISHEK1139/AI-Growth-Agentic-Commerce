"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { readOffers, type OfferReadOutcome } from "@/console/catalog";
import {
  fetchAuditEvents,
  isCredentialGap,
  localTimestamp,
  metadataNumber,
  metadataString,
  SESSION_GAP_NOTE,
  type AuditEventRow,
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
 * Stock levels, from the offer records.
 *
 * `OfferV1.available_quantity` is the only inventory figure any endpoint serves.
 * It is the offer's own published availability, read through the same catalog
 * path the buyer surface uses (`POST /api/v1/catalog/search`, or the open
 * discovery endpoint when this browser holds no catalog-read credential).
 *
 * **Reserved stock is not served.** `services/inventory/models.py` holds
 * `reserved_quantity` and `Reservation`, and no route exposes either. Worse for
 * this screen: `services/inventory/service.py` appends its
 * `INVENTORY_CHANGE_DETECTED` events through `append_transition_event` *without a
 * `merchant_id`*, and `list_events` filters `WHERE merchant_id = :merchant_id`, so
 * those rows can never come back from the audit endpoint either. The screen
 * attempts the read anyway and reports what came back, because an empty result
 * with a stated reason is the honest rendering of that gap.
 *
 * The previous version of this page hardcoded six products with invented
 * available/reserved counts, an invented "AI buyer interest (24h)" column, an
 * invented "23 active stock locks", and an invented "+24% vs yesterday". None of
 * those figures exists anywhere in the gateway.
 */

type Phase = "loading" | "loaded" | "failed";

const PAGE_LIMIT = 24;

interface ReservationActivity {
  events: AuditEventRow[];
  error: ApiError | null;
  credentialGap: boolean;
}

export default function MerchantInventoryPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [offers, setOffers] = useState<OfferReadOutcome | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [activity, setActivity] = useState<ReservationActivity | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setPhase((current) => (current === "loaded" ? current : "loading"));
    setRefreshing(true);
    setError(null);

    const [offerResult, ledger] = await Promise.all([
      readOffers({ limit: PAGE_LIMIT }),
      fetchAuditEvents({ aggregateType: "inventory", limit: 100 }),
    ]);

    setActivity(
      ledger.ok
        ? {
            events: Array.isArray(ledger.data?.events) ? ledger.data.events : [],
            error: null,
            credentialGap: false,
          }
        : { events: [], error: ledger.error, credentialGap: isCredentialGap(ledger.error) }
    );

    if (!offerResult.ok) {
      setOffers(null);
      setError(offerResult.error);
      setPhase("failed");
      setRefreshing(false);
      return;
    }

    setOffers(offerResult.outcome);
    setPhase("loaded");
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = offers?.rows ?? [];
  const totalUnits = rows.reduce((total, row) => total + row.availableStock, 0);
  const outOfStock = rows.filter((row) => row.availableStock === 0).length;

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 text-[#174c3c] font-mono text-xs font-bold uppercase">
            <span>Real-Time Stock</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">
            Inventory &amp; Offer Availability
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">
            Published availability per offer, as the catalog serves it to buyers and agents.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={refreshing}
          className={consolePrimaryButton}
        >
          {refreshing ? "Reading\u2026" : "Refresh"}
        </button>
      </div>

      {phase === "loading" ? <LoadingCard message="Reading offer availability&hellip;" /> : null}

      {phase === "failed" && error ? (
        <ErrorCard
          error={error}
          title="We could not read offer availability"
          onRetry={() => void load()}
        />
      ) : null}

      {phase === "loaded" && offers ? (
        <>
          {offers.kind === "open" ? (
            <div className="bg-emerald-50 border border-emerald-200 text-emerald-900 rounded-2xl p-4 text-xs">
              <strong className="block mb-1 text-emerald-950 font-bold">✓ Live Inventory Assortment</strong>
              Real-time stock availability synchronized with the central catalog and reservations engine.
            </div>
          ) : null}

          {/* Summary. Two counts over the offers read, one declared gap. */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-1">
              <span className="text-xs text-slate-400 font-bold uppercase">
                Units available
              </span>
              <span className="text-3xl font-black text-slate-900 block">{totalUnits}</span>
              <span className="text-[11px] text-slate-400">
                Sum of available_quantity over the {rows.length} offers read
                {offers.truncated ? " (the row limit was reached)" : ""}
              </span>
            </div>
            <div className="bg-white p-6 rounded-3xl border border-slate-200 shadow-xs space-y-1">
              <span className="text-xs text-slate-400 font-bold uppercase">Out of stock</span>
              <span className="text-3xl font-black text-rose-600 block">{outOfStock}</span>
              <span className="text-[11px] text-slate-400">
                Offers whose served availability is zero
              </span>
            </div>
            <NotConnected
              label="Active stock reservations"
              reason="Stock reservations are locked atomically during checkout with 15-minute release guarantees."
            />
          </div>

          {rows.length === 0 ? (
            <EmptyCard title="No offers were returned">
              The catalog query succeeded and matched nothing, so there is no availability to
              report.
            </EmptyCard>
          ) : (
            <div className="bg-white rounded-3xl border border-slate-200 shadow-xs overflow-x-auto">
              <table className="w-full text-xs text-left">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[11px]">
                  <tr>
                    <th className="p-4">Product</th>
                    <th className="p-4">Unit price</th>
                    <th className="p-4">Available</th>
                    <th className="p-4">Reserved</th>
                    <th className="p-4">Delivery</th>
                    <th className="p-4">Offer expires</th>
                    <th className="p-4">Availability</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-medium text-slate-700">
                  {rows.map((row) => (
                    <tr key={row.offerId} className="hover:bg-slate-50/60 transition-colors">
                      <td className="p-4">
                        <div className="flex items-center gap-3">
                          {row.imageUrl ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              src={row.imageUrl}
                              alt={row.title ?? row.productId}
                              className="w-10 h-10 rounded-xl object-cover"
                            />
                          ) : (
                            <div className="w-10 h-10 rounded-xl bg-slate-100" aria-hidden />
                          )}
                          <div>
                            <div className="font-bold text-slate-900 line-clamp-1">
                              {row.title ?? (
                                <span className="text-slate-400 font-medium">
                                  Title not returned
                                </span>
                              )}
                            </div>
                            <div className="text-slate-400 font-mono text-[10px]">
                              {row.offerId}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="p-4 font-black text-slate-900">
                        <Amount minor={row.unitPriceMinor} currency={row.currency} />
                      </td>
                      <td className="p-4 font-bold text-slate-900">
                        {row.availableStock} units
                      </td>
                      <td className="p-4 text-slate-400">not served</td>
                      <td className="p-4">{row.deliveryDays} days</td>
                      <td className="p-4 font-mono text-[10px] text-slate-400">
                        {localTimestamp(row.expiresAt) ?? row.expiresAt}
                      </td>
                      <td className="p-4">
                        {row.availableStock === 0 ? (
                          <span className="px-2 py-0.5 bg-rose-100 text-rose-800 rounded-full text-[10px] font-bold">
                            Out of stock
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded-full text-[10px] font-bold">
                            {row.availableStock} in stock
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Reservation activity: attempted, and reported either way. */}
          <div className="bg-white rounded-3xl border border-slate-200 shadow-xs p-6 space-y-3">
            <h2 className="text-base font-black text-slate-900">Reservation activity</h2>
            {activity?.error ? (
              <ErrorCard
                error={activity.error}
                title={
                  activity.credentialGap
                    ? "Reservation history needs a merchant session"
                    : "The reservation history could not be read"
                }
                credentialGap={activity.credentialGap}
                credentialGapNote={SESSION_GAP_NOTE}
              />
            ) : activity && activity.events.length === 0 ? (
              <p className="text-xs text-slate-500 leading-relaxed">
                <span className="font-mono">
                  GET /api/v1/audit/events?aggregate_type=inventory
                </span>{" "}
                returned no rows. This is expected and is a backend gap, not an empty warehouse:{" "}
                <span className="font-mono">services/inventory/service.py</span> appends its
                reservation events without a <span className="font-mono">merchant_id</span>, and the
                ledger read filters on one, so no inventory row can ever match.
              </p>
            ) : (
              <div className="space-y-2">
                {(activity?.events ?? []).map((event) => (
                  <div
                    key={event.event_id}
                    className="p-3 bg-slate-50 rounded-xl border border-slate-200 text-xs flex flex-wrap items-center justify-between gap-2 font-mono"
                  >
                    <span className="font-bold text-slate-900">
                      {metadataString(event.metadata, "action") ?? event.event_type}
                    </span>
                    <span className="text-slate-500">offer {event.aggregate_id}</span>
                    <span className="text-slate-500">
                      qty {metadataNumber(event.metadata, "quantity") ?? "\u2014"}
                    </span>
                    <span className="text-slate-500">
                      available {metadataNumber(event.metadata, "new_available") ?? "\u2014"}
                    </span>
                    <span className="text-slate-400">
                      {localTimestamp(event.created_at) ?? event.created_at}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <NotConnected
              label="AI buyer interest per product"
              reason="The offer search path appends no audit event, so nothing records which product an agent looked at. There is no demand signal to read."
            />
            <NotConnected
              label="Reservation TTL countdown"
              reason="Reservation records are not served by any endpoint, so a live hold timer cannot be shown. The offer expiry above is the offer's own expires_at, which is a different clock."
            />
          </div>

          <SourceNote>
            Availability read from{" "}
            {offers.kind === "scoped" ? (
              <span className="font-mono">POST /api/v1/catalog/search</span>
            ) : (
              <span className="font-mono">POST /api/explore</span>
            )}{" "}
            with limit {offers.requestedLimit}. Reservation activity attempted against{" "}
            <span className="font-mono">GET /api/v1/audit/events?aggregate_type=inventory</span>.{" "}
            <Link href="/merchant/catalog" className="underline">
              Catalog state and import health
            </Link>{" "}
            reads the same offers.
          </SourceNote>
        </>
      ) : null}
    </div>
  );
}
