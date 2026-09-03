"""Task 1 acceptance: the process is serving, and the probes tell the truth."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["service"] == "agentpay"
    assert body["data"]["env"] == "local"


def test_health_reports_fakes_as_the_default_providers(client: TestClient) -> None:
    """A clean clone must run with no credentials. That is a testable claim."""
    body = client.get("/health").json()

    assert body["data"]["payment_provider"] == "fake"
    assert body["data"]["model_provider"] == "mock"


def test_health_needs_no_datastore(client: TestClient) -> None:
    """Liveness must not depend on PostgreSQL or Redis being reachable."""
    with patch("apps.api.db.check_database", return_value=(False, "OperationalError")):
        assert client.get("/health").status_code == 200


class TestHealthDb:
    def test_returns_503_when_a_datastore_is_unreachable(self, client: TestClient) -> None:
        with (
            patch(
                "apps.api.routers.health.check_database", return_value=(False, "OperationalError")
            ),
            patch("apps.api.routers.health._check_redis", return_value=(False, "ConnectionError")),
        ):
            response = client.get("/health/db")

        assert response.status_code == 503
        body = response.json()
        assert body["ok"] is False
        assert body["data"]["postgres"]["ok"] is False
        assert body["data"]["redis"]["ok"] is False

    def test_returns_200_when_both_datastores_answer(self, client: TestClient) -> None:
        with (
            patch("apps.api.routers.health.check_database", return_value=(True, None)),
            patch("apps.api.routers.health._check_redis", return_value=(True, None)),
        ):
            response = client.get("/health/db")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_never_leaks_the_connection_string(self, client: TestClient) -> None:
        """A driver error message routinely embeds the DSN, and the DSN embeds a
        password. The probe must report an exception class, nothing more."""
        with (
            patch(
                "apps.api.routers.health.check_database", return_value=(False, "OperationalError")
            ),
            patch("apps.api.routers.health._check_redis", return_value=(True, None)),
        ):
            raw = client.get("/health/db").text

        assert "agentpay:agentpay" not in raw
        assert "postgresql" not in raw
        assert "5432" not in raw


@pytest.mark.parametrize("path", ["/health", "/health/db"])
def test_probes_carry_a_request_id_field(client: TestClient, path: str) -> None:
    """Populated by the middleware in Task 2; the field exists from Task 1 so the
    contract does not change underneath the frontend later."""
    assert "request_id" in client.get(path).json()
