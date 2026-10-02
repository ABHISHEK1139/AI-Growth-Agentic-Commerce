"""Security response headers are present on every response, not only successful ones.

Why this file asserts what it does
----------------------------------
A header middleware that only touches 200 responses is not a security control. The
failure mode it invites is a deployment where ``/health`` and error envelopes look fine
in a spot check, and the response that actually carried a user's payment state did not
carry the header. These tests therefore assert on error responses and 404s explicitly,
not just the happy path.

The HSTS test drives a real HTTPS request rather than asserting on a settings flag,
because the point of the gating is behavioural: the header must depend on how the
request arrived, and only an actual TLS request can show that.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.db import get_db

# Applied to every response regardless of path.
UNCONDITIONAL_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_response_carries_security_headers(client: TestClient) -> None:
    """Even the most boring response is covered.

    ``/health`` is the endpoint most likely to be spot-checked by an operator, so it
    is the one most likely to create a false sense that headers are configured.
    """
    response = client.get("/health")
    assert response.status_code == 200
    for header, expected in UNCONDITIONAL_HEADERS.items():
        assert response.headers.get(header) == expected, (
            f"{header} missing or wrong on /health: " f"{response.headers.get(header)!r}"
        )


def test_error_response_carries_security_headers(client: TestClient) -> None:
    """A 404 must carry the headers too.

    This is the assertion that distinguishes a real control from decoration. A
    middleware placed inside the exception handler, or applied only on the success
    path, passes a happy-path test and fails here.
    """
    response = client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404, response.text
    for header, expected in UNCONDITIONAL_HEADERS.items():
        assert response.headers.get(header) == expected, (
            f"{header} missing on a 404 response: {response.headers.get(header)!r}. A "
            "header that only appears on 200s is not a control."
        )


def test_api_responses_are_not_stored_by_a_shared_cache(client: TestClient) -> None:
    """Per-user API responses must not be written to a shared cache.

    A reverse proxy or CDN in front of the API would otherwise be free to retain
    merchant and buyer JSON and serve it to the next caller.
    """
    response = client.get("/api/v1/offers")
    assert response.headers.get("cache-control") == "no-store"


def test_health_stays_cacheable(client: TestClient) -> None:
    """``no-store`` is scoped, not blanket.

    Marking the liveness probe uncacheable would make an orchestrator's probe hit the
    database on every interval, and would mean the header had been applied to the one
    response where it carries no security value.
    """
    response = client.get("/health")
    assert "no-store" not in response.headers.get("cache-control", "")


def test_hsts_is_sent_over_https(app: FastAPI) -> None:
    """HSTS appears once the request genuinely arrived over TLS.

    The TLS termination proxy normally speaks plain HTTP to the app and forwards
    ``X-Forwarded-Proto``. ``TestClient`` with an ``https://`` base URL is what
    produces a request whose ``url.scheme`` is https, which is the condition the
    middleware keys off.
    """
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        response = c.get("/health")
    app.dependency_overrides.clear()

    hsts = response.headers.get("strict-transport-security")

    assert hsts is not None, "HSTS missing on an HTTPS request"
    assert "max-age=31536000" in hsts
    # includeSubDomains is deliberately omitted: pinning subdomains would strand any
    # subdomain not served over TLS. Asserting its absence keeps it from creeping in.
    assert "includeSubDomains" not in hsts


def test_hsts_is_not_sent_over_plain_http(app: FastAPI) -> None:
    """HSTS over HTTP is ignored by browsers and asserts protection that is absent.

    Sending it unconditionally would also let a host that never served TLS pin users
    to it for a year.
    """
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as c:
        response = c.get("/health")
    app.dependency_overrides.clear()

    assert "strict-transport-security" not in response.headers, (
        "HSTS was sent over a plain HTTP request. Browsers ignore it there, so it is "
        "a claim of protection that is not actually in effect."
    )


def test_no_content_security_policy_on_api_responses(client: TestClient) -> None:
    """The API deliberately does not set CSP, and that must stay a decision.

    CSP only protects a *document* that loads scripts. This is a JSON API; a policy
    here would protect nothing while implying the application is covered when the
    frontend — the part that actually loads Razorpay's script — is not.

    This test exists so that adding a header nobody reasoned about fails loudly. The
    correct CSP work belongs on the Next.js app and is tracked in the readiness plan.
    """
    response = client.get("/health")
    assert "content-security-policy" not in response.headers, (
        "A CSP was set on the JSON API. It cannot protect anything here and creates a "
        "false impression of coverage. CSP belongs on the web document."
    )


def test_headers_survive_a_rate_limited_response(app: FastAPI) -> None:
    """A 429 must carry the headers as well.

    The rate limiter is the layer most likely to short-circuit before the security
    middleware runs, depending on stack order. This asserts the two interact
    correctly.

    A backend whose ``hit`` always reports the limit is the simplest way to guarantee
    a 429 without depending on the configured per-route rule, which differs between
    the webhook path and everything else.
    """

    class _AlwaysLimited:
        """Reports every request as over the limit."""

        def hit(self, key: str, window_seconds: int) -> int:
            return window_seconds * 1000

    # Assigned after the fixture, which installs its own in-memory backend; setting it
    # before would simply be overwritten.
    app.state.rate_limit_backend = _AlwaysLimited()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app, raise_server_exceptions=False) as c:
        # A real, non-exempt route. An unrouted path 404s before a rule is resolved,
        # and the webhook routes declare their own permissive limit, so neither would
        # prove the default path was reached.
        response = c.get("/api/v1/auth/me")
    app.dependency_overrides.clear()

    assert response.status_code == 429, response.text
    for header, expected in UNCONDITIONAL_HEADERS.items():
        assert response.headers.get(header) == expected, (
            f"{header} missing on a rate-limited response: " f"{response.headers.get(header)!r}"
        )
