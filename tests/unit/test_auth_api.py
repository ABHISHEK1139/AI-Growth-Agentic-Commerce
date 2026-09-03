"""HTTP authentication boundaries for sessions and external agents (Requirement 24)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from apps.api.auth import require_scopes, session_principal, start_session
from packages.security.principals import Principal, Role, Scope
from packages.security.tokens import issue_access_token


def _install_protected_test_routes(app: FastAPI) -> None:
    @app.get("/__test__/session")
    async def session_only(
        principal: Principal = Depends(session_principal),
    ) -> dict[str, Any]:
        return {"subject": principal.subject, "method": principal.method.value}

    @app.get("/__test__/payment-scope")
    async def payment_scope(
        principal: Principal = Depends(require_scopes(Scope.PAYMENT_WRITE)),
    ) -> dict[str, Any]:
        return {"subject": principal.subject}


def test_api_key_exchange_returns_a_scoped_bearer_token(app: FastAPI, settings) -> None:
    registry = app.state.api_client_registry
    api_key, _ = registry.issue(
        merchant_id="merchant_demo",
        role=Role.BUYER,
        buyer_id="buyer_ada",
        scopes={Scope.CATALOG_READ, Scope.CHECKOUT_WRITE, Scope.PAYMENT_WRITE},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/auth/token",
            json={"api_key": api_key, "scopes": ["catalog:read", "checkout:write"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["token_type"] == "Bearer"
    assert payload["data"]["scopes"] == ["catalog:read", "checkout:write"]
    assert api_key not in response.text


def test_unknown_api_key_is_denied_without_echoing_it(app: FastAPI) -> None:
    presented = "ak_unknown-agent-key"

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/auth/token",
            json={"api_key": presented, "scopes": ["catalog:read"]},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert presented not in response.text


def test_missing_scope_returns_403(app: FastAPI, settings) -> None:
    """Requirement 24.2: authenticating is not enough when the scope is absent."""
    _install_protected_test_routes(app)
    token = issue_access_token(
        secret=settings.jwt_secret,
        subject="buyer_ada",
        role=Role.BUYER,
        merchant_id="merchant_demo",
        buyer_id="buyer_ada",
        scopes={Scope.CATALOG_READ},
        ttl_seconds=60,
    ).token

    with TestClient(app) as client:
        response = client.get(
            "/__test__/payment-scope", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "FORBIDDEN"
    assert payload["error"]["details"]["missing_scopes"] == ["payment:write"]


def test_expired_bearer_token_is_denied_at_http_boundary(app: FastAPI, settings) -> None:
    """Requirement 24.7: expiry is enforced before a protected route executes."""
    _install_protected_test_routes(app)
    token = issue_access_token(
        secret=settings.jwt_secret,
        subject="buyer_ada",
        role=Role.BUYER,
        merchant_id="merchant_demo",
        buyer_id="buyer_ada",
        scopes={Scope.PAYMENT_WRITE},
        ttl_seconds=60,
        now=time.time() - 61,
    ).token

    with TestClient(app) as client:
        response = client.get(
            "/__test__/payment-scope", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.json()["error"]["details"]["reason"] == "expired"


def test_web_surface_accepts_a_signed_session_cookie(app: FastAPI, settings) -> None:
    _install_protected_test_routes(app)

    with TestClient(app) as client:
        from fastapi import Response

        cookie_response = Response()
        issued = start_session(
            cookie_response,
            settings=settings,
            subject="merchant_admin_1",
            role=Role.MERCHANT_ADMIN,
            merchant_id="merchant_demo",
        )
        client.cookies.set("agentpay_session", issued.token)
        response = client.get("/__test__/session")

    assert response.status_code == 200
    assert response.json() == {"subject": "merchant_admin_1", "method": "session"}
    set_cookie = cookie_response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_web_session_route_refuses_a_bearer_token(app: FastAPI, settings) -> None:
    _install_protected_test_routes(app)
    token = issue_access_token(
        secret=settings.jwt_secret,
        subject="merchant_admin_1",
        role=Role.MERCHANT_ADMIN,
        merchant_id="merchant_demo",
        scopes={Scope.CATALOG_READ},
        ttl_seconds=60,
    ).token

    with TestClient(app) as client:
        response = client.get("/__test__/session", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "session_required"


def test_session_login_and_logout_endpoints(app: FastAPI) -> None:
    with TestClient(app) as client:
        # 1. Initially unauthenticated on /auth/me
        me_resp = client.get("/api/v1/auth/me")
        assert me_resp.status_code == 200
        assert me_resp.json()["data"]["authenticated"] is False

        # 2. Login to mint session cookie
        login_resp = client.post(
            "/api/v1/auth/session",
            json={
                "role": "merchant_admin",
                "merchant_id": "merchant_demo",
                "subject": "admin_test",
            },
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        assert login_data["ok"] is True
        assert login_data["data"]["authenticated"] is True
        assert login_data["data"]["principal"]["subject"] == "admin_test"
        assert login_data["data"]["principal"]["role"] == "merchant_admin"
        assert "agentpay_session" in login_resp.cookies

        # 3. Check /auth/me with active session
        me_auth_resp = client.get("/api/v1/auth/me")
        assert me_auth_resp.status_code == 200
        assert me_auth_resp.json()["data"]["authenticated"] is True
        assert me_auth_resp.json()["data"]["principal"]["subject"] == "admin_test"

        # 4. Logout to clear session
        logout_resp = client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json()["data"]["authenticated"] is False
