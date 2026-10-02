"""Security response headers for every API response.

Why these were missing
----------------------
The API previously set no security headers at all. Verified: nothing in
``apps/api`` or ``apps/web/next.config.js`` emitted ``Content-Security-Policy``,
``Strict-Transport-Security``, ``X-Frame-Options``, ``X-Content-Type-Options``, or
``Referrer-Policy``. For an API that returns session-bearing JSON and handles payment
state, that leaves the browser to infer the policy from defaults.

What each header does here
--------------------------
``X-Content-Type-Options: nosniff``
    Stops a browser from re-interpreting a JSON response as HTML or script. JSON
    endpoints are the ones most at risk: a value that reaches a response body and is
    later written into a document would otherwise be sniffable as script.

``X-Frame-Options: DENY``
    The API has no business being framed. Clickjacking a payment confirmation page
    needs no XSS, so this is cheap and unconditional.

``Referrer-Policy: no-referrer``
    URLs here carry identifiers in the path and query. Without this, a link out of a
    payment page leaks the path to the destination.

``Strict-Transport-Security``
    Sent only when the request already arrived over HTTPS. Emitting HSTS over plain
    HTTP is ignored by browsers, and asserting it unconditionally would let a
    misconfigured proxy pin users to https for a host that never offered it.
    ``max-age`` starts at one year, which is the value that actually gets preload
    eligibility; includeSubDomains is deliberately off, since a subdomain not served
    over TLS would become unreachable once the parent is pinned.

``Cache-Control: no-store`` on responses that are per-user
    A shared cache in front of the API must not retain merchant or buyer JSON.
    Scoped to the authenticated surface so that ``/health`` stays cacheable — a probe
    that a cache serves stale is a probe that lies.

On Content-Security-Policy
--------------------------
Deliberately **not** set here, and this is a decision rather than an omission.

A CSP is only meaningful as a header on the document that loads scripts, which is the
Next.js frontend, not the JSON API. Setting it on ``/api/*`` would protect nothing and
invite the false impression that the app is covered.

The frontend genuinely cannot take a strict policy yet: ``apps/web/src/app/layout.tsx``
loads ``https://checkout.razorpay.com/v1/checkout.js`` and the checkout page mounts
Razorpay's script, whose internals inject inline scripts and styles at runtime. A
policy without ``unsafe-inline`` breaks checkout; one with it largely defeats the
purpose. Moving to a nonce-based policy requires Razorpay's script to cooperate, which
is not something this codebase controls.

So the correct sequence is: land the headers that are unambiguous now, then introduce a
nonce-based CSP on the web app when the checkout flow can be verified against it. That
is tracked in ``docs/production/READINESS_PLAN.md`` rather than silently skipped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

#: Paths whose responses describe per-user or per-merchant state and must never be
#: written to a shared cache. Anything unauthenticated (``/health``) is left cacheable
#: on purpose so a liveness probe can be cached without lying about liveness.
_PRIVATE_PATH_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/auth/",
    "/merchant",
    "/me",
    "/checkout",
    "/payments",
    "/orders",
    "/offers",
    "/agent",
    "/api-keys",
)

_HSTS_MAX_AGE_SECONDS = 31_536_000  # one year, the threshold for preload eligibility


def _is_private_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in _PRIVATE_PATH_PREFIXES)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds the response headers that are safe to assert unconditionally.

    Installed inside :class:`~apps.api.middleware.context.RequestContextMiddleware` and
    outside the routers, so it applies to success responses, error envelopes, rate-limit
    responses, and anything the exception handler renders alike. A header that only
    appears on 200s is not a security control.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")

        # Only meaningful once the connection is already TLS. Sending it over HTTP is
        # ignored, so gating avoids claiming protection that is not there.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={_HSTS_MAX_AGE_SECONDS}",
            )

        if _is_private_path(request.url.path):
            response.headers.setdefault("Cache-Control", "no-store")

        return response
