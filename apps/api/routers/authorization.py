"""Authorization API endpoints (Task 17, Requirement 13)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import require_scopes, require_session_scopes
from apps.api.db import get_db
from apps.api.envelope import success
from packages.security.principals import Principal, Scope
from services.authorization.service import AuthorizationService

router = APIRouter(prefix="/api/v1/authorization", tags=["authorization"])
DatabaseSession = Annotated[Session, Depends(get_db)]
AuthPrincipal = Annotated[Principal, Depends(require_scopes(Scope.CHECKOUT_WRITE))]

#: Approving or rejecting an authorization is a human gate, not an agent action.
#:
#: An exchanged agent token carries ``checkout:write`` -- it has to, so the agent can
#: build a cart -- which made ``require_scopes`` alone accept the agent's own approval
#: of its own spending. That silently deleted the control the capability document
#: advertises as ``explicit_approval_required``, and it deleted it in the exact place
#: it matters: above the auto-approval limit, where the whole point is that a person
#: looks at it. Requirement 20.5 already says a session-only administrative action must
#: not be performable with a long-lived agent credential; this is that action.
ApproverPrincipal = Annotated[Principal, Depends(require_session_scopes(Scope.CHECKOUT_WRITE))]


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
    principal: ApproverPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Explicitly approve a pending authorization. Requires a signed-in session."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    if principal.buyer_id is None:
        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)
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
    principal: ApproverPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Explicitly reject a pending authorization, cancelling the checkout.

    Requires a signed-in session, for the same reason as approval.
    """
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    if principal.buyer_id is None:
        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)
    auth = service.reject_authorization(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        authorization_id=authorization_id,
    )
    return success({"authorization": auth.model_dump(mode="json")})
