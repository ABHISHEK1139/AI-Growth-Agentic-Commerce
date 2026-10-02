"""The login endpoint: what it accepts, and what it refuses to accept.

The endpoint these tests replace read ``role`` from the request body and issued a
session for it, so an anonymous caller could ask for ``platform_admin`` and get
it. The assertions below are mostly about that not being possible any more.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def operator_app(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The app with a real SQLite datastore the auth router can use.

    `get_db` resolves the process-wide engine, which under test points at
    Postgres. Overridden here so these tests need no Docker -- the credential
    path has to be testable on a laptop, and the properties being checked are in
    `services.connectors.operator_auth`, not in the driver.

    Rate limiting is pointed at a fresh in-memory backend, as the `app` fixture
    already does, because the *declared* login limit is 10 per 5 minutes -- and
    unauthenticated callers are counted by IP, so every test in this file shares
    one bucket. Without a per-test backend the eleventh test gets a 429 and
    reports it as a broken credential check. The limit itself is asserted
    separately in `TestLoginRateLimit`.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import services.connectors.models  # noqa: F401 - register the tables
    import tests.sqlite_types  # noqa: F401 - registers the JSONB/ARRAY compilers
    from apps.api.db import get_db
    from apps.api.middleware.ratelimit import InMemoryRateLimitBackend
    from packages.db.base import Base
    from services.catalog.models import Merchant

    app.state.rate_limit_backend = InMemoryRateLimitBackend()

    # `StaticPool` keeps one connection for the lifetime of the engine. A bare
    # `sqlite://` gives every new connection its own empty database, so the
    # schema this fixture creates would be gone by the time a request opened its
    # session -- and the endpoint would report "no such table" rather than
    # anything about the credential.
    #
    # `check_same_thread=False` because FastAPI runs a *synchronous* dependency in
    # a worker thread, so the fixture's connection is created on one thread and
    # used on another. The same pair appears in the production engine
    # (`apps.api.db`).
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    db.add(Merchant(merchant_id="merchant_demo", name="Demo", status="active"))
    db.commit()
    db.close()

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

    app.dependency_overrides[get_db] = override_get_db
    return app


PASSWORD = "correct-horse-battery-staple"


def _seed(
    app: FastAPI, *, role: str = "merchant_admin", email: str = "admin@merchant.local"
) -> None:
    from apps.api.db import get_db
    from packages.security.principals import Role
    from services.connectors.operator_auth import create_operator

    override = app.dependency_overrides[get_db]
    generator = override()
    session = next(generator)
    try:
        create_operator(
            session,
            merchant_id="merchant_demo",
            email=email,
            display_name="Admin",
            password=PASSWORD,
            role=Role(role),
        )
        session.commit()
    finally:
        generator.close()


# --- The vulnerability this replaced --------------------------------------


def test_a_role_in_the_request_body_cannot_escalate(operator_app: FastAPI) -> None:
    """The core regression test.

    The previous `/api/v1/auth/login` accepted `{"role": "platform_admin"}` with
    no credential and returned a signed session for it. Now the model refuses
    unknown fields, so the attempt is a 422 -- and the role that ends up in the
    session is the one on the stored row.
    """
    _seed(operator_app, role="merchant_operator")

    with TestClient(operator_app) as client:
        escalation = client.post(
            "/api/v1/auth/login",
            json={
                "email": "admin@merchant.local",
                "password": PASSWORD,
                "role": "platform_admin",
            },
        )
        honest = client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )
        me = client.get("/api/v1/auth/me")

    # The escalation attempt is refused outright rather than silently ignored:
    # a caller who thinks they granted themselves an admin role needs to know.
    assert escalation.status_code == 422
    assert honest.status_code == 200
    assert honest.json()["data"]["principal"]["role"] == "merchant_operator"
    assert me.json()["data"]["principal"]["role"] == "merchant_operator"


def test_an_unrecognised_field_is_refused_rather_than_ignored(
    operator_app: FastAPI,
) -> None:
    """A typo in a security-relevant field must not be silently dropped."""
    _seed(operator_app)

    with TestClient(operator_app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@merchant.local", "password": PASSWORD, "rol": "platform_admin"},
        )

    assert response.status_code == 422


# --- Verification ---------------------------------------------------------


def test_a_correct_credential_starts_a_session(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )
        me = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is True
    assert me.json()["data"]["principal"]["merchant_id"] == "merchant_demo"
    assert "agentpay_session" in response.cookies


def test_a_wrong_password_is_401_and_sets_no_cookie(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": "nope"}
        )

    assert response.status_code == 401
    assert "agentpay_session" not in response.cookies


def test_an_unknown_address_returns_the_same_message(operator_app: FastAPI) -> None:
    """Different messages for the two cases would enumerate who has an account."""
    _seed(operator_app)

    with TestClient(operator_app) as client:
        unknown = client.post(
            "/api/v1/auth/login", json={"email": "nobody@merchant.local", "password": PASSWORD}
        )
        wrong = client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": "nope"}
        )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_the_password_never_appears_in_the_response(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )

    assert PASSWORD not in response.text


def test_a_console_read_is_refused_without_a_session(operator_app: FastAPI) -> None:
    with TestClient(operator_app) as client:
        response = client.get("/api/v1/channels/connections")

    assert response.status_code == 401


def test_a_console_read_succeeds_after_signing_in(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )
        response = client.get("/api/v1/channels/connections")

    assert response.status_code == 200
    assert response.json()["data"]["merchant_id"] == "merchant_demo"


def test_logout_clears_the_session(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )
        client.post("/api/v1/auth/logout")
        me = client.get("/api/v1/auth/me")

    assert me.json()["data"]["authenticated"] is False


# --- The demo path, and where it now lives -------------------------------


def test_the_no_credential_path_is_not_the_login_endpoint(operator_app: FastAPI) -> None:
    """`POST /api/v1/auth/login` with no password is a 422, not a session.

    The no-credential behaviour was not removed -- local development and the e2e
    suite depend on it -- but it moved to `/api/v1/auth/demo-session` so the URL a
    human types cannot be the one that grants an administrator.
    """
    with TestClient(operator_app) as client:
        response = client.post("/api/v1/auth/login", json={"role": "platform_admin"})

    assert response.status_code == 422


def test_the_demo_path_still_works_in_local(operator_app: FastAPI) -> None:
    with TestClient(operator_app) as client:
        response = client.post("/api/v1/auth/demo-session", json={"role": "merchant_admin"})

    assert response.status_code == 200
    assert response.json()["data"]["principal"]["role"] == "merchant_admin"


def test_the_demo_path_is_refused_outside_local(app: FastAPI, settings) -> None:
    """The guard the docstring promises, asserted rather than assumed."""
    from apps.api.config import Settings
    from apps.api.main import create_app
    from apps.api.middleware.ratelimit import InMemoryRateLimitBackend

    staging = create_app(
        Settings(
            app_env="staging",
            # An explicit SQLite URL, because a staging environment may no longer fall
            # back to one implicitly: `validate_datastore_for_env` refuses that outside
            # `local` unless the fallback is opted into, so constructing this app would
            # otherwise fail on datastore policy before the demo-login guard is ever
            # reached. Naming SQLite explicitly is permitted anywhere, and it keeps this
            # test about the thing it is named after. See TestDatastoreSafety in
            # test_config.py for the policy itself.
            database_url="sqlite+pysqlite:///:memory:",
            jwt_secret="real",
            session_secret="real",
            channel_encryption_key="real-channel-key",
            payment_provider="fake",
            model_provider="mock",
        )
    )
    staging.state.rate_limit_backend = InMemoryRateLimitBackend()

    with TestClient(staging) as client:
        response = client.post("/api/v1/auth/demo-session", json={"role": "platform_admin"})

    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "demo_login_disabled"


def test_console_status_reports_which_paths_are_live(operator_app: FastAPI) -> None:
    """So the login page knows whether to render a form at all."""
    with TestClient(operator_app) as client:
        response = client.get("/api/v1/auth/console-status")

    data = response.json()["data"]
    assert data["demo_session_enabled"] is True
    assert data["signup_enabled"] is False
    assert data["password_min_length"] == 12


def test_signup_is_refused_by_default(operator_app: FastAPI) -> None:
    """An open signup on a payments gateway is a way for anyone to obtain a tenant.

    A tenant can publish a catalog, mint agent API keys, and receive pushed
    orders, so registration is off unless `ALLOW_CONSOLE_SIGNUP=1`.
    """
    with TestClient(operator_app) as client:
        response = client.post(
            "/api/v1/auth/signup",
            json={
                "merchant_id": "acme",
                "merchant_name": "Acme",
                "email": "owner@acme.test",
                "display_name": "Owner",
                "password": PASSWORD,
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "signup_disabled"


# --- Change password ------------------------------------------------------


def test_changing_a_password_requires_the_current_one(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )
        wrong = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "not-it", "new_password": "a-brand-new-password"},
        )
        right = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        )
        old_password_still_works = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@merchant.local", "password": PASSWORD},
        )

    assert wrong.status_code == 401
    assert right.status_code == 200
    assert old_password_still_works.status_code == 401


def test_changing_a_password_needs_a_session(operator_app: FastAPI) -> None:
    with TestClient(operator_app) as client:
        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        )

    assert response.status_code == 401


class TestLoginRateLimit:
    """The declared login limit, which is a security control rather than hygiene.

    The per-account lockout in `operator_auth` stops one account being ground
    down; this stops one address trying a thousand different accounts, which the
    lockout would never notice. Both halves are needed.
    """

    def test_repeated_failed_logins_are_throttled(self, operator_app: FastAPI) -> None:
        _seed(operator_app)

        with TestClient(operator_app) as client:
            statuses = [
                client.post(
                    "/api/v1/auth/login",
                    json={"email": "admin@merchant.local", "password": f"guess-{index}-xxxxx"},
                ).status_code
                for index in range(14)
            ]

        # The first failures are 401 (wrong password); past the declared limit
        # they become 429. Anything else -- say every one staying 401 -- means
        # the limit is not actually attached to this route.
        assert 401 in statuses
        assert 429 in statuses
        assert statuses.index(429) < len(statuses)


def test_a_weak_new_password_is_refused(operator_app: FastAPI) -> None:
    _seed(operator_app)

    with TestClient(operator_app) as client:
        client.post(
            "/api/v1/auth/login", json={"email": "admin@merchant.local", "password": PASSWORD}
        )
        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "short"},
        )

    assert response.status_code == 422
