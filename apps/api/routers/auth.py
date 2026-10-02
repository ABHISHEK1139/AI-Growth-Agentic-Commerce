"""Merchant console logins: password verification, session issuance, and signup.

The endpoint this replaces took a ``role`` in the request body and issued a
session for it::

    POST /api/v1/auth/login  {"role": "platform_admin"}  -> 200, signed session

Any anonymous caller could therefore become a platform administrator. The
replacement checks a stored Argon2id hash, derives the role from the stored row
rather than the request, and refuses the no-credential path outside local.

What each route is for:

* ``POST /api/v1/auth/login`` — verify a credential and set the session cookie.
* ``POST /api/v1/auth/logout`` — clear it.
* ``GET  /api/v1/auth/me`` — who am I, without raising when anonymous.
* ``POST /api/v1/auth/change-password`` — set a new password, requiring the
  current one. Exists because ``must_change_password`` is set on an account
  created by someone else, and that account cannot clear the flag without it.
* ``POST /api/v1/auth/demo-session`` — the old no-credential shortcut, moved to
  its own path, refused outside local, and rate-limited by
  :mod:`apps.api.middleware.ratelimit` like any other login.

The demo path stays because the e2e suite and a demo deployment depend on a
zero-credential console, and removing it would mean re-seeding an account for
every test. What changes is that it is not the login endpoint: a deployment
cannot accidentally leave "log in as platform admin" mounted at the URL a human
types.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from apps.api.auth import (
    clear_session_cookie,
    current_principal,
    exchange_api_key,
    registry_for,
    session_principal,
    settings_for,
    start_session,
    token_response_payload,
)
from apps.api.db import get_db
from apps.api.envelope import success
from apps.api.middleware.ratelimit import RateLimitRule, declare_route_limit
from packages.errors.exceptions import (
    ForbiddenError,
    UnauthenticatedError,
    ValidationError,
)
from packages.security.apikeys import MAX_API_KEY_LENGTH
from packages.security.passwords import password_problem
from packages.security.principals import Principal, Role, Scope
from services.connectors.models import OperatorAccount
from services.connectors.operator_auth import (
    LoginError,
    authenticate,
    create_operator,
    find_by_email,
    set_password,
)

router = APIRouter(tags=["auth"])

#: Tighter than the default per-route budget for both login-shaped routes. The
#: default exists to stop a runaway client; this exists to make online password
#: guessing cost an attacker hours rather than minutes. The per-account lockout
#: in :mod:`services.connectors.operator_auth` is the second half of the answer,
#: because an IP limit alone does nothing against a botnet or an attacker who
#: rotates one address per guess.
_LOGIN_LIMIT = RateLimitRule(limit=10, window_seconds=300)

#: Generous, because this endpoint is refused outside `APP_ENV=local` and so is
#: not reachable in any deployment that matters -- the guard is the real control,
#: and this number only exists to stop a runaway local loop.
#:
#: It was previously 20 per 5 minutes, which was too tight to *use*: a single
#: local page load can mint a session, a developer switching between browser tabs
#: mints several, and the e2e suite mints one per worker. The visible symptom was
#: a console that appeared to lose its session, because the storefront's
#: `bootstrapSession` fell through on a 429 and the operator landed on `/login`.
#: Tightening a limit until the product misbehaves is not a security control.
_DEMO_SESSION_LIMIT = RateLimitRule(limit=120, window_seconds=300)

declare_route_limit("POST", "/api/v1/auth/login", _LOGIN_LIMIT)
declare_route_limit("POST", "/api/v1/auth/signup", _LOGIN_LIMIT)
declare_route_limit("POST", "/api/v1/auth/change-password", _LOGIN_LIMIT)
declare_route_limit("POST", "/api/v1/auth/demo-session", _DEMO_SESSION_LIMIT)

# `GET /api/v1/auth/me` is a *read* of the caller's own session, and it is
# exempted rather than merely loosened.
#
# Two clients call it on every console navigation: the web tier's middleware and
# the `useSession` hook. Under the default 120/minute that is comfortable for a
# human, but the endpoint is also the answer to "am I still signed in", so
# throttling it produces a *false negative* -- a 429 that reads as "signed out" and
# logs an operator out of a valid session. A session check that can lie is worse
# than one that is merely slow, and it cannot be brute-forced: it accepts no
# attacker-chosen input and returns nothing about anyone but the caller.
declare_route_limit(
    "GET",
    "/api/v1/auth/me",
    RateLimitRule(limit=600, window_seconds=60),
)

#: One deliberate message for every failure mode. A different message for "no
#: such account" than for "wrong password" turns this endpoint into a way to
#: enumerate who has access to a merchant's console.
INVALID_CREDENTIALS_MESSAGE = "Email or password is incorrect."


def _check_email(value: str) -> str:
    """Shape check, then normalise.

    Pydantic's ``EmailStr`` was the obvious choice and is wrong here twice over.
    It validates with `email-validator`, which rejects special-use and reserved
    TLDs -- so ``admin@merchant.local`` and any ``.test`` or ``.internal``
    address, all of which are exactly what a self-hosted or staging deployment
    uses, cannot sign in at all. And it does not normalise case, so
    ``Admin@Example.com`` and ``admin@example.com`` pass validation and then miss
    each other in the database.

    The check here is deliberately shallow: one ``@``, something either side, no
    whitespace. It is a typo guard, not an address validator, and the unique index
    on the column is the real guarantee.
    """
    normalized = value.strip().lower()
    local, separator, domain = normalized.rpartition("@")
    if not separator or not local or not domain or "." not in domain:
        raise ValueError("must be an email address")
    if any(char.isspace() for char in normalized):
        raise ValueError("must not contain whitespace")
    return normalized


def _password_is_acceptable(value: SecretStr) -> SecretStr:
    problem = password_problem(value.get_secret_value())
    if problem is not None:
        raise ValueError(problem)
    return value


class TokenExchangeRequest(BaseModel):
    """A key exchange request.

    ``SecretStr`` keeps the API key out of model representations and validation
    diagnostics. Unknown fields are refused so a misspelled scope field cannot
    silently produce a broader default token than the caller intended.
    """

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=MAX_API_KEY_LENGTH)
    scopes: list[Scope] | None = None


class LoginRequest(BaseModel):
    """An email and password.

    Both are ``SecretStr``-adjacent: the password must never appear in a
    validation error body or a repr that reaches a log.
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)

    _email = field_validator("email")(_check_email)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=1, max_length=1024)


class SignupRequest(BaseModel):
    """A self-service merchant registration.

    Open registration is off by default; ``ALLOW_CONSOLE_SIGNUP`` has to be set
    for this route to accept anyone. An always-open signup on a payments
    gateway is a way for anyone to obtain a tenant, and a tenant is a place to
    push a catalog, issue agent API keys, and receive orders.
    """

    model_config = ConfigDict(extra="forbid")

    merchant_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    merchant_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=1024)

    _email = field_validator("email")(_check_email)
    _password = field_validator("password")(_password_is_acceptable)


class DemoSessionRequest(BaseModel):
    """A no-credential session, for local development and the e2e suite only."""

    model_config = ConfigDict(extra="forbid")

    role: Role = Field(default=Role.MERCHANT_ADMIN)
    merchant_id: str | None = None
    buyer_id: str | None = None
    subject: str | None = None


def _principal_payload(principal: Principal) -> dict[str, Any]:
    return {
        "subject": principal.subject,
        "role": principal.role.value,
        "merchant_id": principal.merchant_id,
        "buyer_id": principal.buyer_id,
        "scopes": sorted(scope.value for scope in principal.scopes),
    }


@router.post(
    "/api/v1/agent/auth/token",
    summary="Exchange an API key for a scoped bearer token",
    tags=["agent-auth"],
)
def exchange_token(
    body: TokenExchangeRequest, request: Request, session: Annotated[Session, Depends(get_db)]
) -> dict[str, Any]:
    """Return a short-lived token carrying no more than the registered scopes.

    Resolves the key against the database on every request rather than a
    process-lifetime registry. That is what makes a key survive a restart and a
    revocation take effect immediately; the constant-time digest comparison still
    happens in the registry, which is loaded per call.
    """
    requested = frozenset(body.scopes) if body.scopes is not None else None
    issued = exchange_api_key(
        body.api_key.get_secret_value(),
        registry=registry_for(request, session),
        settings=settings_for(request),
        requested_scopes=requested,
    )
    return success(token_response_payload(issued))


@router.post(
    "/api/v1/auth/login",
    summary="Log into the merchant or buyer console",
    tags=["session-auth"],
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Verify a password and start a session.

    The role in the issued session is the one on the stored account. It is never
    read from this request, which is the difference between this endpoint and
    the one it replaces.
    """
    settings = settings_for(request)
    try:
        operator = authenticate(
            session, email=body.email, password=body.password.get_secret_value()
        )
    except LoginError:
        # 401 with the same body for every failure. Deliberately not 403 for a
        # locked or disabled account: that distinction is the enumeration oracle.
        raise UnauthenticatedError(
            INVALID_CREDENTIALS_MESSAGE, details={"reason": "invalid_credentials"}
        ) from None
    issued = start_session(
        response=response,
        settings=settings,
        subject=operator.operator_id,
        role=operator.role,
        merchant_id=operator.merchant_id,
    )
    return success(
        {
            "authenticated": True,
            "principal": _principal_payload(issued.principal),
            "email": operator.email,
            "display_name": operator.display_name,
            "must_change_password": operator.must_change_password,
            "expires_at": issued.expires_at,
        }
    )


@router.post(
    "/api/v1/auth/change-password",
    summary="Change the signed-in operator's password",
    tags=["session-auth"],
)
def change_password(
    body: ChangePasswordRequest,
    session: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(session_principal)],
) -> dict[str, Any]:
    """Replace the current password, requiring the current one to be supplied.

    Session-authenticated specifically: an agent bearer token must not be able to
    rotate a console password, even one it can already read.
    """
    account = _operator_for_session(session, principal.subject)

    from packages.security.passwords import verify_password

    if not verify_password(body.current_password.get_secret_value(), account.password_hash):
        raise UnauthenticatedError(
            "Current password is incorrect.", details={"reason": "invalid_credentials"}
        )

    try:
        set_password(session, account, body.new_password.get_secret_value())
    except ValueError as exc:
        raise ValidationError(str(exc), details={"field": "new_password"}) from exc

    return success({"changed": True, "message": "Password updated."})


def _operator_for_session(session: Session, operator_id: str) -> OperatorAccount:
    """The account a session token names, or refuse.

    The operator id is the session's ``subject`` -- that is what
    :func:`start_session` was given, and what every other principal carries -- so
    a session outlives the row it names: the operator can be deleted, or the
    database reseeded under a running process. Every session-authenticated
    password change goes through here, because "the session is valid" and "the
    account still exists" are different facts and only the first is checked by
    token verification.
    """
    from sqlalchemy import select

    from services.connectors.models import OperatorAccount

    account = session.execute(
        select(OperatorAccount).where(OperatorAccount.operator_id == operator_id)
    ).scalar_one_or_none()
    if account is None:
        raise UnauthenticatedError(
            "This account no longer exists. Sign in again.", details={"reason": "account_gone"}
        )
    return account


@router.post(
    "/api/v1/auth/signup",
    summary="Register a merchant tenant and its first administrator",
    tags=["session-auth"],
)
def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Create a merchant tenant and sign in as its administrator.

    Refused unless ``ALLOW_CONSOLE_SIGNUP=1``. An open route here would let
    anyone create a tenant, and a tenant can publish a catalog, mint agent API
    keys, and receive pushed orders.
    """
    settings = settings_for(request)
    if not settings.allow_console_signup:
        raise ForbiddenError(
            "Self-service signup is disabled. Set ALLOW_CONSOLE_SIGNUP=1 to enable it.",
            details={"reason": "signup_disabled"},
        )

    from services.catalog.models import Merchant

    existing = session.get(Merchant, body.merchant_id)
    if existing is not None:
        raise ValidationError(
            "That merchant identifier is already taken.", details={"field": "merchant_id"}
        )
    if find_by_email(session, body.email) is not None:
        raise ValidationError("That email is already registered.", details={"field": "email"})

    session.add(Merchant(merchant_id=body.merchant_id, name=body.merchant_name, status="active"))
    session.flush()

    account = create_operator(
        session,
        merchant_id=body.merchant_id,
        email=body.email,
        display_name=body.display_name,
        password=body.password.get_secret_value(),
        role=Role.MERCHANT_ADMIN,
    )

    issued = start_session(
        response=response,
        settings=settings,
        subject=account.operator_id,
        role=Role.MERCHANT_ADMIN,
        merchant_id=body.merchant_id,
    )
    return success(
        {
            "authenticated": True,
            "merchant_id": body.merchant_id,
            "operator_id": account.operator_id,
            "expires_at": issued.expires_at,
        }
    )


@router.post(
    "/api/v1/auth/demo-session",
    summary="Start a no-credential session (local development and e2e only)",
    tags=["session-auth"],
)
@router.post(
    "/api/v1/auth/session",
    summary="Start a browser session (alias of /demo-session, local development only)",
    tags=["session-auth"],
)
def create_demo_session(
    body: DemoSessionRequest, request: Request, response: Response
) -> dict[str, Any]:
    """Issue a session without checking a credential. Local environments only.

    This is the previous ``/api/v1/auth/login`` body, moved to its own path so
    the credential-checking login is the only thing mounted at the URL a human
    types. Outside ``APP_ENV=local`` it refuses, because a caller could
    otherwise self-assign ``PLATFORM_ADMIN`` and act on any tenant.

    ``POST /api/v1/auth/session`` is the same handler under the name the web app
    and the browser tests already use. It was briefly dropped when the body moved,
    which turned every ``POST /api/v1/auth/session`` into a 405 and silently
    left the callers that depend on it unauthenticated: the 401s looked like
    authorisation problems rather than a missing route. The alias is kept so the
    two spellings cannot diverge again.
    """
    settings = settings_for(request)
    if not settings.is_local:
        raise ForbiddenError(
            "The demo session endpoint is disabled outside local development. "
            "Use POST /api/v1/auth/login with a credential.",
            details={"reason": "demo_login_disabled"},
        )
    merchant_id = body.merchant_id or settings.default_merchant_id
    subject = body.subject or f"user_{body.role.value}"
    issued = start_session(
        response=response,
        settings=settings,
        subject=subject,
        role=body.role,
        merchant_id=merchant_id,
        buyer_id=body.buyer_id,
    )
    return success(
        {
            "authenticated": True,
            "principal": {
                "subject": issued.principal.subject,
                "role": issued.principal.role.value,
                "merchant_id": issued.principal.merchant_id,
                "buyer_id": issued.principal.buyer_id,
                "scopes": sorted(scope.value for scope in issued.principal.scopes),
            },
            "expires_at": issued.expires_at,
        }
    )


@router.get(
    "/api/v1/auth/me",
    summary="Inspect the active browser or bearer principal",
    tags=["session-auth"],
)
@router.get(
    "/api/v1/auth/session",
    summary="Inspect the active browser session",
    tags=["session-auth"],
)
async def get_current_session(request: Request) -> dict[str, Any]:
    """Return the authenticated principal or unauthenticated status without raising."""
    from packages.errors.exceptions import ForbiddenError
    from packages.security.tokens import TokenError

    try:
        principal = await current_principal(request)
        return success({"authenticated": True, "principal": _principal_payload(principal)})
    except (UnauthenticatedError, ForbiddenError, TokenError):
        # Credential problems mean "not signed in". Anything else (a database
        # outage, a missing registry, a programming error) must propagate
        # to the error middleware instead of masquerading as anonymous.
        return success({"authenticated": False, "principal": None})


@router.post(
    "/api/v1/auth/logout",
    summary="Terminate the current browser session",
    tags=["session-auth"],
)
async def logout_session(request: Request, response: Response) -> dict[str, Any]:
    """Clear the session cookie."""
    settings = settings_for(request)
    clear_session_cookie(response, settings=settings)
    return success({"authenticated": False, "message": "Session terminated successfully."})


@router.get(
    "/api/v1/auth/console-status",
    summary="Report whether this deployment has any usable login path",
    tags=["session-auth"],
)
def console_status(request: Request) -> dict[str, Any]:
    """Which login affordances are live.

    The frontend reads this to decide whether to show a password form at all.
    Without it a deployment with signup disabled and no seeded operator renders a
    login page that cannot succeed, and the operator has to read the API logs to
    find out why.
    """
    settings = settings_for(request)
    return success(
        {
            "password_login_enabled": not settings.is_local,
            "demo_session_enabled": settings.is_local,
            "signup_enabled": settings.allow_console_signup,
            "password_min_length": 12,
        }
    )


__all__ = ["router"]
