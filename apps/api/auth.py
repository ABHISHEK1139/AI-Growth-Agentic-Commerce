"""Authentication and authorization as FastAPI dependencies (Requirement 24.1).

Two credential families reach this module, and they stay apart:

* **The web surface** presents a signed session cookie. ``HttpOnly`` so no script
  can read it, ``SameSite=Lax`` so a cross-site form post cannot ride it, and
  ``Secure`` everywhere except local development, where the frontend is plain
  ``http://localhost`` and a secure cookie would never be sent at all.
* **The public agent surface** presents ``Authorization: Bearer <token>``, a
  short-lived scoped credential obtained by exchanging an API key. No session, no
  privileged header (Requirement 20.5).

A route declares what it needs and gets a :class:`~packages.security.principals.Principal`::

    @router.post("/agent/checkout")
    async def create_checkout(
        principal: Principal = Depends(require_scopes(Scope.CHECKOUT_WRITE)),
    ) -> dict[str, Any]: ...

Three deliberate choices worth naming.

**A bearer header is never allowed to fall back to the cookie.** If a caller
presents a token and it does not verify, the request is refused. Falling through
to an ambient session would mean a broken agent credential silently borrowing
whatever browser session happened to be attached — the classic confused-deputy
shape.

**Every dependency here is ``async``.** FastAPI runs a synchronous dependency in a
threadpool with a *copy* of the context, so a correlation identifier bound there
would be discarded before the endpoint ran. Async keeps the binding in the request's
own context, which is how ``actor_id`` reaches the log lines that follow.

**A failed credential is logged with a reason and no credential.** The reason code
comes from the typed token error; the token itself never reaches a log call. The
encoded form also begins with ``eyJ``, which the formatter's redactor already masks,
so an accidental interpolation somewhere else is still covered.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request, Response

from apps.api.config import Settings, get_settings
from packages.errors.exceptions import ForbiddenError, UnauthenticatedError
from packages.observability.context import set_ids
from packages.observability.logging import get_logger
from packages.security import authorization
from packages.security.apikeys import ApiClientRegistry
from packages.security.principals import AuthMethod, Principal, Role, Scope
from packages.security.tokens import (
    IssuedToken,
    TokenError,
    issue_access_token,
    issue_session_token,
    principal_from_access_token,
    principal_from_session_token,
)

logger = get_logger(__name__)

AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "
SESSION_COOKIE_NAME = "agentpay_session"

#: Where the resolved principal is cached for the life of the request, so several
#: dependencies on one route verify the credential once.
_PRINCIPAL_STATE_KEY = "principal"

PrincipalDependency = Callable[[Request], Awaitable[Principal]]


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def install_auth(app: Any, settings: Settings) -> None:
    """Attach the API client registry to the application.

    In-memory for now, holding nothing. Task 9 owns the ``api_client`` table; the
    exchange below depends only on :meth:`ApiClientRegistry.resolve`, so that swap
    replaces one object on ``app.state`` and touches no authentication code.
    """
    del settings  # nothing configuration-dependent yet; kept for a stable signature
    app.state.api_client_registry = ApiClientRegistry()


def settings_for(request: Request) -> Settings:
    """Settings for this request. Falls back to the process singleton.

    Every route that reads configuration must come through here rather than
    calling ``get_settings()``. The singleton is cached for the life of the
    process and is built from the environment, so a route that calls it ignores
    whatever settings the application was actually constructed with — which is how
    a probe that passed a local model endpoint to ``create_app`` still sent its
    request to the hosted provider named in ``.env``, and billed for it.
    """
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()  # pragma: no cover - only when an app was built by hand


#: The route-parameter spelling of :func:`settings_for`. A route declares
#: ``settings: AppSettings`` and receives the object the application was built
#: with, so configuring a test application never means mutating the environment.
AppSettings = Annotated[Settings, Depends(settings_for)]


def registry_for(request: Request) -> ApiClientRegistry:
    registry = getattr(request.app.state, "api_client_registry", None)
    if not isinstance(registry, ApiClientRegistry):
        raise RuntimeError("api client registry is not installed on the application")
    return registry


# ---------------------------------------------------------------------------
# Credential extraction
# ---------------------------------------------------------------------------


def bearer_token(request: Request) -> str | None:
    """The bearer token from the ``Authorization`` header, if one is present.

    The scheme is matched case-insensitively. If an Authorization header is present
    with an unsupported scheme (e.g., Basic) or malformed format, reject it with
    UnauthenticatedError rather than silently ignoring it.
    """
    header = request.headers.get(AUTHORIZATION_HEADER)
    if not header:
        return None
    scheme, sep, value = header.partition(" ")
    if not sep or scheme.lower() != "bearer":
        raise UnauthenticatedError(
            f"Unsupported authorization scheme '{scheme}'. Only Bearer tokens are accepted.",
            details={"scheme": scheme, "reason": "unsupported_scheme"},
        )
    token = value.strip()
    if not token:
        raise UnauthenticatedError(
            "Bearer token value is missing in Authorization header.",
            details={"reason": "empty_bearer_token"},
        )
    return token


def session_cookie(request: Request) -> str | None:
    if SESSION_COOKIE_NAME not in request.cookies:
        return None
    value = request.cookies[SESSION_COOKIE_NAME]
    if not value or not value.strip():
        raise UnauthenticatedError(
            "Session cookie is empty or malformed.",
            details={"reason": "empty_session_cookie"},
        )
    return value.strip()


def _log_auth_failure(request: Request, exc: UnauthenticatedError, *, source: str) -> None:
    logger.warning(
        "authentication failed",
        extra={
            "event": "AUTHENTICATION_FAILED",
            "error_code": exc.code.value,
            "reason": exc.details.get("reason", "unknown"),
            "credential_source": source,
            "method": request.method,
            "path": request.url.path,
            "outcome": "denied",
        },
    )


def _cache(request: Request, principal: Principal) -> Principal:
    request.scope.setdefault("state", {})[_PRINCIPAL_STATE_KEY] = principal
    # Bound here so every log line for the rest of the request carries the actor,
    # including the ones a service emits deep in the call stack. Not reset: the
    # request's context dies with its task.
    set_ids(actor_id=principal.subject)
    return principal


def _cached(request: Request) -> Principal | None:
    cached = (request.scope.get("state") or {}).get(_PRINCIPAL_STATE_KEY)
    return cached if isinstance(cached, Principal) else None


def resolve_principal(request: Request) -> Principal | None:
    """The authenticated caller, or ``None`` if no credential was presented.

    Raises :class:`~packages.errors.exceptions.UnauthenticatedError` when a
    credential *was* presented and did not verify — including an expired one
    (Requirement 24.7).
    """
    cached = _cached(request)
    if cached is not None:
        return cached

    settings = settings_for(request)

    token = bearer_token(request)
    if token is not None:
        try:
            principal = principal_from_access_token(token, secret=settings.jwt_secret)
        except TokenError as exc:
            _log_auth_failure(request, exc, source="bearer")
            raise
        return _cache(request, principal)

    cookie = session_cookie(request)
    if cookie is not None:
        try:
            principal = principal_from_session_token(cookie, secret=settings.session_secret)
        except TokenError as exc:
            _log_auth_failure(request, exc, source="session")
            raise
        return _cache(request, principal)

    return None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def optional_principal(request: Request) -> Principal | None:
    """For a route that behaves differently when signed in, but is public."""
    return resolve_principal(request)


async def current_principal(request: Request) -> Principal:
    """Any authenticated caller, by session or by token."""
    principal = resolve_principal(request)
    if principal is None:
        raise UnauthenticatedError(details={"reason": "missing"})
    return principal


async def session_principal(request: Request) -> Principal:
    """A caller authenticated by session cookie specifically.

    Used by the merchant console. A long-lived agent credential must not be able to
    drive an administrative screen even when its role would allow the action.
    """
    principal = await current_principal(request)
    if principal.method is not AuthMethod.SESSION:
        raise ForbiddenError(
            "This action requires a signed-in session.",
            details={"reason": "session_required"},
        )
    return principal


async def token_principal(request: Request) -> Principal:
    """A caller authenticated by bearer token specifically (the agent surface)."""
    principal = await current_principal(request)
    if principal.method is not AuthMethod.TOKEN:
        raise ForbiddenError(
            "This surface requires a scoped access token.",
            details={"reason": "token_required"},
        )
    return principal


def _log_denied(request: Request, principal: Principal, exc: ForbiddenError) -> None:
    logger.warning(
        "authorization denied",
        extra={
            "event": "AUTHORIZATION_DENIED",
            "error_code": exc.code.value,
            "reason": exc.details.get("reason", "unknown"),
            "method": request.method,
            "path": request.url.path,
            "outcome": "denied",
            **principal.as_log_fields(),
        },
    )


def require_scopes(*scopes: Scope) -> PrincipalDependency:
    """Dependency requiring every scope in ``scopes`` (Requirement 24.2).

    A missing scope is a 403 ``FORBIDDEN``, not a 401: the caller authenticated
    successfully, and presenting the same credential again will not help.
    """
    if not scopes:
        raise ValueError("require_scopes needs at least one scope")

    async def dependency(request: Request) -> Principal:
        principal = await current_principal(request)
        try:
            authorization.require_scopes(principal, *scopes)
        except ForbiddenError as exc:
            _log_denied(request, principal, exc)
            raise
        return principal

    return dependency


def require_roles(*roles: Role) -> PrincipalDependency:
    """Dependency requiring one of ``roles``."""
    if not roles:
        raise ValueError("require_roles needs at least one role")

    async def dependency(request: Request) -> Principal:
        principal = await current_principal(request)
        try:
            authorization.require_role(principal, *roles)
        except ForbiddenError as exc:
            _log_denied(request, principal, exc)
            raise
        return principal

    return dependency


def require_session_roles(*roles: Role) -> PrincipalDependency:
    """Dependency requiring a session *and* one of ``roles``. The console default."""
    if not roles:
        raise ValueError("require_session_roles needs at least one role")

    async def dependency(request: Request) -> Principal:
        principal = await session_principal(request)
        try:
            authorization.require_role(principal, *roles)
        except ForbiddenError as exc:
            _log_denied(request, principal, exc)
            raise
        return principal

    return dependency


# ---------------------------------------------------------------------------
# API key exchange
# ---------------------------------------------------------------------------


def exchange_api_key(
    api_key: str,
    *,
    registry: ApiClientRegistry,
    settings: Settings,
    requested_scopes: frozenset[Scope] | None = None,
    now: float | None = None,
) -> IssuedToken:
    """Exchange a long-lived API key for a short-lived scoped bearer token.

    The two failure modes are answered differently on purpose. An unknown or
    revoked key is 401: the credential itself is no good. A known key asking for
    scopes it does not hold is 403: the credential is fine, the request is not.

    Requested scopes are validated strictly against the client's granted scopes.
    If requested scopes contain any scope not permitted for the client, the request
    is rejected with ForbiddenError (403). Asking for *nothing* it holds is a 403
    rather than a token that can do nothing, because a credential that silently grants
    nothing is a support ticket.

    The route that will call this (``POST /agent/auth/token``, Task 25) already has
    the tightest rate-limit rule in the table, because this is the brute-force
    surface.
    """
    client = registry.resolve(api_key)
    if client is None:
        raise UnauthenticatedError("The API key is not valid.", details={"reason": "api_key"})

    if requested_scopes is not None:
        requested_set = frozenset(requested_scopes)
        excess = requested_set - client.scopes
        if excess:
            raise ForbiddenError(
                f"Requested scopes {sorted(s.value for s in excess)} exceed granted scopes for this client.",
                details={
                    "reason": "scope_exceeded",
                    "requested": sorted(s.value for s in requested_set),
                    "permitted": sorted(s.value for s in client.scopes),
                    "excess": sorted(s.value for s in excess),
                },
            )
        granted = requested_set
    else:
        granted = client.scopes

    if not granted:
        raise ForbiddenError(
            "This credential does not carry the scope required for this action.",
            details={
                "reason": "scope",
                "granted_scopes": sorted(scope.value for scope in client.scopes),
            },
        )

    issued = issue_access_token(
        secret=settings.jwt_secret,
        # A buyer-bound client authenticates *as* its buyer, so ownership checks and
        # the audit trail see the buyer rather than the machine.
        subject=client.buyer_id or client.client_id,
        role=client.role,
        merchant_id=client.merchant_id,
        buyer_id=client.buyer_id,
        scopes=granted,
        ttl_seconds=settings.access_token_ttl_seconds,
        client_id=client.client_id,
        now=now,
    )
    logger.info(
        "access token issued",
        extra={
            "event": "ACCESS_TOKEN_ISSUED",
            "outcome": "success",
            "scopes": sorted(scope.value for scope in granted),
            "ttl_seconds": settings.access_token_ttl_seconds,
            **client.as_log_fields(),
        },
    )
    return issued


def token_response_payload(issued: IssuedToken) -> dict[str, Any]:
    """The body of a token exchange response.

    Shaped like an OAuth token response because every agent HTTP client already
    knows that shape, and carries the scope list so a caller can assert what it got
    rather than assume.
    """
    return {
        "access_token": issued.token,
        "token_type": "Bearer",
        "expires_in": issued.expires_in,
        "expires_at": issued.expires_at,
        "scopes": sorted(scope.value for scope in issued.principal.scopes),
    }


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------


def start_session(
    response: Response,
    *,
    settings: Settings,
    subject: str,
    role: Role,
    merchant_id: str,
    buyer_id: str | None = None,
    now: float | None = None,
) -> IssuedToken:
    """Issue a session and set it as a cookie on ``response``."""
    issued = issue_session_token(
        secret=settings.session_secret,
        subject=subject,
        role=role,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        ttl_seconds=settings.session_ttl_seconds,
        now=now,
    )
    set_session_cookie(response, issued.token, settings=settings)
    return issued


def set_session_cookie(response: Response, token: str, *, settings: Settings) -> None:
    """Write the session cookie with the attributes that make it a session cookie.

    ``httponly`` keeps it out of reach of any script, which is what stops an XSS
    from becoming an account takeover. ``samesite=lax`` blocks a cross-site POST
    from riding it while still allowing an ordinary top-level navigation back into
    the app. ``secure`` is on everywhere but local development.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, *, settings: Settings) -> None:
    """Sign out. The attributes must match the ones used to set it, or browsers
    keep the original cookie."""
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
