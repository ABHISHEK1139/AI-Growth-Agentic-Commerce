"""API keys must outlive the process that minted them.

The bug these exist to pin: `install_auth` attached an `ApiClientRegistry` to
`app.state`, populated once at boot and empty on every start. A merchant minted
a key, handed it to a buyer agent, restarted the API, and every subsequent token
exchange returned 401 -- which is indistinguishable from a wrong key, so the
obvious response was to re-mint, and the previous keys stayed live and
unrevoked on the buyer's side.

The assertion that matters is the last one in the file: a *second* application
object, with no shared state, still honours a key minted through the first.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.config import Settings
from apps.api.db import get_db
from apps.api.main import create_app
from apps.api.middleware.ratelimit import InMemoryRateLimitBackend
from packages.observability.context import new_id
from packages.security.apikeys import ApiClient, generate_api_key, hash_api_key
from packages.security.principals import Role, Scope
from services.connectors.api_clients import (
    MAX_LOADED_CLIENTS,
    ApiClientRepository,
    _UnparseableClient,
)
from services.connectors.models import ApiClient as ApiClientRow


@pytest.fixture
def factory() -> sessionmaker:
    """A real ``api_client`` table, shared across every app in the test."""
    import services.connectors.models  # noqa: F401 - register the tables
    import tests.sqlite_types  # noqa: F401 - registers the JSONB/ARRAY compilers
    from packages.db.base import Base
    from services.catalog.models import Merchant

    # `StaticPool` so every session in every app shares one in-memory database.
    # Without it each new connection gets its own empty database and the key
    # "disappears" -- which is the bug under test, not a property of the fix.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = made()
    session.add(Merchant(merchant_id="merchant_demo", name="Demo", status="active"))
    session.commit()
    session.close()
    return made


def _app(factory: sessionmaker) -> FastAPI:
    """A freshly constructed application. Two of these simulate a restart."""

    def override_get_db():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    application = create_app(
        Settings(
            app_env="local", payment_provider="fake", model_provider="mock", log_level="WARNING"
        )
    )
    application.state.rate_limit_backend = InMemoryRateLimitBackend()
    application.dependency_overrides[get_db] = override_get_db
    return application


def _mint(factory: sessionmaker, **overrides) -> str:
    kwargs = {
        "merchant_id": "merchant_demo",
        "role": Role.BUYER,
        "buyer_id": "buyer_ada",
        "scopes": {Scope.CATALOG_READ, Scope.CHECKOUT_WRITE, Scope.PAYMENT_WRITE},
    }
    kwargs.update(overrides)
    plaintext = generate_api_key()
    with factory() as session:
        ApiClientRepository(session).add(
            ApiClient(
                client_id=new_id("apc"),
                key_hash=hash_api_key(plaintext),
                **kwargs,
            )
        )
        session.commit()
    return plaintext


def _exchange(app: FastAPI, api_key: str, scopes: list[str] | None = None) -> int:
    with TestClient(app) as client:
        body = {"api_key": api_key}
        if scopes is not None:
            body["scopes"] = scopes
        return client.post("/api/v1/agent/auth/token", json=body).status_code


# --- The restart ---------------------------------------------------------


def test_a_key_survives_a_restart(factory: sessionmaker) -> None:
    """The regression test.

    Two independent application objects, no shared state, one database. The second
    one stands in for a redeployed process: before this change it had an empty
    registry and answered 401 for a key that was still perfectly valid.
    """
    api_key = _mint(factory)

    first = _app(factory)
    assert _exchange(first, api_key, ["catalog:read"]) == 200

    # A genuinely new application object -- not a reused one, not a cache reset.
    restarted = _app(factory)
    assert _exchange(restarted, api_key, ["catalog:read"]) == 200


def test_the_state_registry_is_no_longer_the_source_of_truth(factory: sessionmaker) -> None:
    """`app.state` must not hold a credential registry at all.

    Leaving the attribute in place is what allowed the old behaviour to survive a
    refactor: a reader would reasonably assume the state object was authoritative.
    """
    app = _app(factory)

    assert not hasattr(app.state, "api_client_registry")


# --- Storage properties --------------------------------------------------


def test_only_the_digest_is_stored(factory: sessionmaker) -> None:
    api_key = _mint(factory)

    with factory() as session:
        rows = session.execute(select(ApiClientRow)).scalars().all()

    assert len(rows) == 1
    assert rows[0].key_hash == hash_api_key(api_key)
    assert api_key not in rows[0].key_hash
    assert len(rows[0].key_hash) == 64


def test_a_minted_key_resolves_back_to_its_scopes(factory: sessionmaker) -> None:
    api_key = _mint(factory, scopes={Scope.CATALOG_READ, Scope.CHECKOUT_WRITE})
    app = _app(factory)

    with TestClient(app) as client:
        granted = client.post(
            "/api/v1/agent/auth/token",
            json={"api_key": api_key, "scopes": ["catalog:read", "checkout:write"]},
        )
        refused = client.post(
            "/api/v1/agent/auth/token",
            json={"api_key": api_key, "scopes": ["payment:write"]},
        )

    assert granted.status_code == 200
    # A scope the client was not issued is 403, not a silently narrowed token.
    assert refused.status_code == 403
    assert refused.json()["error"]["details"]["requested"] == ["payment:write"]


def test_revocation_takes_effect_on_the_next_exchange(factory: sessionmaker) -> None:
    """Immediate, because the registry is rebuilt per request.

    With a cached registry a revocation would wait for the cache to expire, which
    is exactly the window an attacker who already holds the key wants.
    """
    api_key = _mint(factory)
    app = _app(factory)
    assert _exchange(app, api_key) == 200

    with factory() as session:
        assert ApiClientRepository(session).revoke("x", "merchant_demo") is False
        client_id = session.execute(select(ApiClientRow.api_client_id)).scalar_one()
        assert ApiClientRepository(session).revoke(client_id, "merchant_demo") is True
        session.commit()

    assert _exchange(app, api_key) == 401


def test_a_revoked_client_is_preserved_not_deleted(factory: sessionmaker) -> None:
    """The history of who held which key is the merchant's only audit trail.

    Deleting the row would make "which agents have access" unanswerable, and
    would retroactively look like the key never existed.
    """
    _mint(factory, label="original-agent")
    with factory() as session:
        client_id = session.execute(select(ApiClientRow.api_client_id)).scalar_one()
        ApiClientRepository(session).revoke(client_id, "merchant_demo")
        session.commit()

    with factory() as session:
        row = session.get(ApiClientRow, client_id)

    assert row is not None
    assert row.status == "revoked"
    assert row.label == "original-agent"


# --- Tenant isolation ----------------------------------------------------


def test_listing_is_scoped_to_one_tenant(factory: sessionmaker) -> None:
    from services.catalog.models import Merchant

    with factory() as session:
        session.add(Merchant(merchant_id="merchant_other", name="Other", status="active"))
        session.commit()
    _mint(factory)
    _mint(factory, merchant_id="merchant_other")

    with factory() as session:
        repository = ApiClientRepository(session)
        assert len(repository.list_for_merchant("merchant_demo")) == 1
        assert len(repository.list_for_merchant("merchant_other")) == 1


def test_revoking_another_tenants_key_reports_not_found(factory: sessionmaker) -> None:
    """A key id from another tenant must be invisible, not merely unauthorised.

    "not_found" says nothing about whether the id exists elsewhere; a 403 would
    confirm it.
    """
    from services.catalog.models import Merchant

    with factory() as session:
        session.add(Merchant(merchant_id="merchant_other", name="Other", status="active"))
        session.commit()
    _mint(factory, merchant_id="merchant_other")
    with factory() as session:
        client_id = session.execute(select(ApiClientRow.api_client_id)).scalar_one()

    with factory() as session:
        assert ApiClientRepository(session).revoke(client_id, "merchant_demo") is False

    with factory() as session:
        assert session.execute(select(ApiClientRow)).scalars().one().status == "active"


# --- Robustness ----------------------------------------------------------


def test_an_unrecognised_stored_role_refuses_that_client_only(factory: sessionmaker) -> None:
    """One bad row must not break token exchange for every other agent.

    A row written by a newer build, or hand-edited, would otherwise make every
    agent on the platform fail. The repository raises for that one client; the
    registry build catches it and drops just that row, so the good key still
    works.
    """
    good = _mint(factory)
    bad = _mint(factory)
    with factory() as session:
        row = session.execute(
            select(ApiClientRow).where(ApiClientRow.key_hash == hash_api_key(bad))
        ).scalar_one()
        row.role = "superuser"
        session.commit()

    # The repository refuses this client...
    with factory() as session, pytest.raises(_UnparseableClient):
        ApiClientRepository(session).list_all_active()

    # ...and the registry build drops it rather than failing the whole request.
    app = _app(factory)
    assert _exchange(app, good) == 200
    assert _exchange(app, bad) == 401


def test_an_unrecognised_scope_is_dropped_not_fatal(factory: sessionmaker) -> None:
    """A scope renamed between releases must not invalidate the whole key."""
    api_key = _mint(factory, scopes={Scope.CATALOG_READ})
    with factory() as session:
        row = session.execute(
            select(ApiClientRow).where(ApiClientRow.key_hash == hash_api_key(api_key))
        ).scalar_one()
        row.scopes = ["catalog:read", "scope:from_a_future_release"]
        session.commit()

    app = _app(factory)

    assert _exchange(app, api_key, ["catalog:read"]) == 200


def test_the_loaded_set_is_bounded(factory: sessionmaker) -> None:
    """The registry is rebuilt per request, so the read is bounded on purpose."""
    assert MAX_LOADED_CLIENTS > 0
    with factory() as session:
        assert ApiClientRepository(session).list_all_active() == []


def test_count_active_counts_only_live_keys(factory: sessionmaker) -> None:
    _mint(factory)
    second = _mint(factory)
    with factory() as session:
        repository = ApiClientRepository(session)
        client_id = session.execute(
            select(ApiClientRow.api_client_id).where(ApiClientRow.key_hash == hash_api_key(second))
        ).scalar_one()
        repository.revoke(client_id, "merchant_demo")
        session.commit()
        assert repository.count_active("merchant_demo") == 1
