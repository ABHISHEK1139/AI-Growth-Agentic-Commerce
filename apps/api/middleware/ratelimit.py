"""Redis-backed rate limiting.

Fixed window, per actor where one is known and per client address otherwise. A
fixed window is a deliberate simplification: it can admit up to twice the nominal
rate across a window boundary, and that is an acceptable price for a counter that
is one ``INCR`` and impossible to get subtly wrong under concurrency.

Two properties matter more than the algorithm.

**It fails open.** If Redis is unreachable the request is allowed and the
degradation is logged. A rate limiter is a protective control, not a dependency of
the payment path â€” taking checkout down because a cache is unavailable would be a
worse outage than the one being prevented. After a failure the backend stops
trying for a short cooldown, so a Redis outage does not add its connect timeout to
every request.

**Limits are per route.** The design gives different endpoints different rates
(payment creation is expensive and sensitive, catalog search is cheap and chatty),
so rules are declared in a table and routes added by later tasks register their
own. A single global number would be simultaneously too tight for search and far
too loose for payments.

Health probes are exempt. An orchestrator probing every few seconds would
otherwise trip the limit and be told the service is unhealthy by the very control
meant to protect it.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from apps.api.envelope import error_response
from apps.api.middleware.context import record_error_code
from packages.errors.registry import ErrorCode
from packages.observability.context import current_ids
from packages.observability.logging import get_logger
from packages.schemas.envelope import NextAction

logger = get_logger(__name__)

RETRY_AFTER_HEADER = "Retry-After"
LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """A limit declaration: ``limit`` requests per ``window_seconds``."""

    limit: int
    window_seconds: int = 60

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("rate limit must allow at least one request per window")
        if self.window_seconds < 1:
            raise ValueError("rate limit window must be at least one second")

    def window_start(self, now: float) -> int:
        return int(now // self.window_seconds) * self.window_seconds

    def retry_after_seconds(self, now: float) -> int:
        """Whole seconds until the current window ends, never less than one."""
        elapsed = now - self.window_start(now)
        return max(1, math.ceil(self.window_seconds - elapsed))


#: Applied to any route without its own declaration. Generous, because the point
#: of the default is to stop a runaway client, not to shape traffic.
DEFAULT_RULE = RateLimitRule(limit=120, window_seconds=60)

#: Never rate limited. Probes must stay answerable, and the docs are static.
EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)

# Keyed by "METHOD /path". A trailing "*" matches by prefix, which is how a
# parameterised path is covered without repeating the router's converters here
# (middleware runs before route matching, so no path template is available yet).
#
# The payment and authorization rates are the tight ones by design: they are the
# endpoints that move money and the ones an abusive client would hammer.
_ROUTE_RULES: dict[str, RateLimitRule] = {
    # Money and approval, internal surface.
    "POST /api/v1/payments": RateLimitRule(limit=10, window_seconds=60),
    "POST /api/v1/authorizations": RateLimitRule(limit=20, window_seconds=60),
    # Money and approval, public agent surface.
    "POST /api/v1/agent/payment": RateLimitRule(limit=10, window_seconds=60),
    "POST /api/v1/agent/authorization": RateLimitRule(limit=20, window_seconds=60),
    # Token exchange: tight, because it is the brute-force surface.
    "POST /api/v1/agent/auth/token": RateLimitRule(limit=20, window_seconds=60),
    # Catalog reads: cheap and chatty, an agent compares many options.
    "POST /api/v1/catalog/search": RateLimitRule(limit=60, window_seconds=60),
    "GET /api/v1/catalog/*": RateLimitRule(limit=60, window_seconds=60),
    "POST /api/v1/agent/search": RateLimitRule(limit=60, window_seconds=60),
    "POST /api/v1/agent/offers/*": RateLimitRule(limit=60, window_seconds=60),
    # AI Explore & Research query endpoints: bounded against cost amplification.
    "POST /api/explore": RateLimitRule(limit=20, window_seconds=60),
    "POST /api/v1/agent/explore": RateLimitRule(limit=20, window_seconds=60),
    "POST /api/v1/research/ask": RateLimitRule(limit=20, window_seconds=60),
    # E-commerce connectors.
    "POST /api/v1/connectors/*": RateLimitRule(limit=30, window_seconds=60),
    # Checkout: a handful per session is normal.
    "POST /api/v1/checkouts": RateLimitRule(limit=30, window_seconds=60),
    "POST /api/v1/agent/checkout": RateLimitRule(limit=30, window_seconds=60),
    # Provider callbacks are not client traffic and are signature-verified; a high
    # ceiling still bounds a misbehaving retry loop.
    "POST /api/v1/webhooks/*": RateLimitRule(limit=300, window_seconds=60),
    # Razorpay web checkout: these move money and were falling through to the
    # generous default while the internal payment route was tightly limited.
    "POST /api/create-order": RateLimitRule(limit=10, window_seconds=60),
    "POST /api/v1/payments/razorpay/create-order": RateLimitRule(limit=10, window_seconds=60),
    "POST /api/verify-payment": RateLimitRule(limit=20, window_seconds=60),
    "POST /api/v1/payments/razorpay/verify-signature": RateLimitRule(limit=20, window_seconds=60),
    # LLM-backed surfaces: every call can trigger model completions and outbound
    # research fetches, so they need their own cost bound rather than the default.
    "POST /api/v1/agent/tools/execute": RateLimitRule(limit=30, window_seconds=60),
    "POST /api/v1/agent/tool/execute": RateLimitRule(limit=30, window_seconds=60),
    "POST /api/v1/agent/converse": RateLimitRule(limit=20, window_seconds=60),
}


def declare_route_limit(method: str, path: str, rule: RateLimitRule) -> None:
    """Declare (or override) the limit for a route.

    Lets a router own its own rate next to the endpoint it protects instead of
    editing this table from a distance.
    """
    _ROUTE_RULES[f"{method.upper()} {path}"] = rule


def route_rules() -> Mapping[str, RateLimitRule]:
    """Read-only view of the declared rules, for tests and the capability doc."""
    return dict(_ROUTE_RULES)


def is_exempt(path: str, *, extra_prefixes: Iterable[str] = ()) -> bool:
    prefixes = (*EXEMPT_PATH_PREFIXES, *extra_prefixes)
    return any(path == prefix or path.startswith(prefix) for prefix in prefixes)


def rule_for(method: str, path: str, *, default: RateLimitRule = DEFAULT_RULE) -> RateLimitRule:
    """Resolve the rule for a request: exact match, then longest prefix, then default."""
    exact = _ROUTE_RULES.get(f"{method.upper()} {path}")
    if exact is not None:
        return exact

    best: tuple[int, RateLimitRule] | None = None
    for key, rule in _ROUTE_RULES.items():
        if not key.endswith("*"):
            continue
        candidate_method, _, candidate_path = key.partition(" ")
        if candidate_method != method.upper():
            continue
        prefix = candidate_path[:-1]
        if path.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), rule)
    if best is not None:
        return best[1]
    return default


class RateLimitUnavailableError(RuntimeError):
    """The counter store could not be reached. Callers must fail open."""


class RateLimitBackend(Protocol):
    """Counter store. Synchronous: both implementations are IO or memory bound."""

    def hit(self, key: str, window_seconds: int) -> int:
        """Increment ``key`` and return its count within the current window.

        Raises :class:`RateLimitUnavailableError` when the count is unknowable.
        """


class InMemoryRateLimitBackend:
    """Process-local counters.

    Used by the unit suite so rate limiting is testable with no Redis, and as a
    single-process fallback. Not correct across workers, which is exactly why
    production uses Redis.
    """

    def __init__(self, *, max_keys: int = 4096) -> None:
        self._counts: dict[str, int] = {}
        self._max_keys = max_keys
        self._lock = threading.Lock()

    def hit(self, key: str, window_seconds: int) -> int:
        with self._lock:
            if len(self._counts) > self._max_keys:
                # Keys embed their window, so old ones are dead weight; dropping
                # them all only ever forgives requests.
                self._counts.clear()
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            return count


class RedisRateLimitBackend:
    """Redis counters with a short circuit breaker.

    The client is created lazily and never at import time, so nothing about
    starting the process depends on Redis being up. After a failure the backend
    reports unavailable for ``cooldown_seconds`` without touching the socket,
    which keeps a Redis outage from adding a connect timeout to every request.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        timeout_seconds: float = 0.25,
        cooldown_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis_url = redis_url
        self._timeout_seconds = timeout_seconds
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._client: object | None = None
        self._unavailable_until: float = 0.0
        self._lock = threading.Lock()

    @property
    def degraded(self) -> bool:
        return self._clock() < self._unavailable_until

    def _connect(self) -> object:
        import redis

        return redis.Redis.from_url(
            self._redis_url,
            socket_connect_timeout=self._timeout_seconds,
            socket_timeout=self._timeout_seconds,
            retry_on_timeout=False,
        )

    def _degrade(self) -> None:
        with self._lock:
            self._unavailable_until = self._clock() + self._cooldown_seconds
            client = self._client
            self._client = None
        if client is not None:
            # Closing an already-broken client is best effort.
            with contextlib.suppress(Exception):
                client.close()  # type: ignore[attr-defined]

    def hit(self, key: str, window_seconds: int) -> int:
        if self.degraded:
            raise RateLimitUnavailableError("rate limit store is in cooldown after a failure")

        try:
            if self._client is None:
                self._client = self._connect()
            pipeline = self._client.pipeline()  # type: ignore[attr-defined]
            pipeline.incr(key, 1)
            # Refreshing the TTL every hit is harmless: the key already encodes
            # its window, so a later expiry cannot leak a count into the next one.
            pipeline.expire(key, window_seconds)
            count, _ = pipeline.execute()
            return int(count)
        except RateLimitUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - any client failure means fail open
            self._degrade()
            raise RateLimitUnavailableError(type(exc).__name__) from exc


def build_backend(
    redis_url: str,
    *,
    timeout_seconds: float = 0.25,
    cooldown_seconds: float = 10.0,
) -> RateLimitBackend:
    return RedisRateLimitBackend(
        redis_url, timeout_seconds=timeout_seconds, cooldown_seconds=cooldown_seconds
    )


def actor_identity(request: Request) -> str:
    """Who to count against.

    An authenticated actor is counted by identity, so one buyer's traffic cannot
    exhaust another's allowance through a shared address. Otherwise the client
    address is used. ``X-Forwarded-For`` is deliberately ignored: it is
    caller-controlled, and trusting it would let anyone reset their own counter by
    inventing a header.
    """
    actor_id = current_ids().actor_id
    if actor_id:
        return f"actor:{actor_id}"
    client = request.client
    return f"ip:{client.host}" if client and client.host else "ip:unknown"


def _bucket_key(rule: RateLimitRule, identity: str, method: str, path: str, now: float) -> str:
    return f"ratelimit:{method.upper()}:{path}:{identity}:{rule.window_start(now)}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Counts requests and answers ``RATE_LIMITED`` when a rule is exceeded.

    The backend is read from ``app.state.rate_limit_backend`` on every request
    rather than captured at construction, so a test can swap in an in-memory
    counter and an operator could swap in a different store without rebuilding the
    middleware stack.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        enabled: bool = True,
        default_rule: RateLimitRule = DEFAULT_RULE,
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._default_rule = default_rule

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not self._enabled or is_exempt(path):
            return await call_next(request)

        backend: RateLimitBackend | None = getattr(request.app.state, "rate_limit_backend", None)
        if backend is None:
            self._log_degraded(request, reason="no_backend_configured")
            return await call_next(request)

        rule = rule_for(request.method, path, default=self._default_rule)
        now = time.time()
        key = _bucket_key(rule, actor_identity(request), request.method, path, now)

        try:
            # redis-py is synchronous; off the event loop it goes.
            count = await run_in_threadpool(backend.hit, key, rule.window_seconds)
        except Exception as exc:  # noqa: BLE001 - fail open, always
            self._log_degraded(request, reason=type(exc).__name__)
            return await call_next(request)

        if count > rule.limit:
            return self._rate_limited_response(request, rule, now)

        response = await call_next(request)
        response.headers[LIMIT_HEADER] = str(rule.limit)
        response.headers[REMAINING_HEADER] = str(max(0, rule.limit - count))
        return response

    def _log_degraded(self, request: Request, *, reason: str) -> None:
        """A limiter outage is invisible unless it is logged, and an invisible
        protective control that is off is worse than not having it."""
        logger.warning(
            "rate limiting degraded, failing open",
            extra={
                "event": "RATE_LIMIT_DEGRADED",
                "reason": reason,
                "method": request.method,
                "path": request.url.path,
                "outcome": "fail_open",
            },
        )

    def _rate_limited_response(self, request: Request, rule: RateLimitRule, now: float) -> Response:
        retry_after = rule.retry_after_seconds(now)
        record_error_code(request, ErrorCode.RATE_LIMITED.value)
        logger.warning(
            "rate limit exceeded",
            extra={
                "event": "RATE_LIMIT_EXCEEDED",
                "error_code": ErrorCode.RATE_LIMITED.value,
                "method": request.method,
                "path": request.url.path,
                "limit": rule.limit,
                "window_seconds": rule.window_seconds,
                "retry_after_seconds": retry_after,
            },
        )
        return error_response(
            ErrorCode.RATE_LIMITED,
            details={
                "limit": rule.limit,
                "window_seconds": rule.window_seconds,
                "retry_after_seconds": retry_after,
            },
            # The retry hint travels twice on purpose: the header for HTTP clients
            # and proxies, the action for a UI that renders recoveries generically.
            next_actions=[
                NextAction(
                    action="RETRY_AFTER",
                    label=f"Wait {retry_after} seconds and try again.",
                    params={"retry_after_seconds": retry_after},
                )
            ],
            headers={
                RETRY_AFTER_HEADER: str(retry_after),
                LIMIT_HEADER: str(rule.limit),
                REMAINING_HEADER: "0",
            },
        )
