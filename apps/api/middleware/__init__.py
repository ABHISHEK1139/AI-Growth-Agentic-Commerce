"""Cross-cutting HTTP middleware, installed as one stack.

Order is the whole design here. Starlette's ``add_middleware`` inserts at the
front of the list, so the *last* one added ends up outermost. Reading outermost
inward, the stack this builds is:

1. :class:`~apps.api.middleware.context.RequestContextMiddleware` — assigns and
   binds the correlation identifiers and echoes them as headers. Outermost so
   that *every* response, including a CORS preflight, a 429, and a 500, carries
   ``X-Request-ID`` and ``X-Trace-ID``, and so the access log line is the last
   thing written for the request.
2. ``CORSMiddleware`` — inside the context middleware so an error response
   generated further in still picks up the CORS headers a browser needs to be
   allowed to read it. A cross-origin failure that the browser hides from the
   frontend is indistinguishable from a network fault.
3. :class:`~apps.api.middleware.errors.UnhandledExceptionMiddleware` — catches
   anything escaping the routers *or* the rate limiter and renders an envelope.
4. :class:`~apps.api.middleware.ratelimit.RateLimitMiddleware` — innermost of
   ours, so a 429 is still logged, still gets correlation headers, and still gets
   CORS headers.

A single ``install_middleware`` call keeps that reasoning in one place instead of
spread through the application factory.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from apps.api.config import Settings
from apps.api.middleware.context import RequestContextMiddleware
from apps.api.middleware.errors import (
    UnhandledExceptionMiddleware,
    install_exception_handlers,
)
from apps.api.middleware.ratelimit import (
    RateLimitMiddleware,
    RateLimitRule,
    build_backend,
)

__all__ = ["install_middleware"]


def install_middleware(app: FastAPI, settings: Settings) -> None:
    """Attach the cross-cutting middleware and exception handlers."""
    install_exception_handlers(app)

    # Lazy: no connection is opened here, so an unreachable Redis cannot stop the
    # process from starting. The middleware reads this attribute per request, so a
    # test can replace it with an in-memory counter.
    app.state.rate_limit_backend = build_backend(
        settings.redis_url,
        timeout_seconds=settings.rate_limit_redis_timeout_seconds,
        cooldown_seconds=settings.rate_limit_degraded_cooldown_seconds,
    )

    # Added innermost first. See the module docstring for the resulting order.
    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.rate_limit_enabled,
        default_rule=RateLimitRule(limit=settings.rate_limit_default_per_minute, window_seconds=60),
    )

    app.add_middleware(UnhandledExceptionMiddleware)

    # CORS is restricted to the configured origins. `Settings.cors_origins`
    # refuses a wildcard outside local development. `Idempotency-Key` must be
    # allowed or the browser blocks every retry-safe POST the frontend makes.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Trace-ID",
        ],
        expose_headers=["X-Request-ID", "X-Trace-ID", "Retry-After"],
    )

    app.add_middleware(RequestContextMiddleware)
