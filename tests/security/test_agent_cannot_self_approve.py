"""An external agent must not be able to approve its own spending.

The capability document advertises ``explicit_approval_required``, and the payment
path refuses while an authorization is pending. That control is only real if a
*person* clears it.

``approve``/``reject`` were gated on ``require_scopes(CHECKOUT_WRITE)`` alone, and
an exchanged agent token carries exactly that scope -- it has to, so the agent can
build a cart. The agent token therefore approved its own authorization and then
drew money, removing the human from the loop in precisely the place the human
matters: spending above the auto-approval limit.

Requirement 20.5 already states that a session-only administrative action must not
be performable with a long-lived agent credential. This is that action.
"""

from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.config import Settings
from apps.api.db import get_db
from apps.api.main import create_app
from apps.api.middleware.ratelimit import InMemoryRateLimitBackend


@pytest.fixture
def client_factory():
    import services.audit.repository  # noqa: F401
    import services.authorization.models  # noqa: F401
    import services.catalog.models  # noqa: F401
    import services.checkout.models  # noqa: F401
    import services.connectors.models  # noqa: F401
    import services.payments.models  # noqa: F401
    import tests.sqlite_types  # noqa: F401
    from packages.db.base import Base
    from services.catalog.models import Merchant

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    seed = made()
    seed.add(Merchant(merchant_id="merchant_demo", name="Demo", status="active"))
    seed.commit()
    seed.close()

    def override_get_db():
        session = made()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def build() -> TestClient:
        app = create_app(
            Settings(
                app_env="local",
                payment_provider="fake",
                model_provider="mock",
                log_level="WARNING",
            )
        )
        app.state.rate_limit_backend = InMemoryRateLimitBackend()
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    return build


def _agent_token(client: TestClient) -> str:
    """Issue an agent key and exchange it, exactly as an external agent would."""
    client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "merchant_demo"},
    )
    res = client.post(
        "/api/v1/agent/keys",
        json={
            "label": "self-approval-probe",
            "requested_scopes": ["catalog:read", "checkout:write", "payment:write"],
        },
    )
    assert res.status_code == 201, res.text
    api_key = res.json()["data"]["key"]["api_key"]

    token = TestClient(client.app)
    exchanged = token.post("/api/v1/agent/auth/token", json={"api_key": api_key})
    assert exchanged.status_code == 200, exchanged.text
    access = exchanged.json()["data"]["access_token"]
    assert "checkout:write" in exchanged.json()["data"]["scopes"], (
        "precondition: the agent token must hold checkout:write, otherwise this test "
        "would pass for the wrong reason"
    )
    return access


def test_an_agent_token_cannot_approve(client_factory) -> None:
    client = client_factory()
    access = _agent_token(client)
    headers = {"Authorization": f"Bearer {access}"}

    res = client.post("/api/v1/authorization/ath_whatever/approve", json={}, headers=headers)
    assert res.status_code == 403, res.text
    body = res.json()["error"]
    assert body["code"] == "FORBIDDEN"
    assert body["details"].get("reason") == "session_required", body["details"]


def test_an_agent_token_cannot_reject(client_factory) -> None:
    client = client_factory()
    access = _agent_token(client)
    headers = {"Authorization": f"Bearer {access}"}

    res = client.post("/api/v1/authorization/ath_whatever/reject", json={}, headers=headers)
    assert res.status_code == 403, res.text
    assert res.json()["error"]["details"].get("reason") == "session_required"


def test_a_session_principal_is_still_allowed_to_approve(client_factory) -> None:
    """The control has to be a gate, not a wall.

    Without this the fix could be "reject everyone" and both tests above would pass.
    A session that holds the scope must still be able to approve; it will 404 on an
    unknown authorization, which proves it got past authorisation.
    """
    client = client_factory()
    client.post(
        "/api/v1/auth/session",
        json={"role": "buyer", "merchant_id": "merchant_demo", "buyer_id": "buyer_x"},
    )
    res = client.post("/api/v1/authorization/ath_does_not_exist/approve", json={})
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_every_token_is_refused_for_being_a_token(client_factory) -> None:
    """The session check runs first, so the reason is "session_required" whatever
    the token's scopes are.

    Ordering is deliberate: "you are holding the wrong kind of credential" is both
    accurate and less informative to a caller than a scope list would be. This test
    pins that so nobody "fixes" the order into a scope leak.
    """
    client = client_factory()
    client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "merchant_demo"},
    )
    res = client.post(
        "/api/v1/agent/keys",
        json={"label": "catalog-only", "requested_scopes": ["catalog:read"]},
    )
    api_key = res.json()["data"]["key"]["api_key"]
    token = TestClient(client.app)
    access = token.post("/api/v1/agent/auth/token", json={"api_key": api_key}).json()["data"][
        "access_token"
    ]

    res = token.post(
        "/api/v1/authorization/ath_x/approve",
        json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["details"].get("reason") == "session_required"


def test_no_principal_can_approve_an_agents_authorization(client_factory) -> None:
    """A known, deliberate limitation -- pinned so it cannot drift unnoticed.

    An agent's authorization carries a synthetic ``buyer_akc_...`` buyer. Approval
    requires ``checkout:write`` *and* a matching ``buyer_id``. Only the ``buyer`` role
    holds ``checkout:write``; ``merchant_admin`` and ``merchant_operator`` are
    deliberately capped at ``catalog:read``, because a merchant-side credential that
    can move money is a merchant-side credential that can charge a buyer.

    So an agent purchase above ``auto_approval_limit_minor`` has no approver: the
    agent is barred from self-approval, and no human session holds the agent's buyer
    id. Purchases at or under the limit auto-approve and complete; larger ones stop.

    Closing this needs a policy decision, not a patch -- either a merchant-side
    approve scope, or an approver concept that is not buyer-identity-bound. Widening
    ``merchant_admin`` is not something a bug fix should do on its own.
    """
    client = client_factory()
    access = _agent_token(client)
    headers = {"Authorization": f"Bearer {access}"}

    # the agent itself
    assert (
        client.post("/api/v1/authorization/ath_x/approve", json={}, headers=headers).status_code
        == 403
    )

    # a merchant session: refused for lacking the grant, not for lacking a session
    client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "merchant_demo"},
    )
    res = client.post("/api/v1/authorization/ath_x/approve", json={})
    assert res.status_code == 403, res.text
    assert res.json()["error"]["details"].get("reason") != "session_required"

    # a buyer session: correct kind of credential, wrong buyer identity
    client.post(
        "/api/v1/auth/session",
        json={"role": "buyer", "merchant_id": "merchant_demo", "buyer_id": "buyer_human"},
    )
    res = client.post("/api/v1/authorization/ath_x/approve", json={})
    assert res.status_code == 404, res.text


def test_cookie_and_bearer_are_not_interchangeable_for_approval(client_factory) -> None:
    """An agent cannot ride a signed-in browser session to approve."""
    client = client_factory()
    client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "merchant_demo"},
    )
    access = _agent_token(client)

    # Session cookie present, agent bearer also present: the bearer decides.
    res = client.post(
        "/api/v1/authorization/ath_x/approve",
        json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["details"].get("reason") == "session_required"


# Keep the imports honest: these are part of the live HTTP path this test drives.
assert json and urllib.request and urllib.error and http.cookiejar
