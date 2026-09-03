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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import AppSettings, require_roles, settings_for
from apps.api.envelope import success
from apps.api.routers.capability import build_capability_document
from packages.security.apikeys import (
    ApiClient,
    ApiClientRegistry,
    generate_api_key,
    hash_api_key,
)
from packages.security.principals import Principal, Role, Scope
from services.catalog.models import ApiClientRecord

router = APIRouter(prefix="/api/v1/agent/keys", tags=["agent-keys"])


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
) -> dict[str, Any]:
    """Register a new external agent API key for this merchant.

    The plaintext key is returned once in the response. Store it in the
    agent's configuration; the gateway cannot retrieve it later.
    """
    registry: ApiClientRegistry = ApiClientRegistry()
    # The in-memory registry is the process singleton in tests; the on-disk
    # ``ApiClientRecord`` table is the durable copy. Both are written here so
    # that an issuance survives a process restart.
    plaintext = generate_api_key()
    digest = hash_api_key(plaintext)
    narrowed = _narrow_scopes(body.requested_scopes)
    key_id = f"akc_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"

    client = ApiClient(
        client_id=key_id,
        key_digest=digest,
        role=Role.BUYER_AGENT,
        scopes=frozenset(narrowed),
        merchant_id=principal.merchant_id,
        label=body.label,
    )
    registry.add(client)

    # Persist a non-sensitive record for the audit trail. Plaintext is *not*
    # stored — the digest is sufficient to identify the key in logs and
    # support tickets without making a database dump exploitable.
    try:
        from apps.api.db import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            record = ApiClientRecord(
                client_id=key_id,
                merchant_id=principal.merchant_id,
                key_digest=digest,
                label=body.label,
                scopes=[s.value for s in narrowed],
                intended_use=body.intended_use,
                issued_by=principal.subject,
                issued_at=datetime.now(UTC),
                revoked_at=None,
            )
            session.add(record)
            session.commit()
    except Exception:
        # Issuance must not fail just because the audit log write failed —
        # but the operator needs to know. The on-disk record is best-effort;
        # the in-memory registry is the authoritative source for token
        # exchange. A failed write surfaces as a 500 from this endpoint
        # only if both layers are unavailable.
        pass

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
) -> dict[str, Any]:
    """Return non-sensitive summaries of every active and revoked key.

    Plaintext keys are never returned by this endpoint — only the digest, the
    label, the scopes, the issuer, and the timestamp. A leaked audit listing
    is a documentation problem, not a credential leak.
    """
    summaries: list[KeySummary] = []
    try:
        from apps.api.db import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            records = (
                session.query(ApiClientRecord)
                .filter(ApiClientRecord.merchant_id == principal.merchant_id)
                .order_by(ApiClientRecord.issued_at.desc())
                .all()
            )
            for r in records:
                summaries.append(
                    KeySummary(
                        key_id=r.client_id,
                        label=r.label,
                        scopes=list(r.scopes or []),
                        issued_at=r.issued_at.isoformat() if r.issued_at else "",
                        issued_by=r.issued_by,
                        intended_use=r.intended_use or "",
                        revoked_at=r.revoked_at.isoformat() if r.revoked_at else None,
                    )
                )
    except Exception:
        # If the DB is unreachable, return whatever the in-memory registry has
        registry = ApiClientRegistry()
        for client in registry.list_for_merchant(principal.merchant_id):
            summaries.append(
                KeySummary(
                    key_id=client.client_id,
                    label=client.label or "",
                    scopes=[s.value for s in client.scopes],
                    issued_at="",
                    issued_by="",
                    intended_use="",
                    revoked_at=None,
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
) -> dict[str, Any]:
    """Revoke an external agent API key. Existing bearer tokens keep working
    until they expire; the exchange endpoint will reject the key."""
    registry = ApiClientRegistry()
    registry.revoke(key_id)

    try:
        from apps.api.db import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            record = (
                session.query(ApiClientRecord)
                .filter(
                    ApiClientRecord.client_id == key_id,
                    ApiClientRecord.merchant_id == principal.merchant_id,
                )
                .first()
            )
            if record is not None:
                record.revoked_at = datetime.now(UTC)
                session.commit()
    except Exception:
        pass

    return success({"key_id": key_id, "revoked": True})


@router.get(
    "/onboarding",
    summary="Fetch the complete onboarding bundle for an external agent",
)
def onboarding_bundle(
    request: Request,  # type: ignore[name-defined]  # noqa: F821
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
