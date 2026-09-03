"""Merchant-scoped audit ledger reads."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.auth import optional_principal
from apps.api.envelope import success
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal
from services.audit.repository import list_events

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


def _get_optional_db():
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
    principal: Principal | None = Depends(optional_principal),
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    event_type: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="desc", pattern="^(asc|desc|ASC|DESC)$"),
) -> dict[str, Any]:
    merchant_id = (principal.merchant_id if principal else None) or "merchant_demo"
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
    aggregate_type: str, aggregate_id: str, principal: MerchantPrincipal, session: DatabaseSession
) -> dict[str, Any]:
    return success(
        {
            "events": list_events(
                session,
                merchant_id=principal.merchant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
            )
        }
    )
