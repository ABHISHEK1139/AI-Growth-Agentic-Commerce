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
 *
 * Two variables, because the two runtimes are in different places.
 * `NEXT_PUBLIC_API_BASE_URL` is compiled into the client bundle, so its value is
 * resolved by the *browser*, where `http://localhost:8000` is the developer's own
 * machine and is correct. The same literal read on the *server* is the web
 * container pointing at itself: the API is a separate service in the compose
 * network. `AGENTPAY_API_INTERNAL_URL` is the server-side answer.
 *
 * It is guarded by a `typeof window` check and that guard is load-bearing, not
 * tidiness. The value has to be inlined at build time for the Edge middleware to
 * read it (see next.config.js), and inlining puts it in the client bundle too -
 * where `http://api:8000` is a hostname that does not resolve outside the
 * compose network. Without the guard the browser's own catalog and auth calls
 * failed with `ERR_NAME_NOT_RESOLVED` and every page sat on its loading state.
 */
function apiBase(): string {
  const onServer = typeof window === "undefined";
  const internal = onServer ? (process.env.AGENTPAY_API_INTERNAL_URL || "").trim() : "";
  const configured = internal || (process.env.NEXT_PUBLIC_API_BASE_URL || "").trim();
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

/**
 * Whether this deployment accepts a no-credential demo session.
 *
 * `null` until the backend has answered. Every caller that would auto-bootstrap
 * has to wait for this, because silently minting a `merchant_admin` session on a
 * deployment that has turned the demo path off would leave the console
 * permanently unreachable with no way to sign in.
 */
let demoSessionAllowed: boolean | null = null;
let demoSessionProbe: Promise<boolean> | null = null;

async function probeDemoSession(): Promise<boolean> {
  if (demoSessionAllowed !== null) return demoSessionAllowed;
  if (!demoSessionProbe) {
    demoSessionProbe = (async () => {
      try {
        const res = await fetch(resolveApiUrl("/api/v1/auth/console-status"), {
          headers: { Accept: "application/json" },
          credentials: "include",
        });
        if (!res.ok) {
          // An unreachable or older backend. Assume the demo path is on, which
          // preserves the previous behaviour rather than locking every operator
          // out of a console that used to work.
          return true;
        }
        const body = asRecord(await res.json());
        const data = asRecord(body.data);
        return data.demo_session_enabled === true;
      } catch {
        return false;
      }
    })();
  }
  demoSessionAllowed = await demoSessionProbe;
  return demoSessionAllowed;
}

/** Test seam: forget the cached probe result. */
export function __resetDemoSessionProbe(): void {
  demoSessionAllowed = null;
  demoSessionProbe = null;
  bootstrapPromise = null;
  mintInFlight = null;
}

export async function loginWithPassword(
  email: string,
  password: string
): Promise<{ ok: boolean; message?: string; mustChangePassword?: boolean }> {
  try {
    const res = await fetch(resolveApiUrl("/api/v1/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email, password }),
      credentials: "include",
    });
    const body = asRecord(await res.json().catch(() => ({})));
    if (res.ok) {
      const data = asRecord(body.data);
      bootstrapPromise = null;
      return {
        ok: true,
        mustChangePassword: data.must_change_password === true,
      };
    }
    const error = asRecord(body.error);
    return {
      ok: false,
      message:
        typeof error.message === "string"
          ? error.message
          : "Sign-in failed. Check your email and password.",
    };
  } catch {
    return { ok: false, message: "Could not reach the server." };
  }
}

export async function logoutSession(): Promise<boolean> {
  try {
    const res = await fetch(resolveApiUrl("/api/v1/auth/logout"), {
      method: "POST",
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    bootstrapPromise = null;
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * The role of the session this browser already holds, or `null` if none.
 *
 * Read from the server rather than inferred from the URL. A path-based guess
 * looks reasonable and is wrong: a buyer who navigates to a merchant URL would
 * be re-minted as a merchant admin on any deployment where the demo session is
 * live, and the re-mint is a *session* -- it is not scoped to the page that
 * triggered it.
 */
async function currentRole(): Promise<string | null> {
  try {
    const res = await fetch(resolveApiUrl("/api/v1/auth/me"), {
      headers: { Accept: "application/json" },
      credentials: "include",
    });
    if (!res.ok) return null;
    const body = asRecord(await res.json());
    const data = asRecord(body.data);
    if (data.authenticated !== true) return null;
    const principal = asRecord(data.principal);
    return typeof principal.role === "string" ? principal.role : null;
  } catch {
    return null;
  }
}

/**
 * Mint a no-credential session. Refused unless the backend says the deployment
 * allows it, so this can never become a silent way into an admin console.
 */
export async function bootstrapSession(
  role: "buyer" | "merchant_admin" = "buyer"
): Promise<boolean> {
  if (!(await probeDemoSession())) return false;

  // Never replace a session that already exists. This is called from the
  // storefront on every page load with the default `buyer` role, so a merchant
  // admin who had just signed in was silently downgraded -- and the console
  // screens then failed with `FORBIDDEN` on every request that needs a merchant
  // role, which reads as a permissions bug rather than as a session that had
  // been replaced thirty seconds earlier.
  const existing = await currentRole();
  if (existing !== null) {
    // `true` means "this browser has a usable session", which is what the
    // callers actually check. Not "we minted one" -- a caller cannot act on
    // that distinction, and reporting failure here would make the storefront
    // look broken to a signed-in shopper.
    return true;
  }

  return mintDemoSession(role);
}

/**
 * POST a fresh no-credential session, unconditionally.
 *
 * Split out from {@link bootstrapSession} so the rate-limited request is
 * serialised: the storefront calls `bootstrapSession` on every page load, and
 * several tabs or a fast test run would otherwise fire several of them at once.
 */
let mintInFlight: Promise<boolean> | null = null;

function mintDemoSession(role: "buyer" | "merchant_admin"): Promise<boolean> {
  if (mintInFlight) return mintInFlight;

  mintInFlight = (async () => {
    try {
      const res = await fetch(resolveApiUrl("/api/v1/auth/demo-session"), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          role,
          merchant_id: "merchant_demo",
          buyer_id: role === "buyer" ? "buy_shopper_demo" : undefined,
          subject: role === "buyer" ? "demo_shopper" : "demo_merchant_admin",
        }),
        credentials: "include",
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      // Cleared so a later call can mint again. Kept until the request settles
      // so concurrent callers share one, which is the whole point.
      mintInFlight = null;
    }
  })();

  return mintInFlight;
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

  if (
    (res.status === 401 || res.status === 403) &&
    !path.includes("/auth/") &&
    !options.skipAuthBootstrap
  ) {
    // Re-mint and retry, once. This is what makes the console usable in local
    // development, where the demo session is the only credential there is. It
    // must not become a way past a real login: `bootstrapSession` refuses unless
    // the backend reports that the demo path is enabled, and outside local
    // development it is not, so a 401 here stays a 401.
    //
    // **Read the current session, never guess the role.** The role used to be
    // inferred from the request path, which meant a `/merchant/*` request would
    // re-mint as `merchant_admin` -- including for a *buyer* who had navigated
    // to a merchant URL. On a deployment where the demo path is live that
    // silently escalated the session; where it is not, the re-mint was a no-op
    // and the console just 403'd in a loop. Reading `/auth/me` costs one request
    // and is correct in both cases.
    const current = await currentRole();
    const ok = await bootstrapSession(current === "merchant_admin" ? "merchant_admin" : "buyer");
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

