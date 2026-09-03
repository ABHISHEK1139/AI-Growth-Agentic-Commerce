"""Cross-sell and Upsell Recommendation API Router (Phase 4 / Revenue Growth Layer)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import optional_principal, require_roles
from apps.api.envelope import success
from packages.observability.logging import get_logger
from packages.security.principals import Principal, Role
from services.recommendations.service import RecommendationService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])
MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN)),
]


def _get_optional_db() -> Iterator[Session | None]:
    """Yield a DB session when the database is reachable, or ``None`` otherwise.

    This allows the recommendation endpoints to degrade gracefully in test /
    offline environments where no database is running, while using the real DB
    for product resolution and inventory checks when it *is* available.
    """
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


OptionalDatabaseSession = Annotated[Session | None, Depends(_get_optional_db)]


class CrossSellRequest(BaseModel):
    target_product_id: str
    budget_limit_minor: int | None = Field(default=None, ge=0)


@router.post("/cross-sell")
def get_cross_sell_recommendations(
    request: CrossSellRequest,
    principal: Principal | None = Depends(optional_principal),
    session: OptionalDatabaseSession = None,
) -> dict[str, Any]:
    """Retrieve strictly compatible accessories with contextual rationale and AOV projection."""
    merchant_id = (principal.merchant_id if principal else None) or "merchant_demo"
    service = RecommendationService()
    outcome = service.get_cross_sell_recommendations(
        session,
        merchant_id=merchant_id,
        target_product_id=request.target_product_id,
        budget_limit_minor=request.budget_limit_minor,
    )

    return success(
        {
            "target_product_id": outcome.target_product_id,
            "target_title": outcome.target_title,
            "recommendations": [
                {
                    "product_id": r.product_id,
                    "offer_id": r.offer_id,
                    "title": r.title,
                    "category": r.category,
                    "price_minor": r.price_minor,
                    "currency": r.currency,
                    "compatibility_reason": r.compatibility_reason,
                    "savings_minor": r.savings_minor,
                    "alternative_title": r.alternative_title,
                    "available_quantity": r.available_quantity,
                }
                for r in outcome.recommendations
            ],
            "metrics": {
                "base_aov_minor": outcome.base_aov_minor,
                "projected_aov_minor": outcome.projected_aov_minor,
                "estimated_attach_rate_pct": outcome.estimated_attach_rate_pct,
            },
        }
    )


@router.get("/metrics")
def get_cross_sell_metrics(
    principal: MerchantPrincipal,
    session: OptionalDatabaseSession = None,
) -> dict[str, Any]:
    """Fetch live merchant revenue growth metrics attributed to AI cross-sell and up-sell."""
    service = RecommendationService()
    metrics = service.get_merchant_metrics(session, merchant_id=principal.merchant_id)
    return success(metrics)
