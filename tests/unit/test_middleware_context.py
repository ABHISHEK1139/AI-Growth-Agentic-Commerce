"""Request identity, header echo, and the access log line.

Two things are being proven here. The mundane one: every response carries the
identifiers a client needs to quote in a support request, and the body agrees with
the headers. The security one: an inbound `X-Request-ID` is untrusted input that
lands in every log line for the request, so a caller must not be able to shape
what an operator reads.

Rejecting a malformed identifier rather than sanitizing it is the deliberate
choice: the caller still gets a usable identifier back in the response header, and
no partially-cleaned string ever reaches the log.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.middleware.context import (
    ERROR_CODE_STATE_KEY,
    REQUEST_ID_HEADER,
    TRACE_ID_HEADER,
    outcome_for_status,
    record_error_code,
    sanitize_inbound_id,
)

PROBE_PATHS = ["/health", "/health/db"]

#: Loggers this codebase owns. The test client's own `httpx` logger echoes the
#: full request URL, which is not something the application wrote, so assertions
#: about what *we* log are scoped to our own lines.
OUR_LOGGERS = ("apps.", "packages.", "services.", "pipeline.")


def our_log_text(logs) -> str:
    return "\n".join(
        line
        for line, record in zip(logs.raw, logs.records, strict=True)
        if str(record.get("logger", "")).startswith(OUR_LOGGERS)
    )


class TestSanitizeInboundId:
    @pytest.mark.parametrize(
        "value",
        [
            "req_1a2b3c4d5e6f7a8b",
            "trace_0123456789abcdef",
            "A-B_C.D:E12345",
            "01234567",
            "x" * 64,
            "  req_1a2b3c4d  ",
        ],
    )
    def test_well_formed_identifiers_are_adopted(self, value: str) -> None:
        assert sanitize_inbound_id(value) == value.strip()

    @pytest.mark.parametrize(
        ("value", "why"),
        [
            (None, "absent"),
            ("", "empty"),
            ("   ", "whitespace only"),
            ("short7", "below the minimum length"),
            ("x" * 65, "above the maximum length"),
            ("req_1a2b 3c4d", "contains a space"),
            ("req_1a2b\n3c4d", "contains a newline"),
            ("req_1a2b\r\n3c4d", "contains a CRLF"),
            ("req_1a2b\t3c4d", "contains a tab"),
            ("req_1a2b\x003c4d", "contains a NUL"),
            ("req_1a2b\x1b[31m3c4d", "contains an ANSI escape"),
            ('{"level":"CRITICAL"}', "forged JSON log fields"),
            ("req/../../etc/passwd", "path traversal shaped"),
            ("req_\u202e1a2b3c4d", "bidi override"),
        ],
    )
    def test_malformed_identifiers_are_rejected(self, value: str | None, why: str) -> None:
        assert sanitize_inbound_id(value) is None, why

    def test_a_megabyte_of_log_noise_is_rejected(self) -> None:
        """The bound is on the pattern, not on a later truncation, so an oversized
        header never reaches a log line at all."""
        assert sanitize_inbound_id("x" * 1_000_000) is None

    def test_a_forged_line_break_cannot_smuggle_a_second_log_record(self) -> None:
        """The classic log injection: a newline plus a plausible-looking record."""
        forged = 'req_1a2b3c4d"}\n{"level":"CRITICAL","event":"PAYMENT_APPROVED"'

        assert sanitize_inbound_id(forged) is None


class TestOutcomeForStatus:
    @pytest.mark.parametrize(
        ("status", "outcome"),
        [
            (200, "success"),
            (201, "success"),
            (304, "success"),
            (400, "client_error"),
            (403, "client_error"),
            (422, "client_error"),
            (429, "rate_limited"),
            (500, "server_error"),
            (503, "server_error"),
        ],
    )
    def test_outcome_is_derived_from_the_status(self, status: int, outcome: str) -> None:
        """Rate limiting gets its own outcome because it is the one 4xx an
        operator wants to see separately from a client's own mistakes."""
        assert outcome_for_status(status) == outcome


class TestRecordErrorCode:
    def test_the_code_is_written_where_the_access_log_can_read_it(self) -> None:
        """It goes on the ASGI scope, not the request object, because the scope is
        the one thing shared by every middleware layer and the handlers."""

        class _Req:
            def __init__(self) -> None:
                self.scope: dict = {}

        request = _Req()
        record_error_code(request, "OFFER_EXPIRED")  # type: ignore[arg-type]

        assert request.scope["state"][ERROR_CODE_STATE_KEY] == "OFFER_EXPIRED"

    def test_an_existing_state_mapping_is_reused_not_replaced(self) -> None:
        class _Req:
            def __init__(self) -> None:
                self.scope: dict = {"state": {"something_else": 1}}

        request = _Req()
        record_error_code(request, "FORBIDDEN")  # type: ignore[arg-type]

        assert request.scope["state"]["something_else"] == 1
        assert request.scope["state"][ERROR_CODE_STATE_KEY] == "FORBIDDEN"


class TestResponseCarriesIdentity:
    @pytest.mark.parametrize("path", PROBE_PATHS)
    def test_both_correlation_headers_are_present(self, client: TestClient, path: str) -> None:
        response = client.get(path)

        assert response.headers[REQUEST_ID_HEADER]
        assert response.headers[TRACE_ID_HEADER]

    @pytest.mark.parametrize("path", PROBE_PATHS)
    def test_request_id_in_the_body_is_not_null(self, client: TestClient, path: str) -> None:
        """The field has existed since Task 1; this is where it starts being
        populated, and a client quoting it must get something real."""
        body = client.get(path).json()

        assert body["request_id"] is not None
        assert body["request_id"].startswith("req_")

    @pytest.mark.parametrize("path", PROBE_PATHS)
    def test_body_and_header_agree(self, client: TestClient, path: str) -> None:
        response = client.get(path)

        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]

    def test_identifiers_differ_between_requests(self, client: TestClient) -> None:
        first = client.get("/health").headers[REQUEST_ID_HEADER]
        second = client.get("/health").headers[REQUEST_ID_HEADER]

        assert first != second

    def test_a_generated_trace_id_is_distinct_from_the_request_id(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.headers[TRACE_ID_HEADER] != response.headers[REQUEST_ID_HEADER]
        assert response.headers[TRACE_ID_HEADER].startswith("trace_")

    def test_headers_are_present_on_an_error_response_too(self, client: TestClient) -> None:
        """A 404 is the response a client most needs to be able to quote."""
        response = client.get("/no-such-route")

        assert response.status_code == 404
        assert response.headers[REQUEST_ID_HEADER]
        assert response.headers[TRACE_ID_HEADER]
        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


class TestInboundIdentifiers:
    def test_a_well_formed_inbound_request_id_is_adopted(self, client: TestClient) -> None:
        """A caller that already has a correlation identifier keeps it, which is
        what makes an agent's own trace joinable to ours."""
        response = client.get("/health", headers={REQUEST_ID_HEADER: "req_caller_0001"})

        assert response.headers[REQUEST_ID_HEADER] == "req_caller_0001"
        assert response.json()["request_id"] == "req_caller_0001"

    def test_a_well_formed_inbound_trace_id_is_adopted(self, client: TestClient) -> None:
        response = client.get("/health", headers={TRACE_ID_HEADER: "trace_caller_0001"})

        assert response.headers[TRACE_ID_HEADER] == "trace_caller_0001"

    @pytest.mark.parametrize(
        "malformed",
        [
            "short",
            "x" * 400,
            "req_1a2b 3c4d",
            '{"level":"CRITICAL","event":"PAYMENT_APPROVED"}',
        ],
    )
    def test_a_malformed_inbound_request_id_is_replaced(
        self, client: TestClient, malformed: str
    ) -> None:
        response = client.get("/health", headers={REQUEST_ID_HEADER: malformed})

        assigned = response.headers[REQUEST_ID_HEADER]
        assert assigned != malformed
        assert assigned.startswith("req_")
        assert response.json()["request_id"] == assigned

    def test_a_malformed_inbound_trace_id_is_replaced(self, client: TestClient) -> None:
        response = client.get("/health", headers={TRACE_ID_HEADER: "no good at all"})

        assert response.headers[TRACE_ID_HEADER].startswith("trace_")

    def test_a_rejected_identifier_never_reaches_the_log(self, client: TestClient, logs) -> None:
        """This is the whole point of validating the header. The forged content
        must appear nowhere in the emitted line."""
        forged = '{"level":"CRITICAL","event":"PAYMENT_APPROVED","actor":"attacker"}'
        client.get("/health", headers={REQUEST_ID_HEADER: forged})

        assert "attacker" not in our_log_text(logs)
        assert "PAYMENT_APPROVED" not in our_log_text(logs)

        line = logs.with_event("REQUEST_COMPLETED")[-1]
        assert line["request_id"].startswith("req_")


class TestAccessLog:
    def test_exactly_one_line_per_request(self, client: TestClient, logs) -> None:
        client.get("/health")

        assert len(logs.with_event("REQUEST_COMPLETED")) == 1

    def test_the_line_carries_the_request_facts(self, client: TestClient, logs) -> None:
        client.get("/health")
        line = logs.with_event("REQUEST_COMPLETED")[0]

        assert line["method"] == "GET"
        assert line["path"] == "/health"
        assert line["status"] == 200
        assert line["outcome"] == "success"
        assert isinstance(line["latency_ms"], int | float)
        assert line["latency_ms"] >= 0

    def test_the_line_carries_the_correlation_identifiers(self, client: TestClient, logs) -> None:
        response = client.get("/health")
        line = logs.with_event("REQUEST_COMPLETED")[0]

        assert line["request_id"] == response.headers[REQUEST_ID_HEADER]
        assert line["trace_id"] == response.headers[TRACE_ID_HEADER]

    def test_the_query_string_is_not_logged(self, client: TestClient, logs) -> None:
        """A query string carries a buyer's search terms. The path is enough to
        identify the endpoint."""
        client.get("/health?q=engagement+ring&buyer=priya")
        line = logs.with_event("REQUEST_COMPLETED")[0]

        assert line["path"] == "/health"
        assert "engagement" not in our_log_text(logs)
        assert "priya" not in our_log_text(logs)

    def test_no_error_code_field_on_a_successful_request(self, client: TestClient, logs) -> None:
        client.get("/health")

        assert "error_code" not in logs.with_event("REQUEST_COMPLETED")[0]

    def test_the_error_code_is_reported_when_there_was_one(self, client: TestClient, logs) -> None:
        """Written by a handler on the ASGI scope, read by the outermost
        middleware. If the scope were not shared this would silently be absent."""
        client.get("/no-such-route")
        line = logs.with_event("REQUEST_COMPLETED")[0]

        assert line["status"] == 404
        assert line["outcome"] == "client_error"
        assert line["error_code"] == "NOT_FOUND"
