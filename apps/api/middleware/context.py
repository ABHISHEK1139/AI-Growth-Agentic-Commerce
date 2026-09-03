"""Request identity, correlation binding, and the access log line.

Three things happen here, in this order, for every request:

1. A ``request_id`` and ``trace_id`` are established. An inbound
   ``X-Request-ID`` is honoured only if it is well formed, because the value
   lands in every log line for the request and an unvalidated header is a log
   injection vector — a caller could send newlines, ANSI escapes, or a megabyte
   of text and shape what an operator reads.
2. Both identifiers are bound with
   :func:`packages.observability.context.correlation_scope`, so every log line
   emitted downstream carries them without anyone passing them around.
3. Exactly one structured line is emitted per request, carrying method, path,
   status, ``latency_ms``, outcome, and the error code when there was one.

What is deliberately *not* logged: request and response bodies, and the query
string. Bodies contain buyer addresses and, on the payment endpoints, amounts;
the audit ledger is where transaction facts belong, with the controls to match.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from packages.observability.context import correlation_scope, new_id
from packages.observability.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"

#: Conservative on purpose: opaque token characters only, bounded length. Prefixed
#: identifiers this system generates (``req_1a2b...``) match; anything carrying a
#: space, a control character, or a newline does not.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

#: Key under ``scope["state"]`` where a handler records the code it answered with,
#: so the access log can report it. ``scope`` is shared by every middleware layer
#: and by the exception handlers, which is what makes this work across them.
ERROR_CODE_STATE_KEY = "error_code"


def sanitize_inbound_id(value: str | None) -> str | None:
    """Return ``value`` if it is a usable identifier, otherwise ``None``.

    Rejecting rather than sanitizing is the right call here: a caller that sends a
    malformed identifier gets a fresh one and can still correlate through the
    echoed response header.
    """
    if not value:
        return None
    candidate = value.strip()
    if not _ID_PATTERN.match(candidate):
        return None
    return candidate


def outcome_for_status(status_code: int) -> str:
    if status_code < 400:
        return "success"
    if status_code == 429:
        return "rate_limited"
    if status_code < 500:
        return "client_error"
    return "server_error"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Outermost of our middleware: identity in, identity out, one log line."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = sanitize_inbound_id(request.headers.get(REQUEST_ID_HEADER)) or new_id("req")
        trace_id = sanitize_inbound_id(request.headers.get(TRACE_ID_HEADER)) or new_id("trace")

        started = time.perf_counter()
        with correlation_scope(request_id=request_id, trace_id=trace_id):
            response = await call_next(request)

            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers[TRACE_ID_HEADER] = trace_id

            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            state = request.scope.get("state") or {}
            error_code = state.get(ERROR_CODE_STATE_KEY)

            logger.info(
                "request completed",
                extra={
                    "event": "REQUEST_COMPLETED",
                    "method": request.method,
                    # Path only. A query string can carry a buyer's search terms.
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "outcome": outcome_for_status(response.status_code),
                    **({"error_code": error_code} if error_code else {}),
                },
            )

            return response


def record_error_code(request: Request, code: str) -> None:
    """Tell the access log which registry code this request answered with."""
    request.scope.setdefault("state", {})[ERROR_CODE_STATE_KEY] = code
