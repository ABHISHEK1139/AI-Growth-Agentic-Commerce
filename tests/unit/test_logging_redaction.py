"""NFR-9: logs must never carry a credential.

This suite is the reason the redaction filter exists. Each case is a way a secret
has plausibly reached a log line in a real system.

agentpay:allow-credential-shapes - the strings below are deliberately shaped like
credentials so the redactor can be proven to catch them. None is valid.
"""

from __future__ import annotations

import json
import logging

import pytest

from packages.observability.context import correlation_scope
from packages.observability.logging import REDACTED, JsonFormatter, redact


class TestRedactByKeyName:
    @pytest.mark.parametrize(
        "key",
        [
            "razorpay_key_secret",
            "RAZORPAY_WEBHOOK_SECRET",
            "password",
            "access_token",
            "api_key",
            "Authorization",
            "Cookie",
            "X-Razorpay-Signature",
            "card_number",
            "cvv",
            "jwt_secret",
            "model_api_key",
        ],
    )
    def test_sensitive_keys_are_masked(self, key: str) -> None:
        assert redact({key: "super-secret-value"})[key] == REDACTED

    @pytest.mark.parametrize(
        "key",
        [
            "authorization_id",
            "authorization_hash",
            "idempotency_key",
            "price_hash",
            "input_hash",
            "raw_body_hash",
            "storage_key",
            "signature_valid",
        ],
    )
    def test_structural_keys_survive(self, key: str) -> None:
        """The audit vocabulary contains "auth", "key", and "signature". Redacting
        it would blind the timeline we are building the system to produce."""
        assert redact({key: "auth_abc123"})[key] == "auth_abc123"


class TestRedactByValueShape:
    @pytest.mark.parametrize(
        "value",
        [
            "rzp_test_A1b2C3d4E5f6G7",
            "rzp_live_ZZZZZZZZZZZZZZ",
            "sk-abcdefghijklmnopqrstuvwxyz",
            "gsk_abcdefghijklmnopqrstuvwxyz",
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        ],
    )
    def test_credential_shapes_are_masked_under_any_key(self, value: str) -> None:
        """The realistic failure is `extra={"note": "...called with rzp_test_x"}`,
        where the key name gives nothing away."""
        assert REDACTED in redact({"harmless_note": value})["harmless_note"]

    def test_a_secret_embedded_in_a_sentence_is_masked(self) -> None:
        scrubbed = redact({"msg": "created order with key rzp_test_A1b2C3d4E5 ok"})["msg"]

        assert "rzp_test_A1b2C3d4E5" not in scrubbed
        assert scrubbed.startswith("created order with key ")


class TestRedactStructures:
    def test_nested_structures_are_traversed(self) -> None:
        result = redact(
            {"outer": {"list": [{"api_key": "abc"}, {"safe": "fine"}]}},
        )

        assert result["outer"]["list"][0]["api_key"] == REDACTED
        assert result["outer"]["list"][1]["safe"] == "fine"

    def test_recursion_is_bounded(self) -> None:
        """A cyclic-ish or pathologically deep payload must not hang the logger."""
        deep: dict = {"level": 0}
        cursor = deep
        for index in range(1, 30):
            cursor["child"] = {"level": index}
            cursor = cursor["child"]

        assert "TRUNCATED" in json.dumps(redact(deep))

    def test_scalars_pass_through_unchanged(self) -> None:
        assert redact({"amount_minor": 6499900})["amount_minor"] == 6499900
        assert redact({"ok": True})["ok"] is True
        assert redact({"nothing": None})["nothing"] is None


class TestJsonFormatter:
    def _record(self, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="a message",
            args=(),
            exc_info=None,
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_output_is_one_json_object(self) -> None:
        payload = json.loads(JsonFormatter(service="agentpay").format(self._record()))

        assert payload["level"] == "INFO"
        assert payload["service"] == "agentpay"
        assert "timestamp" in payload

    def test_event_name_is_promoted(self) -> None:
        payload = json.loads(
            JsonFormatter(service="agentpay").format(self._record(event="PAYMENT_CREATED"))
        )

        assert payload["event"] == "PAYMENT_CREATED"
        assert payload["message"] == "a message"

    def test_correlation_ids_are_attached_automatically(self) -> None:
        with correlation_scope(trace_id="trc_1", request_id="req_1", checkout_id="chk_1"):
            payload = json.loads(JsonFormatter(service="agentpay").format(self._record()))

        assert payload["trace_id"] == "trc_1"
        assert payload["request_id"] == "req_1"
        assert payload["checkout_id"] == "chk_1"

    def test_unset_correlation_ids_are_omitted(self) -> None:
        with correlation_scope(request_id="req_1"):
            payload = json.loads(JsonFormatter(service="agentpay").format(self._record()))

        assert "payment_id" not in payload

    def test_extras_are_redacted(self) -> None:
        payload = json.loads(
            JsonFormatter(service="agentpay").format(
                self._record(razorpay_key_secret="live-secret", amount_minor=6499900)
            )
        )

        assert payload["razorpay_key_secret"] == REDACTED
        assert payload["amount_minor"] == 6499900

    def test_exception_text_is_scrubbed(self) -> None:
        try:
            raise ValueError("connect failed for rzp_test_A1b2C3d4E5f6")
        except ValueError:
            import sys

            record = self._record()
            record.exc_info = sys.exc_info()
            payload = json.loads(JsonFormatter(service="agentpay").format(record))

        assert "rzp_test_A1b2C3d4E5f6" not in payload["exception"]
