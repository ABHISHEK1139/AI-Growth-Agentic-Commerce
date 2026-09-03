"""Exception to envelope translation.

The interesting case is the unexpected one. An exception string routinely carries
a DSN, a bound SQL parameter, a provider payload, or an API key, and a traceback
is a map of the codebase. So the contract is asymmetric on purpose: everything is
logged, nothing but the code is returned. The tests below assert both halves,
because either one alone is a bug — a silent 500 is undebuggable, and a chatty one
is a disclosure.

Domain errors are the opposite: they are expected outcomes, so the code, the
status, the retryable flag, and any recovery the service attached all travel to
the client, and the status is never chosen by a router.

agentpay:allow-credential-shapes - one test raises an exception whose message
contains a credential-shaped string, which is the only way to prove the response
and the log line are both scrubbed. It is not a real key.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from apps.api.middleware.context import REQUEST_ID_HEADER, RequestContextMiddleware
from apps.api.middleware.errors import (
    UnhandledExceptionMiddleware,
    install_exception_handlers,
)
from packages.errors.exceptions import (
    DomainError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
)
from packages.errors.exceptions import ValidationError as DomainValidationError
from packages.errors.registry import ErrorCode, spec_for
from packages.observability.logging import REDACTED
from packages.schemas.envelope import NextAction

#: Shaped like a Razorpay test key so the redactor has something to catch.
#: Structurally valid, cryptographically worthless.
FAKE_KEY = "rzp_test_" + "A1b2C3d4E5"
BOOM = f"boom {FAKE_KEY}"
#: Not numeric, so it fails `int` parsing and lands in Pydantic's echoed input.
FAKE_CARD = "4111-1111-1111-1111"


class _Body(BaseModel):
    amount_minor: int
    currency: str


def _build_app(*, with_handlers: bool = True) -> FastAPI:
    """The middleware stack from `install_middleware`, minus CORS, plus test routes.

    Built by hand rather than through `create_app` because these cases need routes
    that raise, and no production router raises on demand.
    """
    app = FastAPI()
    if with_handlers:
        install_exception_handlers(app)

    @app.get("/raise/domain")
    def _domain() -> dict:
        raise NotFoundError("No such offer.", details={"offer_id": "off_missing"})

    @app.get("/raise/domain-with-actions")
    def _domain_with_actions() -> dict:
        raise DomainError(
            code=ErrorCode.PRICE_CHANGED,
            details={"old_amount_minor": 649900, "new_amount_minor": 679900},
            next_actions=[
                NextAction(action="REFRESH_COMPARISON", label="Get fresh prices."),
                NextAction(action="REQUEST_APPROVAL", label="Approve the new amount."),
            ],
        )

    @app.get("/raise/forbidden")
    def _forbidden() -> dict:
        raise ForbiddenError

    @app.get("/raise/retryable")
    def _retryable() -> dict:
        raise DomainError(code=ErrorCode.VERSION_CONFLICT)

    @app.get("/raise/rate-limited")
    def _rate_limited() -> dict:
        raise RateLimitedError

    @app.get("/raise/domain-validation")
    def _domain_validation() -> dict:
        raise DomainValidationError("Quantity must be positive.")

    @app.get("/raise/unexpected")
    def _unexpected() -> dict:
        raise RuntimeError(BOOM)

    @app.get("/raise/zero-division")
    def _zero_division() -> dict:
        return {"value": 1 // 0}

    @app.post("/echo")
    def _echo(body: _Body) -> dict:
        return {"amount_minor": body.amount_minor}

    @app.get("/ok")
    def _ok() -> dict:
        return {"ok": True}

    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


@pytest.fixture
def error_client() -> Iterator[TestClient]:
    with TestClient(_build_app()) as client:
        yield client


class TestDomainErrors:
    def test_status_and_code_come_from_the_registry(self, error_client: TestClient) -> None:
        response = error_client.get("/raise/domain")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_the_response_is_the_error_envelope(self, error_client: TestClient) -> None:
        payload = error_client.get("/raise/domain").json()

        assert set(payload) == {"ok", "request_id", "error"}
        assert payload["ok"] is False
        assert payload["request_id"] is not None
        assert set(payload["error"]) == {"code", "message", "retryable", "details"}

    def test_the_message_and_details_from_the_raise_site_are_returned(
        self, error_client: TestClient
    ) -> None:
        payload = error_client.get("/raise/domain").json()

        assert payload["error"]["message"] == "No such offer."
        assert payload["error"]["details"] == {"offer_id": "off_missing"}

    def test_a_bare_raise_still_returns_the_registry_sentence(
        self, error_client: TestClient
    ) -> None:
        payload = error_client.get("/raise/forbidden").json()

        assert payload["error"]["message"] == spec_for(ErrorCode.FORBIDDEN).message

    @pytest.mark.parametrize(
        ("path", "status", "code", "retryable"),
        [
            ("/raise/domain", 404, "NOT_FOUND", False),
            ("/raise/forbidden", 403, "FORBIDDEN", False),
            ("/raise/domain-with-actions", 409, "PRICE_CHANGED", False),
            ("/raise/retryable", 409, "VERSION_CONFLICT", True),
            ("/raise/rate-limited", 429, "RATE_LIMITED", True),
            ("/raise/domain-validation", 422, "VALIDATION_ERROR", False),
        ],
    )
    def test_each_code_lands_on_its_documented_status(
        self, error_client: TestClient, path: str, status: int, code: str, retryable: bool
    ) -> None:
        response = error_client.get(path)

        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["retryable"] is retryable

    def test_recoveries_attached_at_the_raise_site_reach_the_client(
        self, error_client: TestClient
    ) -> None:
        """Requirement 31.10: the frontend renders recovery from what it is
        handed, so the service's `next_actions` must survive transport."""
        payload = error_client.get("/raise/domain-with-actions").json()

        assert [a["action"] for a in payload["next_actions"]] == [
            "REFRESH_COMPARISON",
            "REQUEST_APPROVAL",
        ]

    def test_correlation_headers_are_present_on_a_domain_error(
        self, error_client: TestClient
    ) -> None:
        response = error_client.get("/raise/domain")

        assert response.headers[REQUEST_ID_HEADER]
        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]

    def test_a_domain_error_is_logged_as_a_warning_not_an_error(
        self, error_client: TestClient, logs
    ) -> None:
        """An expected outcome must not page anyone."""
        error_client.get("/raise/domain")
        line = logs.with_event("DOMAIN_ERROR")[0]

        assert line["level"] == "WARNING"
        assert line["error_code"] == "NOT_FOUND"
        assert line["status"] == 404
        assert line["path"] == "/raise/domain"

    def test_the_access_log_reports_the_code(self, error_client: TestClient, logs) -> None:
        error_client.get("/raise/domain")

        assert logs.with_event("REQUEST_COMPLETED")[0]["error_code"] == "NOT_FOUND"


class TestDomainErrorsEscapingTheHandlers:
    """A domain error raised outside a route — a dependency, another middleware —
    still deserves its registry answer rather than a bare 500."""

    def test_the_middleware_answers_when_no_handler_is_registered(self) -> None:
        with TestClient(_build_app(with_handlers=False)) as client:
            response = client.get("/raise/domain")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert response.headers[REQUEST_ID_HEADER]


class TestUnexpectedExceptions:
    def test_the_status_and_code_are_generic(self, error_client: TestClient) -> None:
        response = error_client.get("/raise/unexpected")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"

    def test_the_exception_detail_is_not_returned(self, error_client: TestClient) -> None:
        raw = error_client.get("/raise/unexpected").text

        assert "boom" not in raw
        assert FAKE_KEY not in raw
        assert "rzp_" not in raw
        assert "RuntimeError" not in raw
        assert "Traceback" not in raw
        assert "test_middleware_errors" not in raw

    def test_the_message_is_the_registry_default(self, error_client: TestClient) -> None:
        payload = error_client.get("/raise/unexpected").json()

        assert payload["error"]["message"] == spec_for(ErrorCode.INTERNAL_ERROR).message
        assert payload["error"]["details"] == {}

    def test_internal_error_is_not_advertised_as_retryable(self, error_client: TestClient) -> None:
        """On a payment endpoint a blind client retry is the more expensive
        mistake than a manual one."""
        assert error_client.get("/raise/unexpected").json()["error"]["retryable"] is False

    def test_the_response_is_still_a_well_formed_envelope(self, error_client: TestClient) -> None:
        payload = error_client.get("/raise/unexpected").json()

        assert set(payload) == {"ok", "request_id", "error"}
        assert payload["ok"] is False
        assert payload["request_id"] is not None

    def test_correlation_headers_survive_a_500(self, error_client: TestClient) -> None:
        """Without our own catch, Starlette's `ServerErrorMiddleware` would answer
        from above our stack: no envelope, no identifiers, nothing to quote."""
        response = error_client.get("/raise/unexpected")

        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]

    def test_the_traceback_is_logged(self, error_client: TestClient, logs) -> None:
        error_client.get("/raise/unexpected")
        line = logs.with_event("UNHANDLED_EXCEPTION")[0]

        assert line["level"] == "ERROR"
        assert line["error_code"] == "INTERNAL_ERROR"
        assert line["exception_type"] == "RuntimeError"
        assert line["path"] == "/raise/unexpected"
        assert "Traceback" in line["exception"]
        assert "RuntimeError" in line["exception"]
        # The detail the client was denied is the detail an operator needs.
        assert "boom" in line["exception"]

    def test_the_logged_traceback_is_still_redacted(self, error_client: TestClient, logs) -> None:
        """Logging the detail is not licence to log a credential. The formatter
        scrubs the traceback text by shape, not by key name."""
        error_client.get("/raise/unexpected")
        line = logs.with_event("UNHANDLED_EXCEPTION")[0]

        assert FAKE_KEY not in line["exception"]
        assert FAKE_KEY not in logs.text
        assert REDACTED in line["exception"]

    def test_the_access_log_reports_the_failure(self, error_client: TestClient, logs) -> None:
        error_client.get("/raise/unexpected")
        line = logs.with_event("REQUEST_COMPLETED")[0]

        assert line["status"] == 500
        assert line["outcome"] == "server_error"
        assert line["error_code"] == "INTERNAL_ERROR"

    def test_a_non_exception_class_of_failure_is_handled_the_same(
        self, error_client: TestClient
    ) -> None:
        """Any escaping exception, not just the ones we imagined."""
        response = error_client.get("/raise/zero-division")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "ZeroDivisionError" not in response.text

    def test_one_failure_does_not_poison_the_next_request(self, error_client: TestClient) -> None:
        error_client.get("/raise/unexpected")

        assert error_client.get("/ok").status_code == 200


class TestFrameworkExceptions:
    def test_an_unmatched_route_gets_the_same_envelope(self, error_client: TestClient) -> None:
        """Otherwise an external buyer meets two different error shapes depending
        on whether it mistyped a path or hit a real failure."""
        response = error_client.get("/nope")
        payload = response.json()

        assert response.status_code == 404
        assert payload["ok"] is False
        assert payload["error"]["code"] == "NOT_FOUND"
        assert payload["request_id"] is not None

    def test_an_unsupported_method_gets_the_same_envelope(self, error_client: TestClient) -> None:
        response = error_client.post("/ok")

        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


class TestRequestValidation:
    def test_a_malformed_body_is_reported_as_a_validation_error(
        self, error_client: TestClient
    ) -> None:
        response = error_client.post("/echo", json={"amount_minor": "not a number"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_the_location_of_every_problem_is_reported(self, error_client: TestClient) -> None:
        """Where it was wrong, not what was sent. A client can still fix it."""
        payload = error_client.post("/echo", json={"amount_minor": "x"}).json()
        fields = payload["error"]["details"]["fields"]
        locations = {tuple(field["location"]) for field in fields}

        assert ("body", "amount_minor") in locations
        assert ("body", "currency") in locations
        assert all(field["type"] and field["message"] for field in fields)

    def test_the_submitted_value_is_not_echoed_back(self, error_client: TestClient) -> None:
        """Pydantic's error list includes the offending input. On an address or a
        payment payload that is buyer data."""
        raw = error_client.post(
            "/echo",
            json={"amount_minor": FAKE_CARD, "currency": "INR"},
        ).text

        assert FAKE_CARD not in raw

    def test_the_validation_failure_is_logged_without_the_payload(
        self, error_client: TestClient, logs
    ) -> None:
        error_client.post("/echo", json={"amount_minor": FAKE_CARD, "currency": "INR"})
        line = logs.with_event("REQUEST_VALIDATION_FAILED")[0]

        assert line["error_code"] == "VALIDATION_ERROR"
        assert line["field_count"] >= 1
        assert FAKE_CARD not in logs.text
