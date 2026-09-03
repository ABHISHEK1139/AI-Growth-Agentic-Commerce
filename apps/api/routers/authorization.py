"""Authorization API endpoints (Task 17, Requirement 13)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import require_scopes
from apps.api.db import get_db
from apps.api.envelope import success
from packages.security.principals import Principal, Scope
from services.authorization.service import AuthorizationService

router = APIRouter(prefix="/api/v1/authorization", tags=["authorization"])
DatabaseSession = Annotated[Session, Depends(get_db)]
AuthPrincipal = Annotated[Principal, Depends(require_scopes(Scope.CHECKOUT_WRITE))]


class RequestAuthorizationPayload(BaseModel):
    checkout_id: str
    ttl_minutes: int = Field(default=15, ge=1, le=1440)


@router.post("")
def request_authorization(
    request: RequestAuthorizationPayload,
    principal: AuthPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Request authorization for a checkout, evaluating policies deterministically."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    auth = service.request_authorization(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        checkout_id=request.checkout_id,
        ttl_minutes=request.ttl_minutes,
    )
    return success({"authorization": auth.model_dump(mode="json")})


@router.get("/{authorization_id}")
def get_authorization(
    authorization_id: str,
    principal: AuthPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Fetch authorization state and policy decision."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    auth = service.get_authorization(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        authorization_id=authorization_id,
    )
    return success({"authorization": auth.model_dump(mode="json")})


@router.post("/{authorization_id}/approve")
def approve_authorization(
    authorization_id: str,
    principal: AuthPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Explicitly approve a pending authorization."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    auth = service.approve_authorization(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        authorization_id=authorization_id,
    )
    return success({"authorization": auth.model_dump(mode="json")})


@router.post("/{authorization_id}/reject")
def reject_authorization(
    authorization_id: str,
    principal: AuthPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Explicitly reject a pending authorization, cancelling the checkout."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    auth = service.reject_authorization(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        authorization_id=authorization_id,
    )
    return success({"authorization": auth.model_dump(mode="json")})
