from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from apps.api.auth import (
    clear_session_cookie,
    current_principal,
    exchange_api_key,
    registry_for,
    settings_for,
    start_session,
    token_response_payload,
)
from apps.api.envelope import success
from packages.security.apikeys import MAX_API_KEY_LENGTH
from packages.security.principals import Role, Scope

router = APIRouter(tags=["auth"])


class TokenExchangeRequest(BaseModel):
    """A key exchange request.

    ``SecretStr`` keeps the API key out of model representations and validation
    diagnostics. Unknown fields are refused so a misspelled scope field cannot
    silently produce a broader default token than the caller intended.
    """

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=MAX_API_KEY_LENGTH)
    scopes: list[Scope] | None = None


class SessionLoginRequest(BaseModel):
    """A browser session login or demo credential request."""

    role: Role = Field(default=Role.MERCHANT_ADMIN)
    merchant_id: str | None = None
    buyer_id: str | None = None
    subject: str | None = None


@router.post(
    "/api/v1/agent/auth/token",
    summary="Exchange an API key for a scoped bearer token",
    tags=["agent-auth"],
)
async def exchange_token(body: TokenExchangeRequest, request: Request) -> dict[str, Any]:
    """Return a short-lived token carrying no more than the registered scopes."""
    requested = frozenset(body.scopes) if body.scopes is not None else None
    issued = exchange_api_key(
        body.api_key.get_secret_value(),
        registry=registry_for(request),
        settings=settings_for(request),
        requested_scopes=requested,
    )
    return success(token_response_payload(issued))


@router.post(
    "/api/v1/auth/session",
    summary="Start a browser session and set an HttpOnly session cookie",
    tags=["session-auth"],
)
@router.post(
    "/api/v1/auth/login",
    summary="Log into the merchant or buyer console",
    tags=["session-auth"],
)
async def create_session(
    body: SessionLoginRequest, request: Request, response: Response
) -> dict[str, Any]:
    """Start an authenticated session and issue a cryptographically signed HttpOnly cookie.

    This endpoint is a demo/development login shortcut and is intentionally open
    (no credential required) in local development. Outside local environments it
    must be disabled: any caller could otherwise self-assign PLATFORM_ADMIN and
    act on any tenant with no authentication.
    """
    settings = settings_for(request)
    if not settings.is_local:
        from packages.errors.exceptions import ForbiddenError

        raise ForbiddenError(
            "The demo session endpoint is disabled outside local development. "
            "Use a real credential flow.",
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
    try:
        principal = await current_principal(request)
        return success(
            {
                "authenticated": True,
                "principal": {
                    "subject": principal.subject,
                    "role": principal.role.value,
                    "merchant_id": principal.merchant_id,
                    "buyer_id": principal.buyer_id,
                    "scopes": sorted(scope.value for scope in principal.scopes),
                },
            }
        )
    except Exception:
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
