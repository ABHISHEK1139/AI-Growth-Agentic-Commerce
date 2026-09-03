"""Checkout API endpoints (Task 15, Requirement 11)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import require_scopes
from apps.api.db import get_db
from apps.api.envelope import success
from packages.security.principals import Principal, Scope
from services.checkout.service import CheckoutService

router = APIRouter(prefix="/api/v1/checkout", tags=["checkout"])
DatabaseSession = Annotated[Session, Depends(get_db)]
CheckoutPrincipal = Annotated[Principal, Depends(require_scopes(Scope.CHECKOUT_WRITE))]


class CreateCheckoutRequest(BaseModel):
    offer_id: str
    quantity: int = Field(default=1, ge=1)
    ttl_minutes: int = Field(default=15, ge=1, le=1440)


@router.post("")
def create_checkout(
    request: CreateCheckoutRequest,
    principal: CheckoutPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Create a new checkout with frozen server-calculated totals and reserved inventory."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for checkout", code=ErrorCode.FORBIDDEN)

    service = CheckoutService()
    checkout = service.create_checkout(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        offer_id=request.offer_id,
        quantity=request.quantity,
        ttl_minutes=request.ttl_minutes,
    )
    return success({"checkout": checkout.model_dump(mode="json")})


@router.get("/{checkout_id}")
def get_checkout(
    checkout_id: str,
    principal: CheckoutPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Fetch an existing checkout by ID."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for checkout", code=ErrorCode.FORBIDDEN)

    service = CheckoutService()
    checkout = service.get_checkout(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        checkout_id=checkout_id,
    )
    return success({"checkout": checkout.model_dump(mode="json")})


@router.post("/{checkout_id}/cancel")
def cancel_checkout(
    checkout_id: str,
    principal: CheckoutPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Cancel a checkout and release its inventory reservation hold."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for checkout", code=ErrorCode.FORBIDDEN)

    service = CheckoutService()
    checkout = service.cancel_checkout(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        checkout_id=checkout_id,
    )
    return success({"checkout": checkout.model_dump(mode="json")})
