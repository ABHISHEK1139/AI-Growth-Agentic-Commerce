"""External agent API-key onboarding and lifecycle (Phase 6).

An external buyer agent is provisioned by minting an API key bound to a
tenant, a role, and a set of scopes. The key is shown to the merchant
exactly once at creation; afterwards only the prefix and a revocation
endpoint are exposed. The persisted record is the hashed key, never the
cleartext, so a database read cannot leak a working credential.

This is the on-ramp for the documented ``api-key/OAuth onboarding``
acceptance criterion: a merchant creates a key, an external agent
exchanges it for a short-lived bearer token via
``/api/v1/agent/auth/token``, and the merchant can revoke the key at any
time.

The keys live in the in-memory :class:`~packages.security.apikeys.ApiClientRegistry`
attached to the application state. Replacing that registry with a persistent
table is the planned next step in Task 9; the contract exposed by this
router does not change.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import require_roles
from apps.api.envelope import success
from packages.security.apikeys import ApiClient, ApiClientRegistry
from packages.security.principals import Principal, Role, Scope

router = APIRouter(prefix="/api/v1/agent/api-keys", tags=["agent-api-keys"])

MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.PLATFORM_ADMIN)),
]


class CreateApiKeyRequest(BaseModel):
    """A request to mint a new external-agent API key.

    Unknown fields are rejected so a typo'd scope cannot silently widen
    the new key's authority.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, description="Human-readable label for the key.")
    scopes: list[Scope] = Field(description="Scopes the key will be allowed to mint tokens for.")
    role: Role = Field(default=Role.BUYER, description="Role the holder is treated as.")
    buyer_id: str | None = Field(default=None, description="Required when ``role`` is BUYER.")


class CreateApiKeyResponse(BaseModel):
    api_key: str = Field(description="The cleartext API key. Shown exactly once; never stored.")
    client_id: str = Field(description="Opaque identifier for later revocation / listing.")
    key_prefix: str = Field(description="First 12 characters of the key; safe to log.")
    name: str
    role: Role
    scopes: list[Scope]
    merchant_id: str


class ApiKeySummary(BaseModel):
    """Redacted view of a key — no secret material."""

    client_id: str
    key_prefix: str
    name: str
    role: Role
    scopes: list[Scope]
    active: bool


def _registry(request: Request) -> ApiClientRegistry:
    registry = getattr(request.app.state, "api_client_registry", None)
    if not isinstance(registry, ApiClientRegistry):
        raise RuntimeError("api client registry is not installed on the application")
    return registry


def _client_to_summary(client: ApiClient) -> ApiKeySummary:
    return ApiKeySummary(
        client_id=client.client_id,
        key_prefix=client.key_hash[:12],
        name=client.label,
        role=client.role,
        scopes=sorted(client.scopes, key=lambda s: s.value),
        active=client.active,
    )


def _clients_for_tenant(registry: ApiClientRegistry, merchant_id: str) -> list[ApiClient]:
    """Return every client registered for a tenant.

    The current registry has no lookup by tenant, so the iteration is the
    contract. With a persistent store this becomes a single query.
    """
    return [c for c in registry if c.merchant_id == merchant_id]


@router.post(
    "",
    summary="Mint a new external-agent API key (shown exactly once)",
    status_code=201,
)
def create_api_key(
    body: CreateApiKeyRequest,
    request: Request,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Create and immediately return the cleartext key.

    The server persists a hash, never the cleartext. A database dump cannot
    be used to authenticate; a reverse lookup cannot recover the secret.
    """
    registry = _registry(request)
    api_key, client = registry.issue(
        merchant_id=principal.merchant_id,
        role=body.role,
        buyer_id=body.buyer_id,
        scopes=list(body.scopes),
        label=body.name,
    )
    return success(
        CreateApiKeyResponse(
            api_key=api_key,
            client_id=client.client_id,
            key_prefix=client.key_hash[:12],
            name=body.name,
            role=body.role,
            scopes=sorted(set(body.scopes), key=lambda s: s.value),
            merchant_id=client.merchant_id,
        ).model_dump(mode="json")
    )


@router.get(
    "",
    summary="List the merchant's API keys (redacted)",
)
def list_api_keys(
    request: Request,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Return every key registered for the caller's tenant.

    No cleartext is included — only prefixes, scopes, and status. The
    merchant uses this to audit who has external-agent access and which
    keys are still live.
    """
    registry = _registry(request)
    matching = _clients_for_tenant(registry, principal.merchant_id)
    return success({"api_keys": [_client_to_summary(c).model_dump(mode="json") for c in matching]})


@router.delete(
    "/{client_id}",
    summary="Revoke an API key",
)
def revoke_api_key(
    client_id: str,
    request: Request,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Mark a key revoked so future token exchanges for it fail.

    Revocation is immediate. A key that does not exist or does not belong
    to the caller's tenant returns ``revoked: false, reason: not_found``
    — the merchant should not be able to probe the existence of another
    tenant's key.
    """
    registry = _registry(request)
    for client in _clients_for_tenant(registry, principal.merchant_id):
        if client.client_id == client_id:
            client.active = False
            return success({"revoked": True, "client_id": client_id})
    return success({"revoked": False, "reason": "not_found"})
