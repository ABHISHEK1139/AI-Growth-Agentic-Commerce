"""Exception to envelope translation (design: "Error Handling", middleware row).

Domain exceptions are mapped to the registry: the code the service raised decides
the HTTP status, the retryable flag, and the default message. Unexpected
exceptions become ``INTERNAL_ERROR`` with the detail logged and never returned —
an exception string routinely contains a DSN, a bound SQL parameter, or a provider
payload, and a stack trace is a map of the codebase.

Two mechanisms, because they cover different ground:

* Registered handlers for the exception types a route can raise. These run inside
  Starlette's ``ExceptionMiddleware``, so the response they produce still passes
  back out through the context middleware and picks up the correlation headers.
* :class:`UnhandledExceptionMiddleware` for anything that escapes a handler,
  including failures inside another middleware. Without it, an unexpected
  exception would be answered by Starlette's ``ServerErrorMiddleware``, which sits
  *above* our middleware — the response would carry no envelope, no
  ``X-Request-ID``, and the correlation scope would already be gone by the time
  it was logged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from apps.api.envelope import error_response, error_response_from_domain_error
from apps.api.middleware.context import record_error_code
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode, code_for_status
from packages.observability.logging import get_logger

logger = get_logger(__name__)


def _log_domain_error(request: Request, exc: DomainError) -> None:
    """A domain error is an expected outcome, so it is a warning, not an error.

    ``details`` goes through the redacting formatter like any other extra.
    """
    logger.warning(
        "domain error",
        extra={
            "event": "DOMAIN_ERROR",
            "error_code": exc.code.value,
            "status": exc.http_status,
            "retryable": exc.retryable,
            "method": request.method,
            "path": request.url.path,
            "details": exc.details,
        },
    )


async def domain_error_handler(request: Request, exc: Exception) -> Response:
    """Map a service's typed error onto its registry entry."""
    if not isinstance(exc, DomainError):  # pragma: no cover - defensive
        return await unexpected_error_handler(request, exc)

    record_error_code(request, exc.code.value)
    _log_domain_error(request, exc)
    return error_response_from_domain_error(exc)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """Give framework-raised ``HTTPException``s the same envelope.

    Starlette raises these itself for an unmatched route or an unsupported
    method, so without this an external buyer would meet two different error
    shapes depending on whether it mistyped a path or hit a real failure.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover - defensive
        return await unexpected_error_handler(request, exc)

    code = code_for_status(exc.status_code)
    record_error_code(request, code.value)
    # `detail` is authored by us or by the framework, never by the client, so it
    # is safe to return. It is only used when it is a plain string.
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else None
    return error_response(
        code,
        message=message,
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def validation_error_handler(request: Request, exc: Exception) -> Response:
    """Report *where* the request was invalid without echoing what was sent.

    Pydantic's error list includes the offending input value. On a payment or
    address payload that is buyer data, so only the location, the type, and the
    message survive into the response.
    """
    if not isinstance(exc, RequestValidationError):  # pragma: no cover - defensive
        return await unexpected_error_handler(request, exc)

    record_error_code(request, ErrorCode.VALIDATION_ERROR.value)
    fields: list[dict[str, Any]] = [
        {
            "location": [str(part) for part in error.get("loc", ())],
            "type": str(error.get("type", "")),
            "message": str(error.get("msg", "")),
        }
        for error in exc.errors()
    ]
    logger.warning(
        "request validation failed",
        extra={
            "event": "REQUEST_VALIDATION_FAILED",
            "error_code": ErrorCode.VALIDATION_ERROR.value,
            "method": request.method,
            "path": request.url.path,
            "field_count": len(fields),
        },
    )
    return error_response(ErrorCode.VALIDATION_ERROR, details={"fields": fields})


async def unexpected_error_handler(request: Request, exc: Exception) -> Response:
    """Last resort: log everything, return nothing but the code."""
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    if isinstance(exc, OperationalError | InterfaceError | DBAPIError):
        record_error_code(request, ErrorCode.SERVICE_UNAVAILABLE.value)
        logger.error(
            "database unavailable",
            exc_info=exc,
            extra={
                "event": "DATABASE_UNAVAILABLE",
                "error_code": ErrorCode.SERVICE_UNAVAILABLE.value,
                "exception_type": type(exc).__name__,
                "method": request.method,
                "path": request.url.path,
            },
        )
        return error_response(ErrorCode.SERVICE_UNAVAILABLE, status_code=503)

    record_error_code(request, ErrorCode.INTERNAL_ERROR.value)
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={
            "event": "UNHANDLED_EXCEPTION",
            "error_code": ErrorCode.INTERNAL_ERROR.value,
            "exception_type": type(exc).__name__,
            "method": request.method,
            "path": request.url.path,
        },
    )
    return error_response(ErrorCode.INTERNAL_ERROR)


class UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    """Catch what the registered handlers cannot see."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except DomainError as exc:
            # A domain error raised outside a route — from a dependency or another
            # middleware — still deserves its registry answer.
            record_error_code(request, exc.code.value)
            _log_domain_error(request, exc)
            return error_response_from_domain_error(exc)
        except Exception as exc:  # noqa: BLE001 - this is the boundary
            return await unexpected_error_handler(request, exc)


def install_exception_handlers(app: FastAPI) -> None:
    """Register the envelope handlers on an application."""
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
