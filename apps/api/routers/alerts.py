"""Operations alerts endpoint (Phase 9).

Exposes the in-process :class:`~services.operations.alerts.AlertManager` over
HTTP so a dashboard or a sidecar can drain it. The endpoint requires platform
admin auth; this is an operator surface, not a buyer one.

The route intentionally does *not* call PagerDuty / Slack / email from inside
the API process. Forwarding to an external channel is the job of a separate
process, and pulling alerts out of the API is the right hand-off: it keeps
the API free of third-party SaaS dependencies, and a misconfigured external
forwarder can never spend a real payment endpoint.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from apps.api.envelope import success
from packages.security.principals import Principal, Role
from services.operations.alerts import alerts

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

PlatformPrincipal = Annotated[
    Principal,
    Depends(
        __import__("apps.api.auth", fromlist=["require_roles"]).require_roles(Role.PLATFORM_ADMIN)
    ),
]


@router.get(
    "/alerts",
    summary="Read the in-process alert queue without draining it",
)
def peek_alerts(
    principal: PlatformPrincipal,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Return the most recent ``limit`` alerts queued in this process.

    The queue is *not* drained; the caller is expected to forward the
    payload onward and then call :func:`drain_alerts` to clear it.
    """
    pending = alerts().peek()[-limit:]
    return success(
        {
            "alerts": [a.to_dict() for a in pending],
            "dropped_count": alerts().dropped_count(),
        }
    )


@router.post(
    "/alerts/drain",
    summary="Atomically clear the in-process alert queue",
)
def drain_alerts(principal: PlatformPrincipal) -> dict[str, Any]:
    """Remove and return every queued alert.

    An external forwarder calls this *after* it has successfully delivered
    the alerts to its downstream channel. If delivery is uncertain, the
    forwarder should not call drain: better to double-deliver a critical
    alert than to lose one.
    """
    drained = alerts().drain()
    return success(
        {
            "drained_count": len(drained),
            "alerts": [a.to_dict() for a in drained],
        }
    )
