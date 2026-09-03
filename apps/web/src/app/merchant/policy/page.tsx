"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  fetchAllCapabilityRoutes,
  fetchMerchantRules,
  updateMerchantRules,
  policyRuleRows,
  servedLimitsFingerprint,
  type CapabilityDocument,
  type CapabilityReading,
  type MerchantRulesData,
} from "@/console/capability";
import {
  Amount,
  ErrorCard,
  LoadingCard,
  SourceNote,
  consolePrimaryButton,
} from "@/console/ui";
import type { ApiError } from "@/lib/api";

type Phase = "loading" | "loaded" | "failed";

function agreementOf(readings: CapabilityReading[]): {
  documents: { reading: CapabilityReading; fingerprint: string | null }[];
  agree: boolean;
  readable: number;
} {
  const documents = readings.map((reading) => ({
    reading,
    fingerprint: reading.result.ok ? servedLimitsFingerprint(reading.result.data) : null,
  }));
  const fingerprints = documents
    .map((entry) => entry.fingerprint)
    .filter((value): value is string => value !== null);
  const agree =
    fingerprints.length > 0 &&
    fingerprints.every((value) => value === fingerprints[0]) &&
    fingerprints.length === documents.length;
  return { documents, agree, readable: fingerprints.length };
}

export default function MerchantPolicyControlPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [doc, setDoc] = useState<CapabilityDocument | null>(null);
  const [readings, setReadings] = useState<CapabilityReading[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [readAt, setReadAt] = useState<string | null>(null);
  const [rereading, setRereading] = useState(false);

  // Editable Policy Bounds Form State
  const [maxTxRupees, setMaxTxRupees] = useState<number>(100000);
  const [autoApproveRupees, setAutoApproveRupees] = useState<number>(5000);
  const [maxDiscountPct, setMaxDiscountPct] = useState<number>(5);
  const [allowedCats, setAllowedCats] = useState<string>("laptops, smartphones, audio, accessories");
  const [blockedCats, setBlockedCats] = useState<string>("");
  const [allowOutOfStock, setAllowOutOfStock] = useState<boolean>(false);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setPhase((current) => (current === "loaded" ? current : "loading"));
    setRereading(true);
    setError(null);

    const [all, rulesRes] = await Promise.all([
      fetchAllCapabilityRoutes(),
      fetchMerchantRules(),
    ]);
    setReadings(all);

    if (rulesRes.ok && rulesRes.data?.rules) {
      const r = rulesRes.data.rules;
      setMaxTxRupees(Math.round(r.max_transaction_minor / 100));
      setAutoApproveRupees(Math.round(r.auto_approval_limit_minor / 100));
      setMaxDiscountPct(Number((r.max_discount_basis_points / 100).toFixed(1)));
      setAllowedCats(r.allowed_categories.join(", "));
      setBlockedCats(r.blocked_categories.join(", "));
      setAllowOutOfStock(r.allow_out_of_stock);
    }

    const primary = all.filter((reading) => reading.path === "/api/v1/capability")[0];
    const firstReadable = all.filter((reading) => reading.result.ok)[0];
    const usable = primary?.result.ok ? primary : firstReadable;

    if (!usable || !usable.result.ok) {
      setError(primary && !primary.result.ok ? primary.result.error : null);
      setDoc(null);
      setPhase("failed");
      setRereading(false);
      return;
    }

    setDoc(usable.result.data);
    setReadAt(
      usable.result.serverDateMs !== null
        ? new Date(usable.result.serverDateMs).toLocaleString("en-IN", {
            dateStyle: "medium",
            timeStyle: "medium",
          })
        : null
    );
    setPhase("loaded");
    setRereading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSavePolicy = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveSuccess(null);
    setSaveError(null);

    const allowed = allowedCats
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
    const blocked = blockedCats
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);

    const res = await updateMerchantRules({
      max_transaction_minor: Math.round(maxTxRupees * 100),
      auto_approval_limit_minor: Math.round(autoApproveRupees * 100),
      max_discount_basis_points: Math.round(maxDiscountPct * 100),
      allowed_categories: allowed,
      blocked_categories: blocked,
      allow_out_of_stock: allowOutOfStock,
    });

    setSaving(false);
    if (res.ok) {
      setSaveSuccess("Policy Rules successfully saved to datastore and synchronized across all surfaces.");
      await load();
    } else {
      setSaveError(res.error.message || "Failed saving policy rules.");
    }
  };

  const rules = doc ? policyRuleRows(doc) : [];
  const agreement = agreementOf(readings);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-black text-slate-900">AI Financial Policy Controls</h1>
        <p className="text-sm text-slate-500">
          The deterministic bounds this gateway applies to autonomous AI buyers, read from the
          capability document it serves to them.
        </p>
      </div>

      {phase === "loading" ? (
        <LoadingCard message="Reading the served capability document&hellip;" />
      ) : null}

      {phase === "failed" ? (
        error ? (
          <ErrorCard
            error={error}
            title="We could not read the served capability document"
            onRetry={() => void load()}
          />
        ) : (
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4 text-center">
            <h2 className="text-lg font-black text-slate-900">
              No capability route answered
            </h2>
            <p className="text-xs text-slate-500">
              All three discovery routes failed to return a document.
            </p>
            <button type="button" onClick={() => void load()} className={consolePrimaryButton}>
              Try again
            </button>
          </div>
        )
      ) : null}

      {phase === "loaded" && doc ? (
        <>
          {/* ---- Interactive Policy Rules Configuration Form ---- */}
          <form
            onSubmit={handleSavePolicy}
            className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
              <div>
                <h2 className="text-base font-black text-slate-900">
                  Configure Merchant AI Policy Bounds
                </h2>
                <p className="text-xs text-slate-500">
                  Directly configure and persist deterministic limits applied to autonomous AI buyers and checkout transactions.
                </p>
              </div>
              <button
                type="submit"
                disabled={saving}
                className={consolePrimaryButton}
              >
                {saving ? "Saving Policy…" : "Save Policy Rules"}
              </button>
            </div>

            {saveSuccess ? (
              <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-xs font-bold text-emerald-800">
                {saveSuccess}
              </div>
            ) : null}

            {saveError ? (
              <div className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-xs font-bold text-rose-700">
                {saveError}
              </div>
            ) : null}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  Maximum Transaction Limit (₹)
                </label>
                <input
                  type="number"
                  min="0"
                  step="100"
                  value={maxTxRupees}
                  onChange={(e) => setMaxTxRupees(Number(e.target.value))}
                  className="w-full p-3 rounded-xl border border-slate-200 bg-white font-bold text-slate-900 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  required
                />
                <span className="text-xs text-slate-400">
                  Transactions above this amount are hard-blocked ({maxTxRupees * 100} minor units).
                </span>
              </div>

              <div className="space-y-2">
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  Auto-Approval Threshold (₹)
                </label>
                <input
                  type="number"
                  min="0"
                  step="50"
                  value={autoApproveRupees}
                  onChange={(e) => setAutoApproveRupees(Number(e.target.value))}
                  className="w-full p-3 rounded-xl border border-slate-200 bg-white font-bold text-slate-900 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  required
                />
                <span className="text-xs text-slate-400">
                  Autonomous orders above this amount require human authorization ({autoApproveRupees * 100} minor units).
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  Maximum Allowable Discount (%)
                </label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.5"
                  value={maxDiscountPct}
                  onChange={(e) => setMaxDiscountPct(Number(e.target.value))}
                  className="w-full p-3 rounded-xl border border-slate-200 bg-white font-bold text-slate-900 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  required
                />
                <span className="text-xs text-slate-400">
                  Discount cap enforced by the policy engine ({Math.round(maxDiscountPct * 100)} basis points).
                </span>
              </div>

              <div className="space-y-2">
                <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                  Inventory Allocation Policy
                </label>
                <div className="pt-3">
                  <label className="flex items-center gap-2 text-xs font-bold text-slate-800 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={allowOutOfStock}
                      onChange={(e) => setAllowOutOfStock(e.target.checked)}
                      className="w-4 h-4 text-emerald-600 rounded border-slate-300 focus:ring-emerald-500"
                    />
                    Allow backorders / out-of-stock checkouts
                  </label>
                  <span className="block text-xs text-slate-400 mt-1">
                    When unchecked, checkout creation is refused if stock is insufficient.
                  </span>
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                Allowed Product Categories
              </label>
              <input
                type="text"
                value={allowedCats}
                onChange={(e) => setAllowedCats(e.target.value)}
                placeholder="laptops, smartphones, audio, accessories"
                className="w-full p-3 rounded-xl border border-slate-200 bg-white text-slate-900 font-medium focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
              <span className="text-xs text-slate-400">
                Comma-separated category allow-list. Leave empty to allow all catalog categories.
              </span>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                Blocked Categories
              </label>
              <input
                type="text"
                value={blockedCats}
                onChange={(e) => setBlockedCats(e.target.value)}
                placeholder="gift_cards, prohibited_goods"
                className="w-full p-3 rounded-xl border border-slate-200 bg-white text-slate-900 font-medium focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
              <span className="text-xs text-slate-400">
                Comma-separated category deny-list for autonomous checkout restriction.
              </span>
            </div>

            <div className="flex flex-wrap justify-between items-center gap-3 pt-4 border-t border-slate-100">
              <span className="text-xs text-slate-400 font-mono">
                Policy Version: {doc.policy.policy_version}
              </span>
              <button
                type="submit"
                disabled={saving}
                className={consolePrimaryButton}
              >
                {saving ? "Saving Policy…" : "Save Policy Rules"}
              </button>
            </div>
          </form>

          {/* ---- Agreement across the three served routes ---- */}
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-black text-slate-900">
                  What an external agent is told
                </h2>
                <p className="text-xs text-slate-500">
                  The same document is served on three routes. A bound that moved in one and not
                  the others would mean an agent planning against limits you did not publish.
                </p>
              </div>
              <button
                type="button"
                onClick={() => void load()}
                disabled={rereading}
                className={consolePrimaryButton}
              >
                {rereading ? "Re-reading\u2026" : "Re-read served document"}
              </button>
            </div>

            <div
              className={`p-3 rounded-xl text-xs font-bold ${
                agreement.agree
                  ? "bg-emerald-50 border border-emerald-200 text-emerald-800"
                  : "bg-rose-50 border border-rose-200 text-rose-700"
              }`}
            >
              {agreement.agree
                ? `All ${agreement.readable} discovery routes serve identical limits.`
                : `The routes do not agree, or one could not be read (${agreement.readable} of ${agreement.documents.length} readable).`}
            </div>

            <div className="space-y-2">
              {agreement.documents.map((entry) => (
                <div
                  key={entry.reading.path}
                  className="p-3 bg-slate-50 rounded-xl border border-slate-200 text-xs space-y-1"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-bold text-slate-900">{entry.reading.label}</span>
                    <span className="font-mono text-slate-500">{entry.reading.path}</span>
                  </div>
                  {entry.fingerprint ? (
                    <p className="font-mono text-[11px] text-slate-500 break-words">
                      {entry.fingerprint}
                    </p>
                  ) : (
                    <p className="text-[11px] text-rose-600 font-semibold">
                      Not readable:{" "}
                      {entry.reading.result.ok ? "unknown" : entry.reading.result.error.code}
                    </p>
                  )}
                </div>
              ))}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
                <span className="text-slate-500 block">Payment provider</span>
                <span className="font-mono font-bold text-slate-900">
                  {doc.payment_provider}
                  {doc.test_mode ? " (test mode)" : ""}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded-xl border border-slate-200">
                <span className="text-slate-500 block">Document read at (server clock)</span>
                <span className="font-mono font-bold text-slate-900">
                  {readAt ?? "not reported"}
                </span>
              </div>
            </div>

            <SourceNote>
              Read from <span className="font-mono">GET /api/v1/capability</span>,{" "}
              <span className="font-mono">GET /api/v1/agent/capability</span> and{" "}
              <span className="font-mono">GET /.well-known/agent-capability.json</span>. All three
              call the same builder, so agreement here is a check that the tenant resolution and
              the datastore read behaved identically for each, not a proof that three independent
              sources match.{" "}
              <Link href="/merchant/policies" className="underline">
                The rule table view
              </Link>{" "}
              reads the same document.
            </SourceNote>
          </div>

          {/* ---- The full rule projection, for completeness ---- */}
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
            <h2 className="text-base font-black text-slate-900">Every served bound</h2>
            <div className="space-y-2">
              {rules.map((rule) => (
                <div
                  key={rule.id}
                  className="p-3 bg-slate-50 rounded-xl border border-slate-200 text-xs flex flex-wrap items-center justify-between gap-2"
                >
                  <div className="space-y-0.5">
                    <span className="font-bold text-slate-900 block">{rule.name}</span>
                    <span className="font-mono text-[10px] text-slate-400">{rule.source}</span>
                  </div>
                  {rule.connected ? (
                    rule.amountMinor !== null && rule.currency !== null ? (
                      <Amount
                        minor={rule.amountMinor}
                        currency={rule.currency}
                        className="font-mono font-bold text-[#174c3c]"
                      />
                    ) : (
                      <span className="font-mono font-bold text-[#174c3c] text-right">
                        {rule.value}
                      </span>
                    )
                  ) : (
                    <span className="font-mono font-bold text-slate-400">Not yet connected</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
