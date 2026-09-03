"""Unit tests for the audit ledger.

agentpay:allow-credential-shapes - synthetic test credential shapes for testing redaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from packages.observability.context import correlation_scope
from services.audit.repository import (
    EventType,
    _safe_metadata,
    append_event,
    list_events,
)
from services.audit.service import AuditService

# ---------------------------------------------------------------------------
# EventType enum coverage
# ---------------------------------------------------------------------------


def test_event_type_enum_covers_all_required_types():
    """Every event type from Requirement 9.1 is declared."""
    required_types = {
        "PROMPT_SAFETY_CHECKED",
        "INTENT_EXTRACTED",
        "CATALOG_SEARCHED",
        "OFFERS_RETURNED",
        "OFFER_SELECTED",
        "OFFER_REVALIDATED",
        "CHECKOUT_CREATED",
        "POLICY_EVALUATED",
        "AUTHORIZATION_REQUESTED",
        "AUTHORIZATION_GRANTED",
        "AUTHORIZATION_REJECTED",
        "PAYMENT_CREATED",
        "PAYMENT_STATUS_CHECKED",
        "PAYMENT_VERIFIED",
        "PAYMENT_FAILED",
        "ORDER_CONFIRMED",
        "PRICE_CHANGE_DETECTED",
        "INVENTORY_CHANGE_DETECTED",
        "IDEMPOTENCY_REPLAYED",
        "RESEARCH_PERFORMED",
        "TOOL_BLOCKED",
    }
    actual_types = {e.value for e in EventType}
    assert required_types.issubset(actual_types)


# ---------------------------------------------------------------------------
# Metadata redaction
# ---------------------------------------------------------------------------


def test_safe_metadata_redacts_sensitive_keys():
    """Keys matching the sensitive pattern are replaced regardless of value."""
    metadata = {
        "normal_key": "normal_value",
        "secret": "my_secret_key",
        "token": "bearer_token",
        "password": "my_password",
        "authorization": "auth_header",
        "cookie": "session_cookie",
        "credential": "creds",
    }
    safe = _safe_metadata(metadata)
    assert safe["normal_key"] == "normal_value"
    for key in ("secret", "token", "password", "authorization", "cookie", "credential"):
        assert safe[key] == "***REDACTED***", f"{key} should be redacted"


def test_safe_metadata_redacts_secret_shaped_values():
    """Values that look like credentials are redacted even under safe keys."""
    metadata = {
        "header": "Bearer abcdefghijkl",
        "api_key": "sk-12345678abcde",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "groq_key": "gsk_abcdefghij1234",
        "plain": "just some text",
    }
    safe = _safe_metadata(metadata)
    assert safe["header"] == "***REDACTED***"
    assert safe["api_key"] == "***REDACTED***"
    assert safe["jwt"] == "***REDACTED***"
    assert safe["groq_key"] == "***REDACTED***"
    assert safe["plain"] == "just some text"


def test_safe_metadata_redacts_nested_structures():
    """Redaction recurses into dicts and lists."""
    metadata = {
        "nested": {
            "token": "nested_token",
            "normal": "nested_normal",
            "val": "Bearer abcdefghij",
        },
        "items": [
            "normal_item",
            "Bearer abcdefghij",
        ],
    }
    safe = _safe_metadata(metadata)
    assert safe["nested"]["token"] == "***REDACTED***"
    assert safe["nested"]["normal"] == "nested_normal"
    assert safe["nested"]["val"] == "***REDACTED***"
    assert safe["items"][0] == "normal_item"
    assert safe["items"][1] == "***REDACTED***"


def test_safe_metadata_redacts_razorpay_keys_and_signatures():
    """Razorpay keys and signatures are redacted, while safe structural keys are kept (BUG-40)."""
    metadata = {
        "rzp_test_key": "rzp_test_1234567890abcdef",
        "rzp_live_key": "rzp_live_abcdef1234567890",
        "provider_signature": "f2d3a4b5c6e7d8f9a0b1c2d3e4f5a6b7c8d9e0f1",
        "signature": "abcdef1234567890abcdef1234567890",
        "signature_valid": True,
        "authorization_id": "ath_123",
        "idempotency_key": "idm_456",
    }
    safe = _safe_metadata(metadata)
    assert safe["rzp_test_key"] == "***REDACTED***"
    assert safe["rzp_live_key"] == "***REDACTED***"
    assert safe["provider_signature"] == "***REDACTED***"
    assert safe["signature"] == "***REDACTED***"
    assert safe["signature_valid"] is True
    assert safe["authorization_id"] == "ath_123"
    assert safe["idempotency_key"] == "idm_456"


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


def test_append_event_writes_all_fields():
    """All audit columns are passed through to the INSERT."""
    session = MagicMock()

    with correlation_scope(request_id="req_123", trace_id="trace_123", agent_run_id="run_123"):
        event_id = append_event(
            session,
            event_type=EventType.POLICY_EVALUATED,
            aggregate_type="checkout",
            aggregate_id="chk_123",
            actor_type="system",
            actor_id="sys_1",
            merchant_id="merch_1",
            input_hash="hash_123",
            decision="ALLOW",
            reason_code="OK",
            policy_version="1.0",
            model_version="2.0",
            amount_minor=1000,
            metadata={"key": "value"},
        )

    assert event_id.startswith("aud_")
    session.execute.assert_called_once()

    call_args = session.execute.call_args
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
    assert params["event_id"] == event_id
    assert params["merchant_id"] == "merch_1"
    assert params["request_id"] == "req_123"
    assert params["trace_id"] == "trace_123"
    assert params["agent_run_id"] == "run_123"
    assert params["actor_type"] == "system"
    assert params["actor_id"] == "sys_1"
    assert params["event_type"] == "POLICY_EVALUATED"
    assert params["aggregate_type"] == "checkout"
    assert params["aggregate_id"] == "chk_123"
    assert params["input_hash"] == "hash_123"
    assert params["decision"] == "ALLOW"
    assert params["reason_code"] == "OK"
    assert params["policy_version"] == "1.0"
    assert params["model_version"] == "2.0"
    assert params["amount_minor"] == 1000


def test_append_event_never_commits():
    """The caller owns commit/rollback, not the audit writer."""
    session = MagicMock()
    with correlation_scope(request_id="req_1"):
        append_event(
            session,
            event_type=EventType.CHECKOUT_CREATED,
            aggregate_type="checkout",
            aggregate_id="chk_1",
            actor_type="buyer",
            actor_id="buy_1",
        )
    session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# list_events filtering
# ---------------------------------------------------------------------------


def test_list_events_applies_all_filters():
    """Every optional filter reaches the SQL query."""
    session = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value = [{"event_id": "aud_1", "event_type": "POLICY_EVALUATED"}]
    session.execute.return_value = mock_result

    start_at = datetime.now(UTC)
    end_at = start_at + timedelta(hours=1)

    events = list_events(
        session,
        merchant_id="merch_1",
        aggregate_type="checkout",
        aggregate_id="chk_123",
        event_type=EventType.POLICY_EVALUATED,
        start_at=start_at,
        end_at=end_at,
    )

    assert len(events) == 1
    assert events[0]["event_id"] == "aud_1"

    call_args = session.execute.call_args
    query = call_args[0][0].text
    params = call_args[0][1]

    assert "merchant_id = :merchant_id" in query
    assert "aggregate_type = :aggregate_type" in query
    assert "aggregate_id = :aggregate_id" in query
    assert "event_type = :event_type" in query
    assert "created_at >= :start_at" in query
    assert "created_at <= :end_at" in query
    assert "ORDER BY created_at ASC, event_id ASC" in query

    assert params["merchant_id"] == "merch_1"
    assert params["start_at"] == start_at
    assert params["end_at"] == end_at


def test_list_events_rejects_invalid_limit():
    session = MagicMock()
    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        list_events(session, merchant_id="m", limit=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        list_events(session, merchant_id="m", limit=201)


def test_list_events_supports_paging_and_descending_order():
    """list_events supports offset paging and descending sort order (BUG-41)."""
    session = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value = [{"event_id": "aud_10"}]
    session.execute.return_value = mock_result

    events = list_events(
        session,
        merchant_id="merch_1",
        limit=50,
        offset=100,
        order="desc",
    )
    assert len(events) == 1
    call_args = session.execute.call_args
    query = call_args[0][0].text
    params = call_args[0][1]

    assert "ORDER BY created_at DESC, event_id DESC LIMIT :limit OFFSET :offset" in query
    assert params["limit"] == 50
    assert params["offset"] == 100

    with pytest.raises(ValueError, match="offset must not be negative"):
        list_events(session, merchant_id="merch_1", offset=-1)


# ---------------------------------------------------------------------------
# AuditService helpers
# ---------------------------------------------------------------------------


def test_audit_service_record_policy_evaluated():
    """The convenience helper threads all domain fields through."""
    session = MagicMock()

    with correlation_scope(request_id="req_123", trace_id="trace_123"):
        AuditService.record_policy_evaluated(
            session,
            checkout_id="chk_123",
            decision="BLOCK",
            reason_code="AMOUNT_ABOVE_MAX_LIMIT",
            policy_version="1.0",
            inputs_hash="hash_456",
            amount_minor=5000,
        )

    call_args = session.execute.call_args
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
    assert params["event_type"] == "POLICY_EVALUATED"
    assert params["aggregate_type"] == "checkout"
    assert params["aggregate_id"] == "chk_123"
    assert params["decision"] == "BLOCK"
    assert params["reason_code"] == "AMOUNT_ABOVE_MAX_LIMIT"
    assert params["policy_version"] == "1.0"
    assert params["input_hash"] == "hash_456"
    assert params["amount_minor"] == 5000


def test_audit_service_record_payment_created():
    """Payment creation records amount and links checkout in metadata."""
    session = MagicMock()

    with correlation_scope(request_id="req_1"):
        AuditService.record_payment_created(
            session,
            payment_id="pay_123",
            checkout_id="chk_456",
            amount_minor=99900,
        )

    call_args = session.execute.call_args
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
    assert params["event_type"] == "PAYMENT_CREATED"
    assert params["aggregate_type"] == "payment"
    assert params["aggregate_id"] == "pay_123"
    assert params["amount_minor"] == 99900
