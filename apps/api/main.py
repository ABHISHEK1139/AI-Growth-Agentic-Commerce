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
    agent_tools,
    alerts,
    api_keys,
    audit,
    auth,
    authorization,
    campaigns,
    capability,
    catalog,
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

    # Health probes are mounted unversioned: an orchestrator should not have to
    # know about API versions to decide whether the process is alive.
    app.include_router(health.router)

    return app


app = create_app()
