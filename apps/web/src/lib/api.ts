/**
 * Typed API client for the AgentPay gateway.
 *
 * Every response from the API has one of exactly two shapes (see
 * `packages/schemas/envelope.py`):
 *
 *   { ok: true,  request_id, data: {}, warnings: [], evidence: [], next_actions: [] }
 *   { ok: false, request_id, error: { code, message, retryable, details }, next_actions? }
 *
 * This module is the only place that knows about that envelope. Callers get a
 * discriminated `ApiResult<T>`: on success the unwrapped `data`, on failure a
 * typed `ApiError` carrying `code`, `retryable`, and whatever recovery
 * affordances the service attached as `next_actions`.
 *
 * Paths are relative by default and travel through the Next.js rewrite declared
 * in `next.config.js`. No host is hardcoded anywhere in the web tree; set
 * `NEXT_PUBLIC_API_BASE_URL` only when the API is not reachable through the
 * frontend's own origin.
 */

/** A recovery or follow-up the service says the client may offer. */
export interface NextAction {
  action: string;
  label: string;
  method?: "GET" | "POST" | "PUT" | "DELETE" | null;
  href?: string | null;
  params?: Record<string, unknown>;
}

/** Something the caller should know that did not stop the request. */
export interface EnvelopeWarning {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

/** A pointer to the record a claim came from. */
export interface Evidence {
  kind: string;
  reference: string;
  summary?: string | null;
}

/** The raw envelope, preserved for callers that need the transport shape. */
export interface ApiResponse<T> {
  ok: boolean;
  request_id?: string | null;
  data?: T;
  warnings?: EnvelopeWarning[];
  evidence?: Evidence[];
  next_actions?: NextAction[];
  error?: {
    code: string;
    message: string;
    retryable: boolean;
    details?: Record<string, unknown>;
  };
}

/**
 * A failure a screen can branch on.
 *
 * `code` is the registry code (`NOT_FOUND`, `PAYMENT_UNKNOWN`,
 * `AUTHORIZATION_EXPIRED`, ...). `retryable` comes from the same registry, so a
 * screen can tell a timeout worth re-polling from a terminal refusal without
 * pattern-matching on messages.
 */
export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
  /** Recovery affordances the service attached. May be empty. */
  nextActions: NextAction[];
  /** HTTP status, or null when the request never reached the server. */
  status: number | null;
  requestId: string | null;
}

export interface ApiSuccess<T> {
  ok: true;
  data: T;
  requestId: string | null;
  warnings: EnvelopeWarning[];
  evidence: Evidence[];
  nextActions: NextAction[];
  /**
   * Server clock at the moment this response was produced, in epoch
   * milliseconds, read from the HTTP `Date` header. Null when absent.
   */
  serverDateMs: number | null;
}

export interface ApiFailure {
  ok: false;
  error: ApiError;
  serverDateMs: number | null;
}

export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

/** Codes this client raises itself, for failures that never reached the API. */
export const CLIENT_ERROR_CODES = {
  NETWORK: "CLIENT_NETWORK_ERROR",
  TIMEOUT: "CLIENT_TIMEOUT",
  MALFORMED: "CLIENT_MALFORMED_RESPONSE",
} as const;

/** Default per-request ceiling. A request that never terminates is a hung screen. */
export const DEFAULT_TIMEOUT_MS = 15000;

/**
 * Origin prefix for API calls. Empty by default, which means every path stays
 * relative and is proxied by the frontend origin.
 *
 * The value is normalised to an origin: a configured `/api` or `/api/v1` suffix
 * is stripped, because callers here pass full paths (`/api/v1/payments/...`) and
 * a base that already carried the prefix would produce `/api/v1/api/v1/...`.
 * `docker-compose.yml` currently sets the variable to
 * `http://localhost:8000/api/v1`, so this normalisation is load-bearing.
 */
function apiBase(): string {
  const configured = (process.env.NEXT_PUBLIC_API_BASE_URL || "").trim();
  if (!configured) return "";
  return configured.replace(/\/+$/, "").replace(/\/api(\/v\d+)?$/, "");
}

export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const base = apiBase();
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

/**
 * Endpoints where a duplicate POST could create a second money-moving record.
 * The gateway reads `Idempotency-Key` on these (see `apps/api/routers/payments.py`
 * and the CORS allow-list in `apps/api/middleware/__init__.py`), so the header is
 * generated automatically rather than left to each caller to remember.
 */
const MONEY_MUTATING_PATH = /\/api\/(v\d+\/)?(payments|checkout|authorization)(\/|$|\?)/i;

export function isMoneyMutatingPath(path: string): boolean {
  return MONEY_MUTATING_PATH.test(path);
}

/** A fresh idempotency key. `crypto.randomUUID` where available. */
export function newIdempotencyKey(): string {
  const cryptoRef = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (cryptoRef && typeof cryptoRef.randomUUID === "function") {
    return cryptoRef.randomUUID();
  }
  // Fallback for a non-secure context, where randomUUID is not exposed. Still
  // unique enough to key a single browser's retries, which is all it must do.
  const random = Math.random().toString(16).slice(2).padEnd(12, "0");
  return `idm-${Date.now().toString(16)}-${random}`;
}

function headerDateMs(res: Response): number | null {
  const raw = res.headers.get("Date");
  if (!raw) return null;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? null : parsed;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function failure(
  code: string,
  message: string,
  retryable: boolean,
  extra: Partial<ApiError> = {},
  serverDateMs: number | null = null
): ApiFailure {
  return {
    ok: false,
    serverDateMs,
    error: {
      code,
      message,
      retryable,
      details: extra.details ?? {},
      nextActions: extra.nextActions ?? [],
      status: extra.status ?? null,
      requestId: extra.requestId ?? null,
    },
  };
}

export interface RequestOptions {
  /** Abort and fail with `CLIENT_TIMEOUT` after this many milliseconds. */
  timeoutMs?: number;
  /** Caller-supplied abort signal, honoured alongside the timeout. */
  signal?: AbortSignal;
  /** Force the idempotency header on or off. Defaults to path detection. */
  idempotent?: boolean;
  /** Reuse a key across retries of the same logical request. */
  idempotencyKey?: string;
  headers?: Record<string, string>;
  /** Internal: avoid recursive auth loop. */
  skipAuthBootstrap?: boolean;
}

let bootstrapPromise: Promise<boolean> | null = null;

export async function bootstrapSession(): Promise<boolean> {
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = (async () => {
    try {
      const res = await fetch(resolveApiUrl("/api/v1/auth/session"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          role: "buyer",
          merchant_id: "merchant_demo",
          buyer_id: "buy_shopper_demo",
          subject: "demo_shopper",
        }),
        credentials: "include",
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      bootstrapPromise = null;
    }
  })();
  return bootstrapPromise;
}

/**
 * The raw envelope call, kept for callers that want the transport shape.
 * Prefer {@link apiGet} and {@link apiPost}.
 */
export async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<ApiResponse<T>> {
  const result = await request<T>(endpoint, options, {});
  if (result.ok) {
    return {
      ok: true,
      request_id: result.requestId,
      data: result.data,
      warnings: result.warnings,
      evidence: result.evidence,
      next_actions: result.nextActions,
    };
  }
  return {
    ok: false,
    request_id: result.error.requestId,
    next_actions: result.error.nextActions,
    error: {
      code: result.error.code,
      message: result.error.message,
      retryable: result.error.retryable,
      details: result.error.details,
    },
  };
}

async function request<T>(
  path: string,
  init: RequestInit,
  options: RequestOptions
): Promise<ApiResult<T>> {
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

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.body !== undefined && init.body !== null
      ? { "Content-Type": "application/json" }
      : {}),
    ...(init.headers as Record<string, string> | undefined),
    ...options.headers,
  };

  const method = (init.method || "GET").toUpperCase();
  const wantsIdempotency = options.idempotent ?? (method === "POST" && isMoneyMutatingPath(path));
  if (wantsIdempotency && !headers["Idempotency-Key"]) {
    headers["Idempotency-Key"] = options.idempotencyKey ?? newIdempotencyKey();
  }

  let res: Response;
  try {
    res = await fetch(resolveApiUrl(path), {
      ...init,
      method,
      headers,
      // The web surface authenticates with an HttpOnly session cookie
      // (`apps/api/auth.py`), so the cookie has to ride the request. `include`
      // rather than `same-origin` because a split-origin deployment sets
      // NEXT_PUBLIC_API_BASE_URL, and the API already declares
      // `allow_credentials=True` against a restricted origin list.
      credentials: init.credentials ?? "include",
      signal: controller.signal,
    });
  } catch (err) {
    if (timedOut) {
      return failure(
        CLIENT_ERROR_CODES.TIMEOUT,
        "The request took too long and was stopped. Nothing was submitted twice.",
        true
      );
    }
    if (external?.aborted) {
      return failure(CLIENT_ERROR_CODES.NETWORK, "The request was cancelled.", true);
    }
    const message = err instanceof Error ? err.message : "Network communication failed.";
    return failure(CLIENT_ERROR_CODES.NETWORK, message, true);
  } finally {
    clearTimeout(timer);
    if (external) external.removeEventListener("abort", onExternalAbort);
  }

  const serverDateMs = headerDateMs(res);
  const requestIdHeader = res.headers.get("X-Request-ID");

  let payload: unknown;
  try {
    payload = await res.json();
  } catch {
    return failure(
      CLIENT_ERROR_CODES.MALFORMED,
      "The server sent a response this application could not read.",
      res.status >= 500,
      { status: res.status, requestId: requestIdHeader },
      serverDateMs
    );
  }

  if (res.status === 401 && !path.includes("/auth/") && !options.skipAuthBootstrap) {
    const ok = await bootstrapSession();
    if (ok) {
      return request<T>(path, init, { ...options, skipAuthBootstrap: true });
    }
  }

  const envelope = asRecord(payload);
  const requestId =
    typeof envelope.request_id === "string" ? envelope.request_id : requestIdHeader;
  const nextActions = asArray<NextAction>(envelope.next_actions);

  if (envelope.ok === true) {
    return {
      ok: true,
      data: (envelope.data ?? {}) as T,
      requestId,
      warnings: asArray<EnvelopeWarning>(envelope.warnings),
      evidence: asArray<Evidence>(envelope.evidence),
      nextActions,
      serverDateMs,
    };
  }

  const error = asRecord(envelope.error);
  const code = typeof error.code === "string" ? error.code : `HTTP_${res.status}`;
  const message =
    typeof error.message === "string" && error.message
      ? error.message
      : "The request could not be completed.";
  const retryable =
    typeof error.retryable === "boolean" ? error.retryable : res.status >= 500 || res.status === 429;

  return {
    ok: false,
    serverDateMs,
    error: {
      code,
      message,
      retryable,
      details: asRecord(error.details),
      nextActions,
      status: res.status,
      requestId,
    },
  };
}

/** GET, unwrapped. */
export function apiGet<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  return request<T>(path, { method: "GET" }, options);
}

/**
 * POST, unwrapped. An `Idempotency-Key` is generated automatically for the
 * money-mutating endpoints so a double click cannot create two records.
 */
export function apiPost<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<ApiResult<T>> {
  return request<T>(
    path,
    {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    options
  );
}

/** PUT, unwrapped. */
export function apiPut<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<ApiResult<T>> {
  return request<T>(
    path,
    {
      method: "PUT",
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    options
  );
}

/** DELETE, unwrapped. */
export function apiDelete<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  return request<T>(path, { method: "DELETE" }, options);
}

/**
 * Multipart file upload via `multipart/form-data`.
 *
 * Does NOT set `Content-Type: application/json` so the browser sets the
 * `multipart/form-data; boundary=…` header automatically with the correct
 * boundary string.
 */
export async function apiUpload<T>(
  path: string,
  file: File,
  fieldName = "file",
  options: Omit<RequestOptions, "headers" | "idempotent" | "idempotencyKey"> = {}
): Promise<ApiResult<T>> {
  const form = new FormData();
  form.append(fieldName, file);
  return request<T>(path, { method: "POST", body: form }, { ...options, idempotent: false });
}

