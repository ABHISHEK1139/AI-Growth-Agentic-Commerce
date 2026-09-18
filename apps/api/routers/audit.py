"""Merchant-scoped audit ledger reads."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from apps.api.auth import current_principal, require_session_roles
from apps.api.db import get_db
from apps.api.envelope import success
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal, Role
from services.audit.repository import list_events

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

MerchantPrincipal = Annotated[Principal, Depends(current_principal)]
DatabaseSession = Annotated[Session, Depends(get_db)]

MerchantAuditPrincipal = Annotated[
    Principal,
    Depends(require_session_roles(Role.MERCHANT_ADMIN, Role.PLATFORM_ADMIN)),
]


def _get_optional_db() -> Any:
    session = None
    try:
        from apps.api.db import get_session_factory

        factory = get_session_factory()
        session = factory()
    except Exception:
        yield None
        return

    try:
        yield session
    finally:
        if session is not None:
            session.close()


OptionalDatabaseSession = Annotated[Session | None, Depends(_get_optional_db)]


@router.get("/events")
def events(
    session: OptionalDatabaseSession,
    principal: MerchantAuditPrincipal,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    event_type: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="desc", pattern="^(asc|desc|ASC|DESC)$"),
) -> dict[str, Any]:
    merchant_id = principal.merchant_id
    if session is not None:
        try:
            return success(
                {
                    "events": list_events(
                        session,
                        merchant_id=merchant_id,
                        aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id,
                        event_type=event_type,
                        start_at=start_at,
                        end_at=end_at,
                        limit=limit,
                        offset=offset,
                        order=order,
                    )
                }
            )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "Failed to query audit events",
                code=ErrorCode.SERVICE_UNAVAILABLE,
            ) from exc

    return success({"events": [], "degraded": True, "reason": "database_unavailable"})


@router.get("/aggregates/{aggregate_type}/{aggregate_id}")
def aggregate_events(
    aggregate_type: Annotated[str, Path(max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")],
    aggregate_id: Annotated[str, Path(max_length=128)],
    principal: Annotated[Principal, Depends(current_principal)],
    session: DatabaseSession,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """One aggregate's ledger slice.

    Merchant sessions read any aggregate in their tenant. Buyer sessions may
    read only checkouts, orders, and payments they own — anything else,
    including another buyer's records, is answered NOT_FOUND.
    """
    from packages.security.principals import Role

    if principal.role is Role.BUYER:
        if not principal.buyer_id:
            raise DomainError("Buyer ID required", code=ErrorCode.FORBIDDEN)
        _assert_buyer_owns_aggregate(
            session,
            principal=principal,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
        )
    elif principal.role not in (Role.MERCHANT_ADMIN, Role.PLATFORM_ADMIN):
        raise DomainError("You do not have access to this resource.", code=ErrorCode.FORBIDDEN)
    return success(
        {
            "events": list_events(
                session,
                merchant_id=principal.merchant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                limit=limit,
                offset=offset,
            ),
            "limit": limit,
            "offset": offset,
        }
    )


def _assert_buyer_owns_aggregate(
    session: Session,
    *,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: str,
) -> None:
    """Raise NOT_FOUND unless the buyer's own record backs this aggregate."""
    if not principal.buyer_id:
        raise DomainError("The requested resource does not exist.", code=ErrorCode.NOT_FOUND)
    try:
        if aggregate_type == "checkout":
            from services.checkout.service import CheckoutService

            CheckoutService().get_checkout(
                session,
                buyer_id=principal.buyer_id,
                merchant_id=principal.merchant_id,
                checkout_id=aggregate_id,
            )
        elif aggregate_type == "order":
            from services.orders.service import OrderService

            OrderService().get_order_for_buyer(
                session,
                buyer_id=principal.buyer_id,
                merchant_id=principal.merchant_id,
                order_id=aggregate_id,
            )
        elif aggregate_type == "payment":
            from services.payments.service import PaymentService

            PaymentService().get_payment_by_id(
                session,
                payment_id=aggregate_id,
                merchant_id=principal.merchant_id,
                buyer_id=principal.buyer_id,
            )
        else:
            raise DomainError("The requested resource does not exist.", code=ErrorCode.NOT_FOUND)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(
            "The requested resource does not exist.",
            code=ErrorCode.NOT_FOUND,
        ) from exc
