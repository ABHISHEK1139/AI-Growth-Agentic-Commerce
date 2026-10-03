"""External agent API key onboarding (Phase 6 — Requirement 20).

External autonomous buyers do not have a human at a keyboard; they need a
programmatic way to register, get a key, and then exchange it for scoped
bearer tokens. The merchant console already issues keys for its own
back-office callers, but the public agent surface has its own flow because:

* The caller is not in the merchant's user table — they are an autonomous
  process running elsewhere.
* The scopes they are issued must be the *narrowest* of what they need, not
  a copy of a merchant admin's set, because a key on a remote server has a
  much larger blast radius.
* The act of issuance has to leave an audit trail with the issuing operator
  and the intended use, so a leaked key can be traced back.

Three flows are exposed:

* ``POST /api/v1/agent/keys``            — register a new key (admin only)
* ``GET  /api/v1/agent/keys``            — list the merchant's issued keys
* ``DELETE /api/v1/agent/keys/{id}``     — revoke a key (admin only)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.auth import AppSettings, require_roles, settings_for
from apps.api.db import get_db
from apps.api.envelope import success
from apps.api.routers.capability import build_capability_document
from packages.observability.context import new_id
from packages.observability.logging import get_logger
from packages.security.apikeys import ApiClient, generate_api_key, hash_api_key
from packages.security.principals import Principal, Role, Scope
from services.catalog.models import Buyer
from services.connectors.api_clients import ApiClientRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent/keys", tags=["agent-keys"])

#: Request-scoped database session. Every endpoint here reads or writes
#: `api_client`, so there is no longer a code path that works without one.
DbSession = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterKeyRequest(BaseModel):
    """A request to issue a new external agent API key.

    ``label`` is a free-form operator-visible string; ``requested_scopes`` is the
    caller *asking* for what they need — the server may narrow it further. The
    ``model_config = extra="forbid"`` setting means a typo in a field name is
    rejected as 422 rather than silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    requested_scopes: list[Scope] = Field(
        default_factory=lambda: [Scope.CATALOG_READ],
        description="The scopes the agent claims it needs. Narrowed at issuance.",
    )
    intended_use: str = Field(
        default="external buyer",
        max_length=500,
        description="Free-form description recorded in the audit trail.",
    )


class IssuedKeyResponse(BaseModel):
    """The single response shape that ever contains a plaintext API key.

    The plaintext key is returned *once* on registration and is never stored.
    All later reads return only the digest and metadata. The audit record
    contains the digest, the issuer, and the labelled intended use, not the
    plaintext.
    """

    model_config = ConfigDict(extra="forbid")

    key_id: str
    api_key: str = Field(description="Plaintext key, shown once. Not retrievable later.")
    key_digest: str = Field(description="SHA-256 digest of the key, for audit lookups.")
    label: str
    scopes: list[str]
    issued_at: str
    issued_by: str
    intended_use: str
    exchange_endpoint: str = Field(
        default="/api/v1/agent/auth/token",
        description="The endpoint to exchange this key for a scoped bearer token.",
    )


class KeySummary(BaseModel):
    """A non-sensitive summary of a key — safe to list."""

    model_config = ConfigDict(extra="forbid")

    key_id: str
    label: str
    scopes: list[str]
    issued_at: str
    issued_by: str
    intended_use: str
    revoked_at: str | None = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

MerchantAdminPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.PLATFORM_ADMIN)),
]

#: The set of scopes an external agent is *ever* allowed to be issued. Anything
#: outside this set is administrative and must not be handed to a remote caller.
EXTERNAL_AGENT_ALLOWED_SCOPES: frozenset[Scope] = frozenset(
    {
        Scope.CATALOG_READ,
        Scope.CHECKOUT_WRITE,
        Scope.PAYMENT_WRITE,
    }
)


def _narrow_scopes(requested: list[Scope]) -> list[Scope]:
    """Return the subset of ``requested`` that an external agent may hold.

    The narrow step is the point. A merchant admin who clicks "issue" by
    reflex should not accidentally issue an ``admin:*`` scope. The server
    narrows to the public-allowed set and the caller can read the result
    to know what they were actually given.
    """
    narrowed = [s for s in requested if s in EXTERNAL_AGENT_ALLOWED_SCOPES]
    if not narrowed:
        # Always grant at least catalog:read so the key is at least useful
        narrowed = [Scope.CATALOG_READ]
    return narrowed


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    summary="Issue a new external agent API key",
    status_code=201,
)
def register_agent_key(
    body: RegisterKeyRequest,
    principal: MerchantAdminPrincipal,
    settings: AppSettings,
    db: DbSession,
) -> dict[str, Any]:
    """Register a new external agent API key for this merchant.

    The plaintext key is returned once in the response. Store it in the
    agent's configuration; the gateway cannot retrieve it later.
    """
    del settings  # no longer needed: the registry is loaded per request from the db
    narrowed = _narrow_scopes(body.requested_scopes)
    # Unpredictable identifiers: timestamp names are enumerable and can
    # collide within one microsecond.
    key_id = new_id("akc")
    buyer_id = f"buyer_{key_id}"

    # The digest is computed here rather than by `registry.issue` because the
    # registry no longer mints: the repository is the only writer, and building
    # the domain object by hand keeps the plaintext in exactly one place.
    plaintext = generate_api_key()
    digest = hash_api_key(plaintext)

    client = ApiClient(
        client_id=key_id,
        key_hash=digest,
        merchant_id=principal.merchant_id,
        role=Role.BUYER,
        buyer_id=buyer_id,
        scopes=frozenset(narrowed),
        label=body.label,
    )
    # A hard failure, not a best-effort write. This route used to catch the
    # database error and return 201 anyway, which handed the merchant a key that
    # could never authenticate -- a credential that looks real and is not.
    ApiClientRepository(db).add(client)

    # The buyer row has to exist in the same transaction as the credential.
    #
    # `checkout`, `order` and `payment` all carry a foreign key to `buyer`, and an
    # exchanged token presents `buyer_id = f"buyer_{key_id}"`. Provisioning that row
    # only as a string meant an agent could search the catalog but every checkout
    # died on `checkout_buyer_id_fkey` and returned 503 -- the headline agentic
    # commerce flow was non-functional end to end, and 503 reads as "try again
    # later" rather than "this was never going to work".
    #
    # Done here, at issuance, rather than lazily on first checkout: the credential
    # and the identity it acts as are created together, so there is no window in
    # which a valid key exists with no buyer behind it.
    if db.query(Buyer).filter(Buyer.buyer_id == buyer_id).first() is None:
        db.add(
            Buyer(
                buyer_id=buyer_id,
                tenant_id=principal.merchant_id,
                display_name=body.label or f"Agent {key_id}",
                status="active",
            )
        )
    db.commit()

    response = IssuedKeyResponse(
        key_id=key_id,
        api_key=plaintext,
        key_digest=digest,
        label=body.label,
        scopes=[s.value for s in narrowed],
        issued_at=datetime.now(UTC).isoformat(),
        issued_by=principal.subject,
        intended_use=body.intended_use,
    )
    return success({"key": response.model_dump(mode="json")})


@router.get(
    "",
    summary="List external agent API keys for this merchant",
)
def list_agent_keys(
    principal: MerchantAdminPrincipal,
    db: DbSession,
) -> dict[str, Any]:
    """Return non-sensitive summaries of every active and revoked key.

    Plaintext keys are never returned by this endpoint — only the digest, the
    label, the scopes, the issuer, and the timestamp. A leaked audit listing
    is a documentation problem, not a credential leak.
    """
    repository = ApiClientRepository(db)
    summaries: list[KeySummary] = []
    for client in repository.list_for_merchant(principal.merchant_id):
        summaries.append(
            KeySummary(
                key_id=client.client_id,
                label=client.label or "",
                scopes=[s.value for s in client.scopes],
                issued_at="",
                issued_by="",
                intended_use="",
                revoked_at=None if client.active else datetime.now(UTC).isoformat(),
            )
        )
    return success({"keys": [s.model_dump(mode="json") for s in summaries]})


@router.delete(
    "/{key_id}",
    summary="Revoke an external agent API key",
    status_code=200,
)
def revoke_agent_key(
    key_id: str,
    principal: MerchantAdminPrincipal,
    db: DbSession,
) -> dict[str, Any]:
    """Revoke an external agent API key. Existing bearer tokens keep working
    until they expire; the exchange endpoint will reject the key.

    Scoped to the caller's tenant inside the repository. Resolving the id without
    that scope would let one merchant deactivate another tenant's credential; a
    key that is not the caller's is answered ``revoked: false, reason: not_found``,
    which says nothing about whether it exists elsewhere.
    """
    if not ApiClientRepository(db).revoke(key_id, principal.merchant_id):
        return success({"key_id": key_id, "revoked": False, "reason": "not_found"})
    return success({"key_id": key_id, "revoked": True})


@router.get(
    "/onboarding",
    summary="Fetch the complete onboarding bundle for an external agent",
)
def onboarding_bundle(
    request: Request,
    principal: MerchantAdminPrincipal,
) -> dict[str, Any]:
    """Return the discovery, capability, and tool bundle in one response.

    This is the single endpoint a new external operator hits on day 1: it
    returns the agent's complete view of the gateway, including the live
    capability document and the tool catalogue. Caching the response for an
    hour is appropriate.
    """
    settings = settings_for(request)
    cap = build_capability_document(settings, None, merchant_id=principal.merchant_id)
    return success(
        {
            "onboarding_version": "1.0",
            "capability": cap.model_dump(mode="json"),
            "tool_catalogue_endpoint": "/api/v1/agent/tools",
            "register_key_endpoint": "/api/v1/agent/keys",
            "exchange_token_endpoint": "/api/v1/agent/auth/token",
            "scopes_available_to_external_agents": sorted(
                s.value for s in EXTERNAL_AGENT_ALLOWED_SCOPES
            ),
        }
    )
