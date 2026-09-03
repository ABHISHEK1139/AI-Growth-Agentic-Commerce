"""The error code registry is a published contract, so it is tested as one.

The table below is transcribed from design.md, "Error code registry". It is
duplicated here on purpose: if someone edits the registry without editing the
design, or the design without the registry, this file fails and names the code
that drifted. A test that derived its expectations from the registry would agree
with any mistake the registry made.

Clients switch on these codes. The frontend drives recovery UI from them and the
external buyer agent branches on them, which is why the spelling, the HTTP
status, and the retryable flag are all asserted rather than just the presence of
an entry.
"""

from __future__ import annotations

import pytest

from packages.errors.registry import (
    ERROR_REGISTRY,
    IN_BAND_CODES,
    ErrorCode,
    code_for_status,
    http_status_for,
    spec_for,
)

#: design.md, "Error code registry": (code, HTTP status, retryable).
#: ``None`` is the design's "n/a", used only where retrying is not the question.
DESIGN_TABLE: tuple[tuple[str, int, bool | None], ...] = (
    ("OFFER_NOT_FOUND", 404, False),
    ("OFFER_EXPIRED", 409, False),
    ("INVENTORY_UNAVAILABLE", 409, False),
    ("VERSION_CONFLICT", 409, True),
    ("PRICE_CHANGED", 409, False),
    ("CHECKOUT_EXPIRED", 409, False),
    ("POLICY_BLOCKED", 403, False),
    ("AMOUNT_ABOVE_MAX_LIMIT", 403, False),
    # "200 with REQUIRE_APPROVAL" — an answer that needs a decision, not a failure.
    ("AMOUNT_ABOVE_AUTO_LIMIT", 200, None),
    ("CATEGORY_NOT_ALLOWED", 403, False),
    ("MERCHANT_NOT_ALLOWED", 403, False),
    ("AUTHORIZATION_EXPIRED", 409, False),
    ("AUTHORIZATION_CHECKOUT_MISMATCH", 409, False),
    ("AUTHORIZATION_ALREADY_CONSUMED", 409, False),
    ("POLICY_VERSION_MISMATCH", 409, False),
    ("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST", 422, False),
    ("REQUEST_IN_PROGRESS", 409, True),
    ("PAYMENT_TIMEOUT", 504, True),
    # "200 with status" — an unconfirmed payment is reported, never retried blindly.
    ("PAYMENT_UNKNOWN", 200, True),
    ("WEBHOOK_SIGNATURE_INVALID", 400, False),
    # "200 with counter" — the counter-offer travels in a success envelope.
    ("MAX_DISCOUNT_EXCEEDED", 200, None),
    ("NEGOTIATION_ROUNDS_EXCEEDED", 409, False),
    ("TOOL_BLOCKED", 403, False),
    ("PROMPT_INJECTION_SUSPECTED", 400, False),
    ("ILLEGAL_TRANSITION", 409, False),
    ("ALREADY_FINALIZED", 409, False),
    ("FORBIDDEN", 403, False),
    ("RATE_LIMITED", 429, True),
)

#: Generic transport conditions the middleware has to be able to name (a
#: malformed body, an unmatched route, an unexpected exception). They are not in
#: the design's domain table, so they are pinned separately here — the point is
#: that the enum is exactly the domain table plus these, and nothing else.
TRANSPORT_TABLE: tuple[tuple[str, int, bool | None], ...] = (
    ("BAD_REQUEST", 400, False),
    ("UNAUTHENTICATED", 401, False),
    ("NOT_FOUND", 404, False),
    ("METHOD_NOT_ALLOWED", 405, False),
    ("CONFLICT", 409, False),
    ("PAYLOAD_TOO_LARGE", 413, False),
    ("VALIDATION_ERROR", 422, False),
    ("INTERNAL_ERROR", 500, False),
    ("SERVICE_UNAVAILABLE", 503, True),
    ("GATEWAY_TIMEOUT", 504, True),
)

ALL_ROWS = DESIGN_TABLE + TRANSPORT_TABLE


class TestRegistryMatchesTheDesignDocument:
    @pytest.mark.parametrize(
        ("name", "status", "retryable"), ALL_ROWS, ids=[r[0] for r in ALL_ROWS]
    )
    def test_status_and_retryable_match(
        self, name: str, status: int, retryable: bool | None
    ) -> None:
        code = ErrorCode(name)
        spec = spec_for(code)

        assert spec.http_status == status
        assert spec.retryable is retryable

    def test_the_enum_is_exactly_the_documented_set(self) -> None:
        """Neither direction may drift.

        A code in the design with no enum member is a promise the system cannot
        keep; an enum member in neither table is a code a client will meet
        without documentation.
        """
        documented = {name for name, _, _ in ALL_ROWS}
        implemented = {code.value for code in ErrorCode}

        assert implemented - documented == set(), "undocumented error codes"
        assert documented - implemented == set(), "documented codes missing from ErrorCode"

    def test_every_enum_member_has_a_registry_entry(self) -> None:
        """`spec_for` is treated as total everywhere else in the codebase."""
        assert set(ERROR_REGISTRY) == set(ErrorCode)

    def test_code_value_equals_its_name(self) -> None:
        """The wire spelling is the member name, so a rename cannot silently
        change the string a client is switching on."""
        for code in ErrorCode:
            assert code.value == code.name


class TestInBandCodes:
    def test_in_band_codes_are_exactly_the_three_documented(self) -> None:
        expected = {
            ErrorCode.AMOUNT_ABOVE_AUTO_LIMIT,
            ErrorCode.PAYMENT_UNKNOWN,
            ErrorCode.MAX_DISCOUNT_EXCEEDED,
        }

        assert set(IN_BAND_CODES) == expected

    def test_in_band_codes_are_the_only_ones_answering_200(self) -> None:
        """A 200 that is not in band would be an error envelope with a success
        status, which no client could interpret."""
        two_hundreds = {code for code, spec in ERROR_REGISTRY.items() if spec.http_status == 200}

        assert two_hundreds == set(IN_BAND_CODES)

    def test_retryable_is_only_unspecified_for_in_band_codes(self) -> None:
        unspecified = {code for code, spec in ERROR_REGISTRY.items() if spec.retryable is None}

        assert unspecified <= set(IN_BAND_CODES)

    def test_unspecified_retryable_reads_as_false_on_the_wire(self) -> None:
        """The error envelope needs a boolean. Collapsing `None` to `False` is
        safe precisely because these codes never reach an error envelope."""
        assert spec_for(ErrorCode.AMOUNT_ABOVE_AUTO_LIMIT).is_retryable is False


class TestRegistryIsImmutable:
    def test_the_mapping_cannot_be_repointed_at_runtime(self) -> None:
        """An import that could rewrite a code's status would make the contract
        depend on import order."""
        with pytest.raises(TypeError):
            ERROR_REGISTRY[ErrorCode.RATE_LIMITED] = spec_for(ErrorCode.FORBIDDEN)  # type: ignore[index]


class TestMessages:
    @pytest.mark.parametrize("code", list(ErrorCode), ids=lambda c: c.value)
    def test_message_is_a_sentence_written_for_the_caller(self, code: ErrorCode) -> None:
        message = spec_for(code).message

        assert message
        assert message[0].isupper()
        assert message.rstrip().endswith(".")

    @pytest.mark.parametrize("code", list(ErrorCode), ids=lambda c: c.value)
    def test_message_carries_no_operator_detail(self, code: ErrorCode) -> None:
        """Messages are returned to an external buyer. A host, a port, a DSN, or
        a driver class name in one of them is a leak by default."""
        message = spec_for(code).message.lower()

        for forbidden in ("postgres", "redis", "psycopg", "sqlalchemy", "http://", "traceback"):
            assert forbidden not in message


class TestStatusToCode:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (400, ErrorCode.BAD_REQUEST),
            (401, ErrorCode.UNAUTHENTICATED),
            (403, ErrorCode.FORBIDDEN),
            (404, ErrorCode.NOT_FOUND),
            (405, ErrorCode.METHOD_NOT_ALLOWED),
            (409, ErrorCode.CONFLICT),
            (413, ErrorCode.PAYLOAD_TOO_LARGE),
            (422, ErrorCode.VALIDATION_ERROR),
            (429, ErrorCode.RATE_LIMITED),
            (500, ErrorCode.INTERNAL_ERROR),
            (503, ErrorCode.SERVICE_UNAVAILABLE),
            (504, ErrorCode.GATEWAY_TIMEOUT),
        ],
    )
    def test_mapped_statuses(self, status: int, expected: ErrorCode) -> None:
        assert code_for_status(status) == expected

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (418, ErrorCode.BAD_REQUEST),
            (451, ErrorCode.BAD_REQUEST),
            (502, ErrorCode.INTERNAL_ERROR),
            (599, ErrorCode.INTERNAL_ERROR),
        ],
    )
    def test_unmapped_statuses_fall_back_by_class(self, status: int, expected: ErrorCode) -> None:
        """A framework-raised status we have not named must still get a code, and
        a 5xx must never be reported to a client as its fault."""
        assert code_for_status(status) == expected

    def test_round_trip_is_stable_for_mapped_statuses(self) -> None:
        """Every status the mapping produces must lead back to the same status,
        or an `HTTPException` would come back under a code meaning something
        else."""
        for status in (400, 401, 403, 404, 405, 409, 413, 422, 429, 500, 503, 504):
            assert http_status_for(code_for_status(status)) == status
