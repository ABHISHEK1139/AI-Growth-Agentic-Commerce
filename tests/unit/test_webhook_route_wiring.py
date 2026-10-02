"""Webhook routes must have working dependency injection, not just a registered path.

The bug this guards against
---------------------------
``razorpay_webhook`` declared its settings as ``settings: AppSettings | None = None``,
where ``AppSettings`` is ``Annotated[Settings, Depends(settings_for)]``.

Widening an ``Annotated`` alias with ``| None`` and giving it a default made FastAPI
read the parameter as an *optional value with no declared dependency*. ``settings_for``
was silently dropped from the route's dependency list and ``None`` was passed to the
handler, which then raised ``Settings not configured``.

The failure was invisible to every existing check:

- the module imported cleanly;
- the route registered, so ``/api/v1/payments/razorpay/webhook`` appeared in the
  OpenAPI schema and in a route listing;
- the 1,900-test suite stayed green, because no test posted to this path;
- only an actual request revealed ``500`` on every delivery, permanently.

A payment-capture webhook that always 500s means payments are never confirmed from the
provider side. That is a total outage of the confirmation path, reached by an
annotation that reads as if it injects settings.

Why assert on the resolved dependency rather than the response
--------------------------------------------------------------
The response assertion below is the behavioural check that matters. The dependency
assertion is here because it fails *earlier and more precisely*: it names the exact
mechanism, so a future regression points at the cause rather than at "500 somewhere".
It also catches the inverse mistake -- a handler that receives a valid ``Settings`` for
the wrong reason (a default constructed at import time, say), which would satisfy a
response-only test while decoupling the endpoint from the app it runs in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.db import get_db

RAZORPAY_CHECKOUT_WEBHOOK = "/api/v1/payments/razorpay/webhook"
SHARED_WEBHOOK = "/api/v1/webhooks/razorpay"

PAYLOAD: dict[str, Any] = {"event": "payment.captured", "payload": {"payment": {}}}

# Both webhook routes must exist. Asserting this explicitly means that deleting or
# renaming a route fails here with a clear message instead of silently leaving a
# provider pointed at a 404.
KNOWN_WEBHOOK_ROUTES = (SHARED_WEBHOOK, RAZORPAY_CHECKOUT_WEBHOOK)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """The real application with the database stubbed and processing faked.

    Stubbing ``process_webhook`` short-circuits the HMAC check, so this fixture is only
    for tests about *routing and injection* -- whether a delivery reaches the handler
    at all. Signature behaviour is asserted with ``real_client`` instead.
    """
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with _faked_processing(app), TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def real_client(app: FastAPI) -> Iterator[TestClient]:
    """The real application, real HMAC verification, database stubbed.

    Safe because ``process_webhook`` verifies the signature as its *first* step and
    raises before touching the session. An unsigned or wrongly signed delivery is
    therefore rejected with no database access at all, and the ``MagicMock`` session is
    never actually exercised.
    """
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@contextmanager
def _faked_processing(app: FastAPI) -> Iterator[None]:
    from services.payments import webhooks as webhooks_module

    original = webhooks_module.WebhookProcessor.process_webhook

    def _ok(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "processed", "duplicate": False}

    webhooks_module.WebhookProcessor.process_webhook = _ok  # type: ignore[method-assign]
    try:
        yield
    finally:
        webhooks_module.WebhookProcessor.process_webhook = original  # type: ignore[method-assign]


def _post(client: TestClient, path: str) -> Any:
    return client.post(
        path,
        content=json.dumps(PAYLOAD),
        headers={
            "content-type": "application/json",
            "X-Razorpay-Signature": "test-signature",
        },
    )


@pytest.mark.parametrize("path", KNOWN_WEBHOOK_ROUTES)
def test_webhook_route_is_registered(app: FastAPI, path: str) -> None:
    """Both webhook endpoints must be present on the application.

    A provider is configured with a literal URL. If the route disappears, every
    delivery 404s and retries forever, so presence is a real requirement rather than
    an implementation detail.
    """
    registered = {getattr(route, "path", None) for route in app.routes}
    assert path in registered, (
        f"{path} is not registered. Providers configured with this URL would get a "
        "404 on every delivery."
    )


@pytest.mark.parametrize("path", KNOWN_WEBHOOK_ROUTES)
def test_webhook_route_accepts_a_delivery(client: TestClient, path: str) -> None:
    """A delivery must be accepted, not rejected for a wiring reason.

    ``WEBHOOK_SIGNATURE_INVALID`` (400) is a legitimate outcome of a bad signature and
    is deliberately *not* treated as failure here -- the signature is a stub. What
    must never happen is a 500, which is what this endpoint returned for every request
    while its settings dependency was silently dropped.
    """
    response = _post(client, path)

    assert response.status_code != 500, (
        f"{path} returned 500: {response.text}. A 500 here means the handler could "
        "not reach its own configuration, so no delivery is ever processed."
    )
    assert response.status_code < 500, response.text


def test_razorpay_webhook_resolves_its_settings_dependency(app: FastAPI) -> None:
    """The route must depend on ``settings_for``; naming it directly is the point.

    ``AppSettings`` is only an injection when the ``Annotated`` metadata survives.
    Adding ``| None`` or a default strips it, and FastAPI will not complain -- it just
    passes ``None``. Asserting the resolved dependency catches that at import time
    rather than at 3am when a payment webhook lands.
    """
    route = next(
        route for route in app.routes if getattr(route, "path", None) == RAZORPAY_CHECKOUT_WEBHOOK
    )
    dependency_names = {getattr(dep.call, "__name__", None) for dep in route.dependant.dependencies}

    assert "settings_for" in dependency_names, (
        f"{RAZORPAY_CHECKOUT_WEBHOOK} does not depend on settings_for; resolved "
        f"dependencies are {sorted(n for n in dependency_names if n)}. The handler's "
        "`AppSettings` annotation has been widened or given a default, which strips the "
        "Depends and makes the endpoint fail on every call."
    )


@pytest.mark.parametrize("path", KNOWN_WEBHOOK_ROUTES)
def test_webhook_routes_reject_a_missing_signature(real_client: TestClient, path: str) -> None:
    """An unsigned delivery is refused before any database work.

    The webhook secret is the only authentication on these endpoints -- there is no
    session and no API key -- so an unsigned request must never reach processing.

    Uses the real HMAC path, so this asserts genuine verification rather than a
    router-level header check that could be mistaken for it.
    """
    response = real_client.post(
        path,
        content=json.dumps(PAYLOAD),
        headers={"content-type": "application/json"},
    )

    assert response.status_code in (400, 401), (
        f"{path} accepted a delivery with no signature: {response.status_code} " f"{response.text}"
    )


def test_webhook_body_is_not_treated_as_a_signature(
    real_client: TestClient, path: str = RAZORPAY_CHECKOUT_WEBHOOK
) -> None:
    """A signature inside the request body must not authenticate anything.

    Guards against a future "simplification" to a Pydantic body model. HMAC-SHA256 is
    only valid over the exact bytes the provider signed, so the signature travels in a
    header and the body is only ever read as raw bytes. An attacker-supplied
    ``signature`` key in the JSON must carry no authority.
    """
    body = {
        "event": "payment.captured",
        "signature": "attacker-controlled",
        "payload": {"payment": {}},
    }
    response = real_client.post(
        path,
        content=json.dumps(body),
        headers={"content-type": "application/json"},
    )

    assert response.status_code in (400, 401), (
        f"{path} trusted a signature supplied inside the request body: "
        f"{response.status_code} {response.text}"
    )
