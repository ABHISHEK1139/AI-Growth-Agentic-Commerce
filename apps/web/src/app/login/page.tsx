"use client";

import React, { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, KeyRound, LogIn, Store } from "lucide-react";

import { loginWithPassword } from "@/lib/api";

/**
 * The sign-in page.
 *
 * It did not exist before this change. The console was reachable at `/merchant`
 * with no credential at all, because `POST /api/v1/auth/session` took a role in
 * the request body and issued a session for it. This page posts an email and a
 * password to `POST /api/v1/auth/login`, which verifies an Argon2id hash and
 * derives the role from the stored row.
 *
 * Two things it deliberately does not do:
 *
 * * **No "forgot password" link.** The deployment has no mail transport, so the
 *   link would be a dead end. The reset path is
 *   `python -m apps.worker.reset_operator_password`, and saying so is more
 *   honest than a link that does nothing.
 * * **No signup form unless the backend says signup is on.** Self-service
 *   registration is off by default, and a form that renders regardless would
 *   post to a 403 and read as a broken product.
 */

interface ConsoleStatus {
  password_login_enabled: boolean;
  demo_session_enabled: boolean;
  signup_enabled: boolean;
  password_min_length: number;
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  /**
   * `next` is attacker-controllable: it arrives in the query string and
   * `middleware.ts` writes it. Only same-site *paths* are honoured -- never an
   * absolute URL, and never a protocol-relative `//evil.example`, which would
   * turn the sign-in page into an open redirect that a victim completes
   * successfully before being bounced.
   */
  const next = useMemo(() => {
    const raw = searchParams.get("next");
    if (
      !raw ||
      !raw.startsWith("/") ||
      raw.startsWith("//") ||
      raw.includes("\\") ||
      raw.includes("://")
    ) {
      return null;
    }
    return raw;
  }, [searchParams]);
  const reason = searchParams.get("reason");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ConsoleStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/v1/auth/console-status", {
          headers: { Accept: "application/json" },
          credentials: "include",
        });
        if (!res.ok) return;
        const body = await res.json();
        if (!cancelled) setStatus(body.data as ConsoleStatus);
      } catch {
        // An unreachable backend. The form still renders, and submitting will
        // report the failure, which is a better experience than a blank panel.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const minLength = status?.password_min_length ?? 12;

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setError(null);
      setSubmitting(true);
      const result = await loginWithPassword(email, password);
    setSubmitting(false);
    if (!result.ok) {
      // One message for a wrong password and an unknown address alike; the
      // backend returns the same string for both on purpose, and the UI must
      // not undo that by implying which one it was.
      setError(result.message ?? "Sign-in failed.");
      // The secret is dropped from the field on failure. Leaving it there is how
      // a password ends up in a screenshot, a screen share, or a shoulder-surfed
      // retry -- and re-typing it is a small cost against that.
      setPassword("");
      return;
    }
    router.replace(next ?? "/merchant");
    },
    [email, password, next, router]
  );

  return (
    <div className="w-full max-w-md space-y-6">
      <div className="space-y-2 text-center">
        <span className="inline-grid h-12 w-12 place-items-center rounded-2xl bg-[#174c3c] text-lg font-black text-white shadow-sm">
          M
        </span>
        <h1 className="font-display text-2xl font-black tracking-tight text-slate-900">
          Merchant Console
        </h1>
        <p className="text-sm text-slate-600">
          Sign in with your merchant or platform account.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div className="space-y-1.5">
          <label htmlFor="email" className="block text-xs font-bold text-slate-700">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-[#174c3c] focus:ring-2 focus:ring-[#174c3c]/15"
            placeholder="admin@merchant.local"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="password" className="block text-xs font-bold text-slate-700">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-[#174c3c] focus:ring-2 focus:ring-[#174c3c]/15"
            placeholder={`At least ${minLength} characters`}
          />
        </div>

        {error ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3 text-xs font-semibold text-rose-800"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
            <span>{error}</span>
          </div>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-[#174c3c] px-5 py-2.5 text-sm font-bold text-white shadow-sm transition-colors hover:bg-[#103c2f] disabled:opacity-60"
        >
          <LogIn className="h-4 w-4" />
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>

      {reason === "insufficient_role" ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4 text-xs leading-relaxed text-amber-900">
          <p className="font-bold">That account cannot open the console</p>
          <p className="mt-1">
            The merchant console needs a merchant or platform account. Yours is signed in
            as a different role, so it was sent back here rather than shown a page of
            panels it could not load.
          </p>
        </div>
      ) : null}

      {reason === "gateway_unavailable" ? (
        <div
          role="alert"
          className="rounded-2xl border border-rose-200 bg-rose-50/70 p-4 text-xs leading-relaxed text-rose-900"
        >
          <p className="font-bold">The gateway did not answer</p>
          <p className="mt-1">
            The console is gated on the API confirming your session, so it stays closed
            while the gateway is unreachable. This is deliberate -- the API is where the
            data is -- and it will open as soon as the gateway responds.
          </p>
        </div>
      ) : null}

      {status?.demo_session_enabled ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-4 text-xs leading-relaxed text-amber-900">
          <p className="font-bold">Local development</p>
          <p className="mt-1">
            This deployment accepts a no-credential demo session, so the console is also
            reachable without a password. That path is refused outside{" "}
            <code className="font-mono">APP_ENV=local</code>.
          </p>
        </div>
      ) : null}

      {!status?.signup_enabled && status !== null ? (
        <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs leading-relaxed text-slate-600">
          <p className="font-bold text-slate-700">No account yet?</p>
          <p className="mt-1">
            Self-service signup is disabled on this deployment. An administrator creates
            accounts with{" "}
            <code className="font-mono">python -m apps.worker.seed_operator</code>. A lost
            password is reset with{" "}
            <code className="font-mono">python -m apps.worker.reset_operator_password</code>.
          </p>
        </div>
      ) : null}

      <div className="flex items-center justify-between text-xs">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 font-bold text-slate-600 hover:text-slate-900"
        >
          <Store className="h-3.5 w-3.5 text-[#174c3c]" />
          Back to storefront
        </Link>
        <span className="inline-flex items-center gap-1.5 font-semibold text-slate-500">
          <KeyRound className="h-3.5 w-3.5" />
          Argon2id · HttpOnly session
        </span>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-[#f8faf9] px-4 py-12">
      {/* `useSearchParams` needs a Suspense boundary during prerender. */}
      <Suspense fallback={<div className="text-sm text-slate-500">Loading…</div>}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
