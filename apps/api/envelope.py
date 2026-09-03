"""Envelope serialization for the HTTP surface.

The models in :mod:`packages.schemas.envelope` are pure. This module is the thin
transport layer over them: it stamps the ``request_id`` from the correlation
context and turns an envelope into a ``JSONResponse`` with the status the error
registry prescribes.

Routers call :func:`success` (or hand back a ``ServiceResult``); the middleware
and exception handlers call :func:`error_response`. Nothing else builds a body by
hand, which is what keeps the two shapes uniform across a dozen routers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi.responses import JSONResponse

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode, spec_for
from packages.observability.context import current_ids
from packages.schemas.envelope import (
    EnvelopeWarning,
    ErrorBody,
    ErrorEnvelope,
    Evidence,
    NextAction,
    ServiceResult,
    SuccessEnvelope,
)


def current_request_id() -> str | None:
    """The request identifier in scope, or ``None`` outside a request."""
    return current_ids().request_id


def success(
    data: Mapping[str, Any] | None = None,
    *,
    warnings: Sequence[EnvelopeWarning] | None = None,
    evidence: Sequence[Evidence] | None = None,
    next_actions: Sequence[NextAction] | None = None,
) -> dict[str, Any]:
    """A success envelope as a plain dict, for a router to return directly."""
    envelope = SuccessEnvelope(
        request_id=current_request_id(),
        data=dict(data or {}),
        warnings=list(warnings or []),
        evidence=list(evidence or []),
        next_actions=list(next_actions or []),
    )
    return envelope.to_payload()


def from_service_result(result: ServiceResult) -> dict[str, Any]:
    """Serialize what a service returned, adding the request identifier."""
    return result.to_envelope(request_id=current_request_id()).to_payload()


def error_payload(
    code: ErrorCode,
    *,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
    next_actions: Sequence[NextAction] | None = None,
) -> dict[str, Any]:
    """An error envelope as a plain dict."""
    envelope = ErrorEnvelope(
        request_id=current_request_id(),
        error=ErrorBody.from_code(code, message=message, details=dict(details or {})),
        next_actions=list(next_actions or []),
    )
    return envelope.to_payload()


def error_response(
    code: ErrorCode,
    *,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
    next_actions: Sequence[NextAction] | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """An error envelope as a response, with the registry's status.

    ``status_code`` is an override for the rare case where a caller already knows
    the status (a mapped ``HTTPException``); by default the registry decides, so
    the same code cannot arrive as 409 from one router and 400 from another.
    """
    resolved_status = status_code if status_code is not None else spec_for(code).http_status
    return JSONResponse(
        status_code=resolved_status,
        content=error_payload(code, message=message, details=details, next_actions=next_actions),
        headers=dict(headers) if headers else None,
    )


def error_response_from_domain_error(exc: DomainError) -> JSONResponse:
    """Render a service's typed error. Status and retryable come from its code."""
    return error_response(
        exc.code,
        message=exc.message,
        details=exc.details,
        next_actions=exc.next_actions,
        status_code=exc.http_status,
    )


def probe_payload(
    *,
    ok: bool,
    data: Mapping[str, Any],
    warnings: Sequence[EnvelopeWarning] | None = None,
) -> dict[str, Any]:
    """Envelope for a health probe.

    The one documented departure from "``ok: false`` implies an ``error``": a
    readiness probe reports per-component status in ``data`` so an orchestrator
    can tell *which* dependency is down, and ``ok`` answers the only question the
    probe was asked. Collapsing it into an error envelope would hide the detail
    that makes the probe useful.
    """
    payload: dict[str, Any] = {
        "ok": ok,
        "request_id": current_request_id(),
        "data": dict(data),
        "warnings": [w.model_dump(mode="json") for w in (warnings or [])],
        "evidence": [],
        "next_actions": [],
    }
    return payload
