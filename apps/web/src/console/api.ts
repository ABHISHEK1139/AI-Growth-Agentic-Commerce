/**
 * Transport for the two console surfaces that do **not** answer with the
 * standard success envelope.
 *
 * `@/lib/api` is the only place that knows the envelope, and every enveloped
 * endpoint the console reads (`/api/v1/audit/*`, `/api/v1/campaigns/*`,
 * `/api/v1/catalog/*`) goes through `apiGet`/`apiPost` unchanged. Two endpoints
 * the console needs are flat:
 *
 * * `GET /api/v1/capability`, `GET /api/v1/agent/capability` and
 *   `GET /.well-known/agent-capability.json` return
 *   `CapabilityDocumentV1.model_dump()` directly — no `ok`, no `data`
 *   (`apps/api/routers/capability.py`). Passing that through `apiGet` would read
 *   `envelope.ok !== true` and report a *failure* for a perfectly good 200.
 * * `POST /api/v1/agent/explore` returns a bare object with a top-level `ok` and
 *   no `data` member (`apps/api/routers/explore.py`), so `apiPost` would hand
 *   back an empty object.
 *
 * `@/catalog/client` already solves the POST half of this for the buyer screens,
 * but its reader is private and POST-only, and `@/catalog/**` is owned by another
 * pass. So the console has its own reader here. It still routes through
 * `resolveApiUrl`, so no host is hardcoded, it still recognises an *error
 * envelope* (these routers can raise a `DomainError` that the application's
 * exception handler serialises the normal way), and it still bounds every request
 * with a timeout, because a request that never terminates is a hung screen.
 */

import { resolveApiUrl, bootstrapSession, type ApiError } from "@/lib/api";

export type FlatResult<T> =
  | {
      ok: true;
      data: T;
      requestId: string | null;
      /**
       * Server clock when the response was produced, in epoch milliseconds, read
       * from the HTTP `Date` header. Null when absent. A screen that says "read
       * at" should say it by the server's clock, not the browser's.
       */
      serverDateMs: number | null;
    }
  | { ok: false; error: ApiError };

/** Matches `DEFAULT_TIMEOUT_MS` in `@/lib/api`; a console read is not special. */
const DEFAULT_TIMEOUT_MS = 15000;

/** The explore pipeline runs a guard, a model call, a query and research. */
export const EXPLORE_TIMEOUT_MS = 25000;

export interface FlatRequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
  skipAuthBootstrap?: boolean;
}

function clientError(
  code: string,
  message: string,
  retryable: boolean,
  extra: Partial<ApiError> = {}
): ApiError {
  return {
    code,
    message,
    retryable,
    details: extra.details ?? {},
    nextActions: extra.nextActions ?? [],
    status: extra.status ?? null,
    requestId: extra.requestId ?? null,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function headerDateMs(res: Response): number | null {
  const raw = res.headers.get("Date");
  if (!raw) return null;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? null : parsed;
}

async function requestFlat<T>(
  path: string,
  init: RequestInit,
  options: FlatRequestOptions
): Promise<FlatResult<T>> {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const external = options.signal;
  const onExternalAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", onExternalAbort);
  }

  let res: Response;
  try {
    res = await fetch(resolveApiUrl(path), {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body !== undefined && init.body !== null
          ? { "Content-Type": "application/json" }
          : {}),
        ...(init.headers as Record<string, string> | undefined),
      },
      // The console authenticates with the HttpOnly session cookie when it has
      // one, exactly as `@/lib/api` does.
      credentials: "include",
      signal: controller.signal,
    });
  } catch (err) {
    if (timedOut) {
      return {
        ok: false,
        error: clientError(
          "CLIENT_TIMEOUT",
          "The gateway did not answer in time and the request was stopped.",
          true
        ),
      };
    }
    if (external?.aborted) {
      return {
        ok: false,
        error: clientError("CLIENT_NETWORK_ERROR", "The request was cancelled.", true),
      };
    }
    const message = err instanceof Error ? err.message : "Network communication failed.";
    return { ok: false, error: clientError("CLIENT_NETWORK_ERROR", message, true) };
  } finally {
    clearTimeout(timer);
    if (external) external.removeEventListener("abort", onExternalAbort);
  }

  const requestIdHeader = res.headers.get("X-Request-ID");

  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    return {
      ok: false,
      error: clientError(
        "CLIENT_MALFORMED_RESPONSE",
        "The gateway sent a response this application could not read.",
        res.status >= 500,
        { status: res.status, requestId: requestIdHeader }
      ),
    };
  }

  if ((res.status === 401 || res.status === 403) && !path.includes("/auth/") && !options.skipAuthBootstrap) {
    const ok = await bootstrapSession("merchant_admin");
    if (ok) {
      return requestFlat<T>(path, init, { ...options, skipAuthBootstrap: true });
    }
  }

  const record = asRecord(payload);
  const envelopeError = asRecord(record.error);

  // A typed refusal from the service, or a mapped HTTP error, serialised as the
  // standard error envelope even though the success body is flat.
  if (typeof envelopeError.code === "string") {
    return {
      ok: false,
      error: clientError(
        envelopeError.code,
        typeof envelopeError.message === "string" && envelopeError.message
          ? envelopeError.message
          : "The request could not be completed.",
        typeof envelopeError.retryable === "boolean"
          ? envelopeError.retryable
          : res.status >= 500 || res.status === 429,
        {
          details: asRecord(envelopeError.details),
          status: res.status,
          requestId:
            typeof record.request_id === "string" ? record.request_id : requestIdHeader,
        }
      ),
    };
  }

  if (!res.ok) {
    return {
      ok: false,
      error: clientError(
        `HTTP_${res.status}`,
        "The gateway refused this request.",
        res.status >= 500 || res.status === 429,
        {
          status: res.status,
          requestId: requestIdHeader,
          // The readiness probe answers 503 *with* the per-component detail that
          // makes it useful, so the body is carried rather than discarded.
          details: { body: record },
        }
      ),
    };
  }

  return {
    ok: true,
    data: payload as T,
    requestId: typeof record.request_id === "string" ? record.request_id : requestIdHeader,
    serverDateMs: headerDateMs(res),
  };
}

/** GET an endpoint that answers with a flat body. */
export function getFlat<T>(
  path: string,
  options: FlatRequestOptions = {}
): Promise<FlatResult<T>> {
  return requestFlat<T>(path, { method: "GET" }, options);
}

/** POST to an endpoint that answers with a flat body. */
export function postFlat<T>(
  path: string,
  body: unknown,
  options: FlatRequestOptions = {}
): Promise<FlatResult<T>> {
  return requestFlat<T>(
    path,
    { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) },
    options
  );
}
