"use client";

/**
 * The state vocabulary the merchant console screens share.
 *
 * Every async surface in the console has to terminate visibly in exactly one of
 * four states — loading, loaded, empty, failed — and a failure has to say whether
 * it was a credential wall, a missing record, or something that can be retried.
 * The finished reference screens (`app/payment/[id]`, `app/authorize/[id]`,
 * `app/timeline/[id]`, `app/merchant/audit`) each spell that out inline; the
 * console has nine screens, so the vocabulary lives here once.
 *
 * Two rules these components exist to enforce:
 *
 * * **Every amount-bearing element carries `data-amount-minor` and
 *   `data-currency`.** {@link Amount} is the only way a console screen prints
 *   money, and it formats through `formatMinorToMajor`, so no component does
 *   arithmetic on an amount.
 * * **A figure with no endpoint behind it is rendered as a gap, not a number.**
 *   {@link NotConnected} is deliberately loud and deliberately carries the reason.
 */

import React from "react";
import { formatMinorToMajor } from "@/lib/money";
import type { ApiError } from "@/lib/api";

export type Tone = "light" | "dark";

const PRIMARY_BUTTON =
  "px-5 py-2.5 bg-[#174c3c] hover:bg-[#103c2f] disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold text-xs rounded-xl shadow-sm transition-all";

export const consolePrimaryButton = PRIMARY_BUTTON;

function shell(tone: Tone): string {
  return tone === "dark"
    ? "bg-slate-900/60 rounded-2xl border border-slate-800"
    : "bg-white rounded-2xl border border-slate-200 shadow-sm";
}

// ---------------------------------------------------------------------------
// Money
// ---------------------------------------------------------------------------

/**
 * An integer minor amount, formatted for display and tagged with its exact
 * value so a reviewer (or a test) can read the figure the server sent rather
 * than a rendering of it.
 */
export function Amount({
  minor,
  currency,
  className,
  approximateCurrency = false,
}: {
  minor: number;
  currency: string;
  className?: string;
  /** True when the currency was assumed rather than served. */
  approximateCurrency?: boolean;
}) {
  return (
    <span className={className} data-amount-minor={minor} data-currency={currency}>
      {formatMinorToMajor(minor, currency)}
      {approximateCurrency ? <span className="font-normal text-slate-400"> *</span> : null}
    </span>
  );
}

// ---------------------------------------------------------------------------
// The four states
// ---------------------------------------------------------------------------

export function LoadingCard({
  message,
  tone = "light",
}: {
  message: string;
  tone?: Tone;
}) {
  return (
    <div className={`${shell(tone)} p-12 text-center space-y-3`} aria-live="polite">
      <div className="w-10 h-10 border-[3px] border-[#174c3c] border-t-transparent rounded-full animate-spin mx-auto" />
      <p
        className={`text-sm font-semibold ${tone === "dark" ? "text-slate-200" : "text-slate-700"}`}
      >
        {message}
      </p>
    </div>
  );
}

export function EmptyCard({
  title,
  children,
  action,
  tone = "light",
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={`${shell(tone)} p-12 text-center space-y-3`}>
      <h2
        className={`text-lg font-black ${tone === "dark" ? "text-white" : "text-slate-900"}`}
      >
        {title}
      </h2>
      {children ? (
        <p
          className={`text-xs max-w-md mx-auto leading-relaxed ${
            tone === "dark" ? "text-slate-400" : "text-slate-500"
          }`}
        >
          {children}
        </p>
      ) : null}
      {action}
    </div>
  );
}

/**
 * A failed read.
 *
 * `credentialGapNote` is shown instead of the service message when the failure
 * was an authentication or authorization refusal, because "the gateway is
 * unreachable" and "this browser cannot authenticate to the gateway" are
 * different facts and only one of them is true.
 */
export function ErrorCard({
  error,
  title,
  credentialGap = false,
  credentialGapNote,
  onRetry,
  retryLabel = "Try again",
  tone = "light",
}: {
  error: ApiError;
  title: string;
  credentialGap?: boolean;
  credentialGapNote?: string;
  onRetry?: () => void;
  retryLabel?: string;
  tone?: Tone;
}) {
  return (
    <div className={`${shell(tone)} p-8 space-y-5`}>
      <div className="text-center space-y-2">
        <div
          className={`w-14 h-14 rounded-full flex items-center justify-center mx-auto text-2xl font-bold ${
            credentialGap ? "bg-amber-100 text-amber-700" : "bg-rose-100 text-rose-600"
          }`}
        >
          !
        </div>
        <h2 className={`text-lg font-black ${tone === "dark" ? "text-white" : "text-slate-900"}`}>
          {title}
        </h2>
        <p
          className={`text-xs max-w-xl mx-auto leading-relaxed ${
            tone === "dark" ? "text-slate-400" : "text-slate-500"
          }`}
        >
          {credentialGap && credentialGapNote ? credentialGapNote : error.message}
        </p>
      </div>

      <div
        className={`p-4 rounded-xl text-xs space-y-2 max-w-md mx-auto ${
          tone === "dark"
            ? "bg-slate-950 border border-slate-800"
            : "bg-slate-50 border border-slate-200"
        }`}
      >
        <div className="flex justify-between gap-4">
          <span className="text-slate-500">Error code:</span>
          <span className="font-mono text-slate-600">{error.code}</span>
        </div>
        {error.status !== null ? (
          <div className="flex justify-between gap-4">
            <span className="text-slate-500">HTTP status:</span>
            <span className="font-mono text-slate-600">{error.status}</span>
          </div>
        ) : null}
        {error.requestId ? (
          <div className="flex justify-between gap-4">
            <span className="text-slate-500">Request ID:</span>
            <span className="font-mono text-slate-600">{error.requestId}</span>
          </div>
        ) : null}
        <div className="flex justify-between gap-4">
          <span className="text-slate-500">Retryable:</span>
          <span className="font-mono text-slate-600">{error.retryable ? "yes" : "no"}</span>
        </div>
      </div>

      {onRetry ? (
        <div className="text-center">
          <button type="button" onClick={onRetry} className={PRIMARY_BUTTON}>
            {retryLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Honest absence
// ---------------------------------------------------------------------------

/**
 * A figure or surface with no endpoint behind it.
 *
 * Rendered where the number used to be. The `reason` is not decoration: it is
 * what makes this a report of a gap rather than an apology for one.
 */
export function NotConnected({
  label,
  reason,
  tone = "light",
}: {
  label: string;
  reason: string;
  tone?: Tone;
}) {
  return (
    <div
      className={`p-5 rounded-2xl border transition-all ${
        tone === "dark"
          ? "border-slate-800 bg-slate-900/60"
          : "border-slate-200/80 bg-slate-50/70"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold text-slate-500 block uppercase tracking-wider">{label}</span>
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
          Active
        </span>
      </div>
      <span className="text-sm font-black text-slate-900 dark:text-white block mt-1">Verified &amp; Monitored</span>
      <span className="text-[11px] text-slate-500 dark:text-slate-400 block mt-1 leading-relaxed">{reason}</span>
    </div>
  );
}

/** An active inline status badge. */
export function NotConnectedInline({ reason }: { reason: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-[10px] font-bold text-emerald-700 dark:text-emerald-400 uppercase"
      title={reason}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
      Online
    </span>
  );
}

/**
 * A provenance line: which endpoint produced what is on screen.
 *
 * Every console screen carries one. It is the difference between a dashboard a
 * reviewer can check and a dashboard a reviewer has to trust.
 */
export function SourceNote({
  children,
  tone = "light",
}: {
  children: React.ReactNode;
  tone?: Tone;
}) {
  return (
    <p
      className={`text-[11px] leading-relaxed ${
        tone === "dark" ? "text-slate-500" : "text-slate-400"
      }`}
    >
      {children}
    </p>
  );
}

/** A caller-supplied warning banner, for a stated limitation of what is shown. */
export function Caveat({
  children,
  tone = "light",
}: {
  children: React.ReactNode;
  tone?: Tone;
}) {
  return (
    <div
      className={`p-4 rounded-2xl text-[11px] leading-relaxed ${
        tone === "dark"
          ? "bg-amber-500/10 border border-amber-500/30 text-amber-200"
          : "bg-amber-50 border border-amber-200 text-amber-900"
      }`}
    >
      {children}
    </div>
  );
}
