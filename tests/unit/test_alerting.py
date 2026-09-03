"""Tests for the operations alerting module (Phase 9)."""

from __future__ import annotations

import pytest

from services.operations.alerts import (
    Alert,
    AlertKind,
    AlertManager,
    AlertSeverity,
    alerts,
    merge_contexts,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_default_alerts() -> None:
    reset_for_tests()
    yield
    reset_for_tests()


def test_fire_and_drain_round_trip() -> None:
    manager = AlertManager()
    manager.fire(
        Alert(
            kind=AlertKind.WEBHOOK_DEAD_LETTER,
            severity=AlertSeverity.WARNING,
            message="first",
            context={"provider": "fake"},
        )
    )
    manager.fire(
        Alert(
            kind=AlertKind.PROVIDER_ERROR,
            severity=AlertSeverity.CRITICAL,
            message="second",
            context={"provider": "razorpay"},
        )
    )

    drained = manager.drain()
    assert len(drained) == 2
    assert drained[0].kind == AlertKind.WEBHOOK_DEAD_LETTER
    assert drained[1].severity == AlertSeverity.CRITICAL
    assert manager.peek() == []


def test_redact_strips_credential_shaped_values() -> None:
    manager = AlertManager()
    manager.fire(
        Alert(
            kind=AlertKind.WEBHOOK_DEAD_LETTER,
            severity=AlertSeverity.WARNING,
            message="with secrets",
            context={
                "api_key": "ak_live_abcdef1234567890",
                "razorpay_id": "rzp_live_abc123",
                "long_token": "a" * 120,
                "merchant": "merchant_demo",
            },
        )
    )

    [alert] = manager.peek()
    assert alert.context["api_key"] == "[redacted]"
    assert alert.context["razorpay_id"] == "[redacted]"
    assert alert.context["long_token"] == "[redacted]"
    # Non-secret values pass through untouched
    assert alert.context["merchant"] == "merchant_demo"


def test_redact_walks_nested_dicts_and_lists() -> None:
    manager = AlertManager()
    manager.fire(
        Alert(
            kind=AlertKind.PROVIDER_ERROR,
            severity=AlertSeverity.INFO,
            message="nested",
            context={
                "outer": {
                    "inner_key": "rzp_live_xyz",
                    "innocent": 42,
                },
                "list_field": ["safe", "rzp_live_qqq", {"deep": "ak_live_pqr"}],
            },
        )
    )

    [alert] = manager.peek()
    assert alert.context["outer"]["inner_key"] == "[redacted]"
    assert alert.context["outer"]["innocent"] == 42
    assert alert.context["list_field"][0] == "safe"
    assert alert.context["list_field"][1] == "[redacted]"
    assert alert.context["list_field"][2]["deep"] == "[redacted]"


def test_queue_overflow_drops_oldest_non_critical() -> None:
    manager = AlertManager(max_pending=3)
    # Two critical then one warning. The warning is at index 0 (oldest
    # non-critical), and a fourth alert must drop it rather than the
    # critical pair.
    for i in range(3):
        severity = AlertSeverity.WARNING if i == 0 else AlertSeverity.CRITICAL
        manager.fire(
            Alert(
                kind=AlertKind.WEBHOOK_DEAD_LETTER,
                severity=severity,
                message=f"m{i}",
                context={"i": i},
            )
        )
    manager.fire(
        Alert(
            kind=AlertKind.WEBHOOK_DEAD_LETTER,
            severity=AlertSeverity.WARNING,
            message="m3",
            context={"i": 3},
        )
    )

    pending = manager.peek()
    assert len(pending) == 3
    # The oldest warning was dropped
    assert [p.message for p in pending] == ["m1", "m2", "m3"]


def test_shared_manager_is_a_singleton() -> None:
    a = alerts()
    b = alerts()
    assert a is b


def test_merge_contexts_overrides_earlier() -> None:
    base = {"a": 1, "b": 2}
    override = {"b": 99, "c": 3}
    assert merge_contexts(base, override) == {"a": 1, "b": 99, "c": 3}


def test_to_dict_is_json_safe() -> None:
    alert = Alert(
        kind=AlertKind.WEBHOOK_RETRY_EXHAUSTED,
        severity=AlertSeverity.CRITICAL,
        message="retry budget gone",
        context={"event_id": "evt_42", "attempt_count": 5},
    )
    payload = alert.to_dict()
    assert payload["kind"] == "webhook_retry_exhausted"
    assert payload["severity"] == "critical"
    assert payload["message"] == "retry budget gone"
    assert payload["context"]["attempt_count"] == 5
    assert "created_at" in payload
