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

The keys are persisted in ``api_client`` through
:class:`~services.connectors.api_clients.ApiClientRepository`. They used to live in
an in-memory registry attached to the application state, which meant every minted
key stopped working when the process restarted -- and the token exchange then
answered 401, which reads exactly like a wrong key.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from apps.api.auth import require_roles
from apps.api.db import get_db
from apps.api.envelope import success
from packages.errors.exceptions import ForbiddenError
from packages.observability.context import new_id
from packages.security.apikeys import ApiClient, generate_api_key, hash_api_key
from packages.security.principals import Principal, Role, Scope, grant_scopes
from services.connectors.api_clients import ApiClientRepository

router = APIRouter(prefix="/api/v1/agent/api-keys", tags=["agent-api-keys"])

DbSession = Annotated[Session, Depends(get_db)]

MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.PLATFORM_ADMIN)),
]

#: The roles each caller may delegate by minting a key.
#:
#: A merchant-side operator is provisioning an external *buyer* agent, so
#: ``BUYER`` is the only role it may hand out. ``PLATFORM_ADMIN`` is deliberately
#: absent, and that is the whole point of the table: a key minted for that role
#: exchanges for a bearer token that :meth:`Principal.acting_on` accepts for any
#: tenant, which turns a merchant console session into a cross-tenant one. The
#: scope ceiling in ``ApiClient`` cannot catch it, because
#: ``scopes_for_role(PLATFORM_ADMIN)`` is a legitimate set. A platform
#: administrator, who already holds the role, may still mint for every role.
DELEGATABLE_ROLES: Mapping[Role, frozenset[Role]] = MappingProxyType(
    {
        Role.BUYER: frozenset(),
        Role.MERCHANT_OPERATOR: frozenset(),
        Role.MERCHANT_ADMIN: frozenset({Role.BUYER}),
        Role.PLATFORM_ADMIN: frozenset(Role),
    }
)


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

    @model_validator(mode="after")
    def _buyer_role_needs_a_buyer_id(self) -> CreateApiKeyRequest:
        # Without this the invariant in ``ApiClient`` fires at registry insertion
        # and the caller receives a 500 traceback for a body the schema accepts.
        if self.role is Role.BUYER and not self.buyer_id:
            raise ValueError("buyer_id is required when role is BUYER")
        return self


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


def _repository(db: Session) -> ApiClientRepository:
    """The API client repository for this request.

    Replaces a lookup of ``app.state.api_client_registry``, which was populated
    once at boot and therefore empty after any restart -- so a merchant's keys
    worked until the process recycled and then reported themselves as invalid.
    """
    return ApiClientRepository(db)


def _client_to_summary(client: ApiClient) -> ApiKeySummary:
    return ApiKeySummary(
        client_id=client.client_id,
        key_prefix=client.key_hash[:12],
        name=client.label,
        role=client.role,
        scopes=sorted(client.scopes, key=lambda s: s.value),
        active=client.active,
    )


@router.post(
    "",
    summary="Mint a new external-agent API key (shown exactly once)",
    status_code=201,
)
def create_api_key(
    body: CreateApiKeyRequest,
    principal: MerchantPrincipal,
    db: DbSession,
) -> dict[str, Any]:
    """Create and immediately return the cleartext key.

    The server persists a hash, never the cleartext. A database dump cannot
    be used to authenticate; a reverse lookup cannot recover the secret.

    The requested role is checked against :data:`DELEGATABLE_ROLES` and a
    request outside the caller's delegation ceiling is refused with ``FORBIDDEN``
    rather than quietly narrowed to something the caller did not ask for.
    """
    permitted = DELEGATABLE_ROLES.get(principal.role, frozenset())
    if body.role not in permitted:
        raise ForbiddenError(
            "This account cannot mint an API key for the requested role.",
            details={
                "reason": "role_delegation_not_permitted",
                "requested_role": body.role.value,
                "permitted_roles": sorted(role.value for role in permitted),
            },
        )

    # Minted here rather than through the registry so the plaintext exists in
    # exactly one place, and the digest is what gets written.
    api_key = generate_api_key()
    client = ApiClient(
        client_id=new_id("apc"),
        key_hash=hash_api_key(api_key),
        merchant_id=principal.merchant_id,
        role=body.role,
        buyer_id=body.buyer_id,
        # `grant_scopes` applies the role ceiling, so a request that somehow
        # carried an excess scope is narrowed rather than persisted.
        scopes=grant_scopes(body.role, body.scopes),
        label=body.name,
    )
    _repository(db).add(client)
    db.commit()

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
    principal: MerchantPrincipal,
    db: DbSession,
) -> dict[str, Any]:
    """Return every key registered for the caller's tenant.

    No cleartext is included — only prefixes, scopes, and status. The
    merchant uses this to audit who has external-agent access and which
    keys are still live.
    """
    matching = _repository(db).list_for_merchant(principal.merchant_id)
    return success({"api_keys": [_client_to_summary(c).model_dump(mode="json") for c in matching]})


@router.delete(
    "/{client_id}",
    summary="Revoke an API key",
)
def revoke_api_key(
    client_id: str,
    principal: MerchantPrincipal,
    db: DbSession,
) -> dict[str, Any]:
    """Mark a key revoked so future token exchanges for it fail.

    Revocation is immediate. A key that does not exist or does not belong
    to the caller's tenant returns ``revoked: false, reason: not_found``
    — the merchant should not be able to probe the existence of another
    tenant's key.
    """
    if not _repository(db).revoke(client_id, principal.merchant_id):
        return success({"revoked": False, "reason": "not_found"})
    db.commit()
    return success({"revoked": True, "client_id": client_id})
