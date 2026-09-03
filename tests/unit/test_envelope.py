"""Envelope shapes are the contract every surface shares (design: "API Design").

Two shapes, and only two. The frontend and the external buyer agent both have one
error-handling path, which only works if every response really does look like one
of these. So the tests here assert the exact key set rather than "contains the
keys I happen to care about" — an extra top-level key is drift, and a missing one
breaks a client.

`next_actions` gets particular attention: Requirement 31.10 has the frontend
offering recovery without hardcoding failure knowledge, which is only possible if
services can attach actions and transport carries them through untouched.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError as PydanticValidationError

from apps.api.envelope import (
    current_request_id,
    error_payload,
    error_response,
    error_response_from_domain_error,
    from_service_result,
    probe_payload,
    success,
)
from packages.errors.exceptions import DomainError, ForbiddenError, NotFoundError
from packages.errors.registry import ErrorCode, spec_for
from packages.observability.context import correlation_scope
from packages.schemas.envelope import (
    EnvelopeWarning,
    ErrorBody,
    ErrorEnvelope,
    Evidence,
    NextAction,
    ServiceResult,
    SuccessEnvelope,
)

SUCCESS_KEYS = {"ok", "request_id", "data", "warnings", "evidence", "next_actions"}
ERROR_KEYS = {"ok", "request_id", "error"}
ERROR_BODY_KEYS = {"code", "message", "retryable", "details"}


def body_of(response) -> dict:  # noqa: ANN001 - JSONResponse
    return json.loads(response.body)


class TestSuccessEnvelopeShape:
    def test_exact_key_set(self) -> None:
        payload = success({"offer_id": "off_1"})

        assert set(payload) == SUCCESS_KEYS

    def test_ok_is_true_and_data_is_carried(self) -> None:
        payload = success({"offer_id": "off_1"})

        assert payload["ok"] is True
        assert payload["data"] == {"offer_id": "off_1"}

    def test_collections_default_to_empty_not_null(self) -> None:
        """A client iterating `warnings` must not have to null-check first."""
        payload = success()

        assert payload["data"] == {}
        assert payload["warnings"] == []
        assert payload["evidence"] == []
        assert payload["next_actions"] == []

    def test_request_id_comes_from_the_correlation_scope(self) -> None:
        with correlation_scope(request_id="req_abc123def456"):
            assert success()["request_id"] == "req_abc123def456"

    def test_request_id_is_null_outside_a_request(self) -> None:
        """A worker or a unit test has no request. The field stays present so the
        shape does not change depending on who built it."""
        assert current_request_id() is None
        assert success()["request_id"] is None

    def test_payload_is_json_serializable(self) -> None:
        payload = success(
            {"amount_minor": 649900},
            warnings=[EnvelopeWarning(code="STALE_PRICE", message="Price was refreshed.")],
            evidence=[Evidence(kind="offer", reference="off_1", summary="Listed price")],
            next_actions=[NextAction(action="REFRESH", label="Refresh the comparison.")],
        )

        assert json.loads(json.dumps(payload)) == payload

    def test_warnings_and_evidence_survive_serialization(self) -> None:
        payload = success(
            {},
            warnings=[EnvelopeWarning(code="STALE_PRICE", message="Price was refreshed.")],
            evidence=[Evidence(kind="offer", reference="off_1")],
        )

        assert payload["warnings"][0]["code"] == "STALE_PRICE"
        assert payload["evidence"][0]["reference"] == "off_1"


class TestNextActionsArePopulatable:
    """Requirement 31.10: the client offers recovery from what it was handed."""

    def test_a_success_envelope_can_carry_actions(self) -> None:
        payload = success(
            {},
            next_actions=[
                NextAction(
                    action="REQUEST_APPROVAL",
                    label="Approve this amount to continue.",
                    method="POST",
                    href="/api/v1/authorizations",
                    params={"checkout_id": "chk_1"},
                )
            ],
        )

        action = payload["next_actions"][0]
        assert action["action"] == "REQUEST_APPROVAL"
        assert action["label"] == "Approve this amount to continue."
        assert action["method"] == "POST"
        assert action["href"] == "/api/v1/authorizations"
        assert action["params"] == {"checkout_id": "chk_1"}

    def test_an_error_envelope_can_carry_actions(self) -> None:
        payload = error_payload(
            ErrorCode.PRICE_CHANGED,
            next_actions=[
                NextAction(action="REFRESH_COMPARISON", label="Get fresh prices."),
                NextAction(action="REQUEST_APPROVAL", label="Approve the new amount."),
            ],
        )

        assert [a["action"] for a in payload["next_actions"]] == [
            "REFRESH_COMPARISON",
            "REQUEST_APPROVAL",
        ]

    def test_empty_next_actions_are_omitted_from_an_error_envelope(self) -> None:
        """The documented error shape has no `next_actions`. Present only when a
        service actually knows a recovery, so a client can treat presence as
        meaning "there is something to offer"."""
        assert "next_actions" not in error_payload(ErrorCode.OFFER_EXPIRED)

    def test_an_action_needs_both_a_discriminator_and_a_label(self) -> None:
        """An action without a label gets rendered as a raw enum; a label without
        an action gets hardcoded in the frontend."""
        with pytest.raises(PydanticValidationError):
            NextAction(action="REFRESH", label="")
        with pytest.raises(PydanticValidationError):
            NextAction(action="", label="Refresh.")

    def test_an_action_rejects_unknown_fields(self) -> None:
        with pytest.raises(PydanticValidationError):
            NextAction(action="REFRESH", label="Refresh.", url="/somewhere")  # type: ignore[call-arg]


class TestErrorEnvelopeShape:
    def test_exact_key_set(self) -> None:
        payload = error_payload(ErrorCode.OFFER_EXPIRED)

        assert set(payload) == ERROR_KEYS

    def test_error_body_key_set_and_values(self) -> None:
        payload = error_payload(ErrorCode.OFFER_EXPIRED)

        assert payload["ok"] is False
        assert set(payload["error"]) == ERROR_BODY_KEYS
        assert payload["error"]["code"] == "OFFER_EXPIRED"
        assert payload["error"]["message"] == spec_for(ErrorCode.OFFER_EXPIRED).message
        assert payload["error"]["retryable"] is False
        assert payload["error"]["details"] == {}

    def test_code_serializes_as_the_bare_string(self) -> None:
        """Clients switch on it, so it must not arrive as `ErrorCode.X`."""
        payload = error_payload(ErrorCode.RATE_LIMITED)

        assert payload["error"]["code"] == "RATE_LIMITED"
        assert json.loads(json.dumps(payload))["error"]["code"] == "RATE_LIMITED"

    def test_retryable_comes_from_the_registry_not_the_caller(self) -> None:
        assert error_payload(ErrorCode.VERSION_CONFLICT)["error"]["retryable"] is True
        assert error_payload(ErrorCode.PRICE_CHANGED)["error"]["retryable"] is False

    def test_details_carry_structured_client_safe_context(self) -> None:
        payload = error_payload(
            ErrorCode.INVENTORY_UNAVAILABLE,
            details={"requested": 5, "available": 2},
        )

        assert payload["error"]["details"] == {"requested": 5, "available": 2}

    def test_message_can_be_overridden_at_the_raise_site(self) -> None:
        payload = error_payload(ErrorCode.OFFER_EXPIRED, message="That offer lapsed a minute ago.")

        assert payload["error"]["message"] == "That offer lapsed a minute ago."

    def test_request_id_is_stamped(self) -> None:
        with correlation_scope(request_id="req_abc123def456"):
            assert error_payload(ErrorCode.FORBIDDEN)["request_id"] == "req_abc123def456"


class TestErrorBodyFromCode:
    def test_defaults_are_read_from_the_registry(self) -> None:
        body = ErrorBody.from_code(ErrorCode.PAYMENT_TIMEOUT)

        assert body.message == spec_for(ErrorCode.PAYMENT_TIMEOUT).message
        assert body.retryable is True

    def test_in_band_code_reports_not_retryable_rather_than_null(self) -> None:
        body = ErrorBody.from_code(ErrorCode.MAX_DISCOUNT_EXCEEDED)

        assert body.retryable is False

    def test_body_is_frozen(self) -> None:
        """Once serialized into an envelope, an error body is a fact."""
        body = ErrorBody.from_code(ErrorCode.FORBIDDEN)

        with pytest.raises(PydanticValidationError):
            body.code = ErrorCode.NOT_FOUND


class TestEnvelopeModelsRejectDrift:
    @pytest.mark.parametrize(
        "model", [SuccessEnvelope, ErrorEnvelope, ServiceResult, NextAction, EnvelopeWarning]
    )
    def test_unknown_fields_are_refused(self, model: type) -> None:
        """`extra="forbid"` is what stops a router quietly inventing a key that a
        client then depends on."""
        assert model.model_config["extra"] == "forbid"

    def test_ok_cannot_be_flipped_on_a_success_envelope(self) -> None:
        with pytest.raises(PydanticValidationError):
            SuccessEnvelope(ok=False)  # type: ignore[arg-type]

    def test_ok_cannot_be_flipped_on_an_error_envelope(self) -> None:
        with pytest.raises(PydanticValidationError):
            ErrorEnvelope(ok=True, error=ErrorBody.from_code(ErrorCode.FORBIDDEN))  # type: ignore[arg-type]


class TestServiceResult:
    def test_transport_only_adds_the_request_id(self) -> None:
        """Services own the domain knowledge, transport owns correlation. This is
        the seam that keeps `next_actions` out of the routers."""
        result = ServiceResult(
            data={"checkout_id": "chk_1"},
            warnings=[EnvelopeWarning(code="STALE_PRICE", message="Price was refreshed.")],
            evidence=[Evidence(kind="offer", reference="off_1")],
            next_actions=[NextAction(action="CONFIRM", label="Confirm the order.")],
        )

        with correlation_scope(request_id="req_abc123def456"):
            payload = from_service_result(result)

        assert set(payload) == SUCCESS_KEYS
        assert payload["request_id"] == "req_abc123def456"
        assert payload["data"] == {"checkout_id": "chk_1"}
        assert payload["warnings"][0]["code"] == "STALE_PRICE"
        assert payload["evidence"][0]["reference"] == "off_1"
        assert payload["next_actions"][0]["action"] == "CONFIRM"

    def test_a_bare_result_still_produces_a_full_envelope(self) -> None:
        assert set(from_service_result(ServiceResult())) == SUCCESS_KEYS


class TestErrorResponse:
    @pytest.mark.parametrize(
        ("code", "status"),
        [
            (ErrorCode.OFFER_NOT_FOUND, 404),
            (ErrorCode.OFFER_EXPIRED, 409),
            (ErrorCode.POLICY_BLOCKED, 403),
            (ErrorCode.RATE_LIMITED, 429),
            (ErrorCode.PAYMENT_TIMEOUT, 504),
            (ErrorCode.VALIDATION_ERROR, 422),
            (ErrorCode.INTERNAL_ERROR, 500),
        ],
    )
    def test_status_comes_from_the_registry(self, code: ErrorCode, status: int) -> None:
        """The same code cannot arrive as 409 from one router and 400 from
        another, because no router chooses the status."""
        assert error_response(code).status_code == status

    def test_status_can_be_overridden_for_a_mapped_http_exception(self) -> None:
        response = error_response(ErrorCode.NOT_FOUND, status_code=410)

        assert response.status_code == 410
        assert body_of(response)["error"]["code"] == "NOT_FOUND"

    def test_headers_are_attached(self) -> None:
        response = error_response(ErrorCode.RATE_LIMITED, headers={"Retry-After": "17"})

        assert response.headers["Retry-After"] == "17"

    def test_body_is_the_error_envelope(self) -> None:
        with correlation_scope(request_id="req_abc123def456"):
            payload = body_of(error_response(ErrorCode.FORBIDDEN))

        assert set(payload) == ERROR_KEYS
        assert payload["request_id"] == "req_abc123def456"


class TestErrorResponseFromDomainError:
    def test_status_message_and_details_come_from_the_exception(self) -> None:
        exc = NotFoundError("No such offer.", details={"offer_id": "off_missing"})
        response = error_response_from_domain_error(exc)
        payload = body_of(response)

        assert response.status_code == 404
        assert payload["error"]["code"] == "NOT_FOUND"
        assert payload["error"]["message"] == "No such offer."
        assert payload["error"]["details"] == {"offer_id": "off_missing"}
        assert payload["error"]["retryable"] is False

    def test_next_actions_attached_at_the_raise_site_survive(self) -> None:
        exc = ForbiddenError(next_actions=[NextAction(action="CONTACT", label="Ask an admin.")])

        assert body_of(error_response_from_domain_error(exc))["next_actions"][0]["action"] == (
            "CONTACT"
        )

    def test_an_explicit_code_overrides_the_subclass_default(self) -> None:
        exc = DomainError(code=ErrorCode.PRICE_CHANGED)
        response = error_response_from_domain_error(exc)

        assert response.status_code == 409
        assert body_of(response)["error"]["code"] == "PRICE_CHANGED"

    def test_a_bare_domain_error_still_reads_as_a_sentence(self) -> None:
        """A service that raises without words must not produce an empty message."""
        assert DomainError(code=ErrorCode.OFFER_EXPIRED).message == (
            spec_for(ErrorCode.OFFER_EXPIRED).message
        )

    def test_retryable_is_derived_from_the_code(self) -> None:
        assert DomainError(code=ErrorCode.VERSION_CONFLICT).retryable is True
        assert DomainError(code=ErrorCode.OFFER_EXPIRED).retryable is False


class TestProbePayload:
    def test_shape_matches_the_success_envelope(self) -> None:
        """A probe is the one documented `ok: false` without an `error`, so its
        shape still has to be the envelope a client can parse."""
        payload = probe_payload(ok=True, data={"service": "agentpay"})

        assert set(payload) == SUCCESS_KEYS
        assert payload["ok"] is True

    def test_a_failing_probe_reports_which_component_is_down(self) -> None:
        payload = probe_payload(
            ok=False,
            data={"postgres": {"ok": False, "error": "OperationalError"}},
            warnings=[EnvelopeWarning(code="DATASTORE_UNREACHABLE", message="postgres is down.")],
        )

        assert payload["ok"] is False
        assert payload["data"]["postgres"]["ok"] is False
        assert payload["warnings"][0]["code"] == "DATASTORE_UNREACHABLE"

    def test_request_id_is_stamped(self) -> None:
        with correlation_scope(request_id="req_abc123def456"):
            assert probe_payload(ok=True, data={})["request_id"] == "req_abc123def456"
