"""Machine-readable capability discovery document generator (Task 23, Requirement 18)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.api.auth import AppSettings
from apps.api.config import Settings
from apps.api.db import get_db
from packages.observability.logging import get_logger
from packages.schemas.v1 import (
    CapabilityAuthenticationV1,
    CapabilityDocumentV1,
    CapabilityEndpointsV1,
    CapabilityLimitsV1,
    CapabilityPolicySummaryV1,
)
from services.catalog.models import MerchantRules

router = APIRouter(tags=["capability"])
DatabaseSession = Annotated[Session, Depends(get_db)]

logger = get_logger(__name__)


def build_capability_document(
    settings: Settings | None = None,
    session: Session | None = None,
    merchant_id: str | None = None,
) -> CapabilityDocumentV1:
    """Construct the capability document from live configuration and merchant rules."""
    cfg = settings or Settings()
    tenant = merchant_id or cfg.default_merchant_id

    max_tx = cfg.max_transaction_amount_minor
    auto_app = cfg.auto_approval_limit_minor
    allowed_cats = [
        "laptop",
        "smartphone",
        "audio",
        "camera",
        "monitor",
        "computer_accessory",
        "phone_accessory",
        "home_electronics",
        "appliance",
    ]
    blocked_cats = ["weapons", "tobacco", "adult"]
    rules: MerchantRules | None = None

    if session is not None:
        try:
            rules = session.execute(
                select(MerchantRules).where(MerchantRules.merchant_id == tenant)
            ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            # Narrow on purpose. A broad `except Exception` here would also
            # swallow a programming error in the projection below and still serve
            # a document, so a genuine bug would look exactly like an unreachable
            # database. Only a datastore failure is recoverable this way.
            logger.warning(
                "merchant rules unreadable; capability document served from configuration",
                extra={
                    "event": "CAPABILITY_RULES_UNREADABLE",
                    "merchant_id": tenant,
                    "error_kind": type(exc).__name__,
                },
            )
        else:
            if rules is not None:
                max_tx = rules.max_transaction_minor
                auto_app = rules.auto_approval_limit_minor
                if rules.allowed_categories:
                    allowed_cats = list(rules.allowed_categories)
                if rules.blocked_categories:
                    blocked_cats = list(rules.blocked_categories)

    if rules is None:
        from apps.api.routers.policy import get_memory_merchant_rules

        mem = get_memory_merchant_rules(tenant)
        if mem is not None:
            max_tx = mem["max_transaction_minor"]
            auto_app = mem["auto_approval_limit_minor"]
            if mem.get("allowed_categories"):
                allowed_cats = list(mem["allowed_categories"])
            if mem.get("blocked_categories"):
                blocked_cats = list(mem["blocked_categories"])

    auth = CapabilityAuthenticationV1(
        method="api_key_exchange",
        token_endpoint="/api/v1/agent/auth/token",  # noqa: S106
        scopes=["catalog:read", "checkout:write", "payment:write"],
    )
    limits = CapabilityLimitsV1(
        # From settings, not literals: an agent that reads a result cap or a
        # currency the search path does not actually apply has been told the
        # wrong contract.
        max_results=cfg.max_search_results,
        max_quantity=10,
        max_transaction_minor=max_tx,
        auto_approval_limit_minor=auto_app,
        currency=cfg.default_currency,
    )
    endpoints = CapabilityEndpointsV1(
        search="/api/v1/agent/search",
        offers_query="/api/v1/agent/offers/query",
        checkout="/api/v1/agent/checkout",
        authorization="/api/v1/agent/authorization",
        payment="/api/v1/agent/payments",
        payment_status="/api/v1/agent/payments/{id}",
        order="/api/v1/agent/orders/{id}",
    )
    policy = CapabilityPolicySummaryV1(
        policy_version="1.0",
        allowed_categories=allowed_cats,
        blocked_categories=blocked_cats,
        explicit_approval_required=True,
    )

    return CapabilityDocumentV1(
        schema_version="1.0",
        authentication=auth,
        capabilities=[
            "catalog_search",
            "offer_query",
            "checkout",
            "authorization",
            "payment",
            "payment_status",
            "order_lookup",
        ],
        limits=limits,
        endpoints=endpoints,
        policy=policy,
        payment_provider=cfg.payment_provider,
        test_mode=cfg.payment_is_test_mode,
        external_protocol_certification="none",
        protocol_notice=(
            "AgentPay is an independent implementation of agentic commerce patterns. "
            "It is not endorsed or certified by Anthropic, OpenAI, Google, or any external standard body."
        ),
    )


def _get_optional_db() -> Iterator[Session | None]:
    """Yield a DB session if available, or None if the database is unreachable."""
    try:
        from apps.api.db import get_db

        gen = get_db()
        session = next(gen)
    except Exception:
        yield None
        return

    try:
        yield session
    finally:
        with suppress(Exception):
            next(gen, None)


@router.get("/.well-known/agent-capability.json")
@router.get("/.well-known/agent-commerce")
@router.get("/api/v1/capability")
@router.get("/api/v1/agent/capability")
@router.get("/api/v1/agent/capabilities")
def get_capability_document(
    settings: AppSettings,
    session: Session | None = Depends(_get_optional_db),
) -> dict[str, Any]:
    """Serve the machine-readable capability discovery document."""
    doc = build_capability_document(settings, session)
    return doc.model_dump(mode="json")
