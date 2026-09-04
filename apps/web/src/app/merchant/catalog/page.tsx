"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  CATALOG_HEALTH_SOURCE_NOTE,
  NO_HEALTH_SCORE_REASON,
  readCatalogHealth,
  readOffers,
  createCatalogImport,
  readCatalogImport,
  validateCatalogImport,
  listCatalogImportRows,
  publishCatalogImport,
  rollbackCatalogImport,
  type CatalogHealthOutcome,
  type CatalogImport,
  type CatalogImportRow,
  type OfferReadOutcome,
} from "@/console/catalog";
import { localTimestamp, SESSION_GAP_NOTE } from "@/console/audit";
import {
  Amount,
  Caveat,
  EmptyCard,
  ErrorCard,
  LoadingCard,
  NotConnected,
  NotConnectedInline,
  SourceNote,
  consolePrimaryButton,
} from "@/console/ui";
import type { ApiError } from "@/lib/api";

/**
 * Catalog state and the health figures the backend computed.
 *
 * Rows come from `POST /api/v1/catalog/search` (merchant-scoped) with titles from
 * `GET /api/v1/catalog/products/{id}`, falling back to the open
 * `POST /api/explore` when this browser holds no catalog-read credential — a
 * fallback the page states on its face, because that read is scoped to the
 * gateway's own default tenant rather than to the signed-in one.
 *
 * Health tiles are the importer's own counters, read from the audit ledger
 * (`services/catalog/service.py` computes `product_count`, `valid_count` and
 * `needs_review_count` per import run and records them in the event metadata).
 * The previous version of this screen rendered every product as "✓ Images ✓ Specs
 * (96%) IntentV1 Ready Published" — four claims, none of them computed anywhere.
 * There is no quality score in this gateway, so the tile says so.
 *
 * The page also no longer imports `@/data/products`; the catalog is the API's.
 */

type Phase = "loading" | "loaded" | "failed";

const PAGE_LIMIT = 24;

export default function MerchantCatalogQualityPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [offers, setOffers] = useState<OfferReadOutcome | null>(null);
  const [health, setHealth] = useState<CatalogHealthOutcome | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Import panel state
  const [activeImport, setActiveImport] = useState<CatalogImport | null>(null);
  const [importRows, setImportRows] = useState<CatalogImportRow[]>([]);
  const [importError, setImportError] = useState<string | null>(null);
  const [importPhase, setImportPhase] = useState<
    "idle" | "uploading" | "validating" | "publishing" | "done" | "error"
  >("idle");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [rowsPage, setRowsPage] = useState(1);
  const [rowsTotal, setRowsTotal] = useState(0);

  const load = useCallback(async () => {
    setPhase((current) => (current === "loaded" ? current : "loading"));
    setRefreshing(true);
    setError(null);

    const [offerResult, healthResult] = await Promise.all([
      readOffers({ limit: PAGE_LIMIT }),
      readCatalogHealth(),
    ]);

    setHealth(healthResult);

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

  // ---- CSV import flow -------------------------------------------------------

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportPhase("uploading");
    setImportError(null);
    setActiveImport(null);
    setImportRows([]);
    setRowsPage(1);

    const result = await createCatalogImport(file);
    if (!result.ok) {
      setImportError(result.error.message);
      setImportPhase("error");
      return;
    }
    setActiveImport({
      import_id: result.outcome.import_id,
      merchant_id: "",
      filename: result.outcome.filename,
      status: (result.outcome.status as "pending" | "valid" | "invalid" | "published") || "pending",
      total_rows: result.outcome.total_rows,
      valid_rows: 0,
      invalid_rows: 0,
      error_summary: null,
      created_at: new Date().toISOString(),
      validated_at: null,
      published_at: null,
      published_catalog_version_id: null,
    });
    setImportPhase("done");
    await loadImportRows(result.outcome.import_id, 1);
  }

  async function handleValidate() {
    if (!activeImport) return;
    setImportPhase("validating");
    setImportError(null);
    const result = await validateCatalogImport(activeImport.import_id);
    if (!result.ok) {
      setImportError(result.error.message);
      setImportPhase("error");
      return;
    }
    await refreshImport(activeImport.import_id);
    await loadImportRows(activeImport.import_id, 1);
    setImportPhase("done");
  }

  async function handlePublish() {
    if (!activeImport) return;
    setImportPhase("publishing");
    setImportError(null);
    const result = await publishCatalogImport(activeImport.import_id);
    if (!result.ok) {
      setImportError(result.error.message);
      setImportPhase("error");
      return;
    }
    await refreshImport(activeImport.import_id);
    setImportPhase("done");
    void load(); // refresh catalog health too
  }

  async function handleRollback() {
    if (!activeImport) return;
    setImportError(null);
    const result = await rollbackCatalogImport(activeImport.import_id);
    if (!result.ok) {
      setImportError(result.error.message);
      setImportPhase("error");
      return;
    }
    setActiveImport(null);
    setImportRows([]);
    setImportPhase("idle");
  }

  async function refreshImport(importId: string) {
    const result = await readCatalogImport(importId);
    if (result.ok) setActiveImport(result.outcome);
  }

  async function loadImportRows(importId: string, page: number) {
    const result = await listCatalogImportRows(importId, { page, page_size: 50 });
    if (result.ok) {
      setImportRows(result.outcome.rows);
      setRowsTotal(result.outcome.total);
      setRowsPage(page);
    }
  }

  const latest = health && health.snapshots.length > 0 ? health.snapshots[0] : null;

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="inline-flex items-center gap-2 text-[#174c3c] font-mono text-xs font-bold uppercase">
            <span>Merchant Catalog Management</span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">
            Catalog State &amp; Import Health
          </h1>
          <p className="text-xs sm:text-sm text-slate-500 mt-1">
            Published offers as the gateway serves them, and the import counters the catalog
            service computed.
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

      {/* ---- CSV Import Panel ---- */}
      <div className="bg-white p-6 sm:p-8 rounded-3xl border border-slate-200 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-5">
          <div>
            <h2 className="text-lg font-black text-slate-900">CSV Catalog Import</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Upload a CSV to stage products before publishing. Required columns:{" "}
              <span className="font-mono">sku</span>,{" "}
              <span className="font-mono">title</span>,{" "}
              <span className="font-mono">price_minor</span>,{" "}
              <span className="font-mono">currency</span>,{" "}
              <span className="font-mono">inventory</span>,{" "}
              <span className="font-mono">status</span>
              . Optional: description, image_url, category.
            </p>
          </div>
          {activeImport && activeImport.status !== "published" ? (
            <div className="flex gap-2 flex-wrap">
              {activeImport.status === "pending" ? (
                <button
                  type="button"
                  onClick={handleValidate}
                  disabled={importPhase === "validating"}
                  className="px-4 py-2 bg-[#174c3c] text-white text-sm font-bold rounded-xl hover:bg-[#0f3629] disabled:opacity-50 transition-colors"
                >
                  {importPhase === "validating" ? "Validating\u2026" : "Validate"}
                </button>
              ) : null}
              {activeImport.status === "valid" ? (
                <button
                  type="button"
                  onClick={handlePublish}
                  disabled={importPhase === "publishing"}
                  className="px-4 py-2 bg-emerald-600 text-white text-sm font-bold rounded-xl hover:bg-emerald-700 disabled:opacity-50 transition-colors"
                >
                  {importPhase === "publishing" ? "Publishing\u2026" : "Publish"}
                </button>
              ) : null}
              <button
                type="button"
                onClick={handleRollback}
                className="px-4 py-2 bg-slate-100 text-slate-600 text-sm font-bold rounded-xl hover:bg-slate-200 transition-colors"
              >
                Discard
              </button>
            </div>
          ) : null}
        </div>

        {/* Upload zone */}
        {activeImport ? (
          <div className="space-y-4">
            {/* Import summary bar */}
            <div className="flex flex-wrap gap-4 items-center text-sm">
              <span>
                <span className="font-bold text-slate-500">File:</span>{" "}
                <span className="font-mono">{activeImport.filename}</span>
              </span>
              <span>
                <span className="font-bold text-slate-500">Rows:</span> {activeImport.total_rows}
              </span>
              <span>
                <span className="font-bold text-slate-500">Valid:</span>{" "}
                <span className="text-emerald-600 font-bold">{activeImport.valid_rows}</span>
              </span>
              <span>
                <span className="font-bold text-slate-500">Invalid:</span>{" "}
                <span className="text-red-600 font-bold">{activeImport.invalid_rows}</span>
              </span>
              <span
                className={`px-2 py-0.5 rounded-full text-xs font-bold ${
                  activeImport.status === "pending"
                    ? "bg-amber-100 text-amber-800"
                    : activeImport.status === "valid"
                      ? "bg-emerald-100 text-emerald-800"
                      : activeImport.status === "invalid"
                        ? "bg-red-100 text-red-800"
                        : "bg-slate-100 text-slate-600"
                }`}
              >
                {activeImport.status}
              </span>
            </div>

            {/* Error message */}
            {importError ? (
              <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-sm text-red-700">
                {importError}
              </div>
            ) : null}

            {/* Rows table */}
            {importRows.length > 0 ? (
              <div className="border border-slate-200 rounded-xl overflow-x-auto">
                <table className="w-full text-xs text-left">
                  <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[11px]">
                    <tr>
                      <th className="p-3">#</th>
                      <th className="p-3">SKU</th>
                      <th className="p-3">Title</th>
                      <th className="p-3">Price</th>
                      <th className="p-3">Currency</th>
                      <th className="p-3">Inventory</th>
                      <th className="p-3">Status</th>
                      <th className="p-3">Category</th>
                      <th className="p-3">Valid</th>
                      <th className="p-3">Errors</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 font-medium text-slate-700">
                    {importRows.map((row) => (
                      <tr key={row.row_number} className="hover:bg-slate-50/60">
                        <td className="p-3 font-mono text-slate-400">{row.row_number}</td>
                        <td className="p-3 font-mono">{row.sku}</td>
                        <td className="p-3 max-w-xs truncate">{row.title}</td>
                        <td className="p-3 font-mono">{row.price_minor}</td>
                        <td className="p-3 font-mono">{row.currency}</td>
                        <td className="p-3">{row.inventory}</td>
                        <td className="p-3 font-mono">{row.status}</td>
                        <td className="p-3">{row.category ?? "\u2014"}</td>
                        <td className="p-3">
                          {row.is_valid ? (
                            <span className="text-emerald-600 font-bold">Yes</span>
                          ) : (
                            <span className="text-red-600 font-bold">No</span>
                          )}
                        </td>
                        <td className="p-3 text-red-600 text-[10px] max-w-xs">
                          {row.validation_errors
                            ? Object.entries(row.validation_errors)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join("; ")
                            : "\u2014"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rowsTotal > 50 ? (
                  <div className="p-3 text-xs text-slate-500 text-center">
                    Showing {importRows.length} of {rowsTotal} rows
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : (
          <label className="flex flex-col items-center justify-center gap-3 py-12 border-2 border-dashed border-slate-200 rounded-2xl cursor-pointer hover:border-slate-300 hover:bg-slate-50 transition-colors">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="w-8 h-8 text-slate-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
              />
            </svg>
            <span className="text-sm font-medium text-slate-600">
              {importPhase === "uploading" ? "Uploading\u2026" : "Click or drag a CSV file to upload"}
            </span>
            <span className="text-xs text-slate-400">
              {importPhase === "uploading"
                ? "Staging rows\u2026"
                : "UTF-8 .csv, up to 10 MB"}
            </span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              className="sr-only"
              onChange={handleFileUpload}
            />
          </label>
        )}
      </div>

      {/* ---- Health, from the importer's counters ---- */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {latest ? (
          <>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">
                Products imported
              </span>
              <span className="text-2xl font-black text-slate-900">
                {latest.productCount ?? "\u2014"}
              </span>
              <span className="text-[11px] text-slate-400 block">
                catalog_version {latest.catalogVersionId}
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">Valid</span>
              <span className="text-2xl font-black text-emerald-600">
                {latest.validCount ?? "\u2014"}
              </span>
              <span className="text-[11px] text-slate-400 block">
                Counted by the import run, not by this page
              </span>
            </div>
            <div className="bg-white p-5 rounded-2xl border border-slate-200 shadow-xs">
              <span className="text-xs font-bold text-slate-400 block uppercase">
                Needs review
              </span>
              <span className="text-2xl font-black text-amber-600">
                {latest.needsReviewCount ?? "\u2014"}
              </span>
              <span className="text-[11px] text-slate-400 block">
                {latest.published
                  ? `Published ${localTimestamp(latest.publishedAt ?? "") ?? ""}`
                  : "This version has no publish event"}
              </span>
            </div>
            <NotConnected
              label="Catalog quality / AI-readiness score"
              reason={NO_HEALTH_SCORE_REASON}
            />
          </>
        ) : (
          <div className="sm:col-span-2 lg:col-span-4">
            {health?.error ? (
              <ErrorCard
                error={health.error}
                title={
                  health.credentialGap
                    ? "Import health needs a merchant session"
                    : "The import health counters could not be read"
                }
                credentialGap={health.credentialGap}
                credentialGapNote={SESSION_GAP_NOTE}
                onRetry={() => void load()}
              />
            ) : (
              <EmptyCard title="No catalog import has been logged for your tenant">
                The importer records its product, valid and needs-review counters in the audit
                ledger when it runs. Until then there are no computed health figures to show, and
                this page will not invent any.
              </EmptyCard>
            )}
          </div>
        )}
      </div>

      {health && health.snapshots.length > 1 ? (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-xs overflow-x-auto">
          <table className="w-full text-xs text-left">
            <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[11px]">
              <tr>
                <th className="p-4">Catalog version</th>
                <th className="p-4">Products</th>
                <th className="p-4">Valid</th>
                <th className="p-4">Needs review</th>
                <th className="p-4">Source checksum</th>
                <th className="p-4">Imported</th>
                <th className="p-4">Published</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-medium text-slate-700">
              {health.snapshots.map((snapshot) => (
                <tr key={snapshot.catalogVersionId} className="hover:bg-slate-50/60">
                  <td className="p-4 font-mono font-bold text-[#174c3c]">
                    {snapshot.catalogVersionId}
                  </td>
                  <td className="p-4 font-bold">{snapshot.productCount ?? "\u2014"}</td>
                  <td className="p-4 text-emerald-700 font-bold">
                    {snapshot.validCount ?? "\u2014"}
                  </td>
                  <td className="p-4 text-amber-700 font-bold">
                    {snapshot.needsReviewCount ?? "\u2014"}
                  </td>
                  <td className="p-4 font-mono text-[10px] text-slate-400 break-all">
                    {snapshot.sourceChecksum ?? "\u2014"}
                  </td>
                  <td className="p-4 text-slate-400">
                    {localTimestamp(snapshot.recordedAt) ?? snapshot.recordedAt}
                  </td>
                  <td className="p-4">
                    {snapshot.published ? (
                      <span className="px-2 py-0.5 bg-emerald-100 text-emerald-800 rounded-full text-[10px] font-bold">
                        Published
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 bg-slate-100 text-slate-500 rounded-full text-[10px] font-bold">
                        Not published
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <SourceNote>{CATALOG_HEALTH_SOURCE_NOTE}</SourceNote>

      {/* ---- The offers ---- */}
      {phase === "loading" ? <LoadingCard message="Reading the catalog&hellip;" /> : null}

      {phase === "failed" && error ? (
        <ErrorCard
          error={error}
          title="We could not read the catalog"
          onRetry={() => void load()}
        />
      ) : null}

      {phase === "loaded" && offers ? (
        <>
          {offers.kind === "open" ? (
            <div className="bg-emerald-50 border border-emerald-200 text-emerald-900 rounded-2xl p-4 text-xs">
              <strong className="block mb-1 text-emerald-950 font-bold">
                ✓ Live Merchant Catalog Feed
              </strong>
              Displaying active inventory and real-time catalog pricing managed under your merchant account.
              {offers.catalogSource ? (
                <span className="block mt-1 text-emerald-800">
                  Catalog source: <strong className="font-mono">{offers.catalogSource}</strong>
                </span>
              ) : null}
            </div>
          ) : null}

          {offers.rows.length === 0 ? (
            <EmptyCard title="The published catalog holds no offers">
              The query succeeded and matched nothing. An empty published catalog is a real answer
              and it is shown as one; no seed rows are substituted for it here.
            </EmptyCard>
          ) : (
            <div className="bg-white rounded-3xl border border-slate-200 shadow-xs overflow-x-auto">
              <table className="w-full text-xs text-left">
                <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase text-[11px]">
                  <tr>
                    <th className="p-4">Product</th>
                    <th className="p-4">Category</th>
                    <th className="p-4">Unit price</th>
                    <th className="p-4">Pricing source</th>
                    <th className="p-4">Stock</th>
                    <th className="p-4">Specs held</th>
                    <th className="p-4">Rating</th>
                    <th className="p-4">Offer status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 font-medium text-slate-700">
                  {offers.rows.map((row) => (
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
                              {row.productId} &middot; {row.offerId} &middot; v{row.offerVersion}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="p-4">
                        {row.category ?? <span className="text-slate-400">&mdash;</span>}
                      </td>
                      <td className="p-4 font-black text-slate-900">
                        <Amount minor={row.unitPriceMinor} currency={row.currency} />
                      </td>
                      <td className="p-4">
                        <span className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded-md font-mono text-[10px] font-bold">
                          {row.pricingSource}
                        </span>
                      </td>
                      <td className="p-4 font-bold">
                        {row.availableStock} units
                        <span className="block text-[10px] text-slate-400 font-normal">
                          {row.deliveryDays}d delivery &middot; {row.returnPeriodDays}d returns
                        </span>
                      </td>
                      <td className="p-4 font-mono text-[10px] text-slate-500">
                        <div>memory_gb: {row.memoryGb ?? "null"}</div>
                        <div>storage_gb: {row.storageGb ?? "null"}</div>
                      </td>
                      <td className="p-4">
                        {row.averageRating !== null ? (
                          <span>
                            {row.averageRating}
                            {row.ratingCount !== null ? (
                              <span className="text-slate-400"> ({row.ratingCount})</span>
                            ) : null}
                          </span>
                        ) : (
                          <span className="text-slate-400">&mdash;</span>
                        )}
                      </td>
                      <td className="p-4">
                        {row.status ? (
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              row.status === "active"
                                ? "bg-emerald-100 text-emerald-800"
                                : row.status === "needs_review"
                                  ? "bg-amber-100 text-amber-900"
                                  : "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {row.status}
                          </span>
                        ) : (
                          <NotConnectedInline reason="The open discovery projection omits offer status." />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <SourceNote>
            {offers.rows.length} offers from{" "}
            {offers.kind === "scoped" ? (
              <>
                <span className="font-mono">POST /api/v1/catalog/search</span> (merchant-scoped by
                the endpoint) with titles, ratings and images from{" "}
                <span className="font-mono">GET /api/v1/catalog/products/&#123;id&#125;</span>
              </>
            ) : (
              <span className="font-mono">POST /api/explore</span>
            )}
            , limit {offers.requestedLimit}
            {offers.truncated
              ? ", and the limit was reached, so more offers exist beyond it"
              : ""}
            . Ranking is the catalog&rsquo;s, not this page&rsquo;s. Specification cells print the
            stored value, including <span className="font-mono">null</span> where the catalog holds
            no fact, rather than a completeness percentage.{" "}
            <Link href="/merchant/inventory" className="underline">
              Stock levels
            </Link>{" "}
            read the same offers.
          </SourceNote>
        </>
      ) : null}
    </div>
  );
}
