"""FastAPI application factory.

AgentPay is a modular monolith: one process, clear internal module boundaries.
This module wires the pieces together and owns nothing itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.auth import install_auth
from apps.api.config import Settings, get_settings
from apps.api.middleware import install_middleware
from apps.api.routers import (
    agent,
    agent_api_keys,
    agent_tools,
    alerts,
    api_keys,
    audit,
    auth,
    authorization,
    campaigns,
    capability,
    catalog,
    channels,
    checkout,
    connectors,
    explore,
    health,
    merchant_catalog,
    orders,
    payments,
    policy,
    razorpay_checkout,
    recommendations,
    research,
)
from packages.cache import (
    InMemoryCacheBackend,
    NullCacheBackend,
    RedisCacheBackend,
    reset_cache,
)
from packages.observability.logging import configure_logging, get_logger

logger = get_logger(__name__)

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "startup",
        extra={
            "event": "APPLICATION_STARTED",
            "env": settings.app_env,
            "payment_provider": settings.payment_provider,
            "model_provider": settings.model_provider,
        },
    )
    yield
    logger.info("shutdown", extra={"event": "APPLICATION_STOPPED"})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Accepting an explicit ``settings`` argument keeps tests free to construct an
    app with overridden configuration instead of mutating the environment.
    """
    settings = settings or get_settings()
    # Fail fast rather than serving traffic with template placeholder secrets.
    settings.validate_for_env()

    # Refuses test-double providers outside `local`. Deliberately separate from the
    # check above, which asks "is the *real* provider configured?" and this one asks "is
    # the *fake* provider running?" -- the failure that yields a deployment reporting
    # successful payments that never moved money, and accepting webhooks signed with a
    # secret published in this repository.
    settings.validate_providers_for_env()

    # A SQLite URL is a legitimate explicit choice, so this only refuses the
    # case that is dangerous rather than the case that is unusual: Postgres
    # configured, not reachable, and the fallback would quietly serve an empty
    # catalog. `apps.api.db` calls the same check when the connection fails.
    settings.validate_datastore_for_env()
    configure_logging(level=settings.log_level, service=settings.app_name)

    app = FastAPI(
        title="AgentPay",
        version="0.1.0",
        summary="Merchant-side AI commerce gateway for agentic commerce",
        description=(
            "AgentPay makes an ordinary merchant machine-readable and safely "
            "transactable by AI buyers. The language model interprets intent and "
            "selects tools; a deterministic core owns prices, inventory, policy, "
            "authorization, and payment."
        ),
        lifespan=lifespan,
        # Interactive docs are useful for a demo but are not a public surface.
        docs_url="/docs" if settings.app_env != "demo" else None,
        redoc_url=None,
    )
    app.state.settings = settings

    # The read cache backend, built here rather than lazily inside the first request so
    # that `reset_cache` in a test is not undone by the next call. Redis is not
    # contacted at construction time, so an unreachable cache cannot stop the process
    # from starting -- `packages.cache` fails open and this only chooses the backend.
    #
    # Correctness by default: an in-process cache is only selected when an operator has
    # said the deployment is a single process. That is the dangerous default to get
    # wrong, because every worker gets its own copy -- an invalidation on one leaves the
    # others serving stale entries until their TTLs expire, which for an offer is a
    # stale price, and the failure is invisible until a customer sees one. So when the
    # requirement is unmet the cache is *disabled* (correct, and today's behaviour)
    # rather than silently downgraded to something fast and wrong.
    if not settings.cache_enabled:
        reset_cache(NullCacheBackend())
    elif settings.redis_url:
        reset_cache(
            RedisCacheBackend(
                settings.redis_url,
                timeout_seconds=settings.cache_timeout_seconds,
                cooldown_seconds=settings.cache_cooldown_seconds,
            )
        )
    elif settings.cache_allow_process_local:
        logger.warning(
            "Using a process-local cache with no Redis configured. This is only correct "
            "for a single-process deployment: with more than one API worker or replica "
            "each holds its own copy, so invalidations do not propagate and readers can "
            "be served a stale offer price until its TTL expires. Configure REDIS_URL, "
            "or set CACHE_ALLOW_PROCESS_LOCAL=1 to accept that knowingly.",
            extra={"event": "CACHE_PROCESS_LOCAL_FALLBACK"},
        )
        reset_cache(InMemoryCacheBackend())
    else:
        logger.warning(
            "Read cache disabled: no REDIS_URL is configured and "
            "CACHE_ALLOW_PROCESS_LOCAL is not 1. Falling back to uncached reads, which "
            "are correct but slower. Configure REDIS_URL to enable caching.",
            extra={"event": "CACHE_DISABLED_NO_REDIS"},
        )
        reset_cache(NullCacheBackend())

    # Correlation identifiers, envelopes, error mapping, rate limiting, and CORS.
    # Order matters and is explained in `apps.api.middleware`.
    install_middleware(app, settings)

    # The API client registry the agent token exchange resolves against. Empty
    # until Task 9 gives it a persistent api_client repository.
    install_auth(app, settings)
    app.include_router(auth.router)
    app.include_router(audit.router)
    app.include_router(capability.router)
    app.include_router(catalog.router)
    app.include_router(merchant_catalog.router)
    app.include_router(checkout.router)
    app.include_router(authorization.router)
    app.include_router(payments.router)
    # The buyer-facing order surface. Session-authenticated, so the web
    # application can read it; the agent surface keeps its own token-only route.
    app.include_router(orders.router)
    app.include_router(agent.router)
    app.include_router(agent_api_keys.router)
    app.include_router(agent_tools.router)
    app.include_router(api_keys.router)
    app.include_router(explore.router)
    app.include_router(razorpay_checkout.router)
    app.include_router(recommendations.router)
    app.include_router(campaigns.router)
    app.include_router(alerts.router)
    app.include_router(policy.router)
    app.include_router(research.router)
    app.include_router(connectors.router)
    app.include_router(channels.router)

    # Health probes are mounted unversioned: an orchestrator should not have to
    # know about API versions to decide whether the process is alive.
    app.include_router(health.router)

    return app


app = create_app()
