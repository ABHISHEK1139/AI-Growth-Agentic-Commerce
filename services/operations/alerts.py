"""AlertManager — the in-process alerting layer for Phase 9.

The ``AlertManager`` is a fan-out sink. Producers call :meth:`fire` with a
typed :class:`Alert`; the manager stores the alert, logs a structured event,
and exposes :meth:`drain` so an external exporter can pull the queue.

Why the in-process queue rather than calling PagerDuty / Slack directly:

* the API process should still be useful when the alerting SaaS is down;
* the same alert should be observable from a test, not buried in a
  third-party dashboard;
* an audit reader can replay the alert stream from the database, not
  from a vendor.

The redactor in :meth:`fire` strips any field that looks like a key, a
signature, or a long base64 token. A misconfigured caller cannot leak
credentials into the alerting stream.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from packages.observability.logging import get_logger

logger = get_logger(__name__)


class AlertSeverity(StrEnum):
    """How urgent an alert is. A separate process can route by severity."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertKind(StrEnum):
    """The closed set of alert types the gateway emits.

    Adding a new alert type means adding a member here; the alerting channel
    contracts and SLO dashboards key off this enum.
    """

    WEBHOOK_DEAD_LETTER = "webhook_dead_letter"
    WEBHOOK_RETRY_EXHAUSTED = "webhook_retry_exhausted"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    WORKER_HEARTBEAT_LOST = "worker_heartbeat_lost"
    WORKER_BACKLOG_HIGH = "worker_backlog_high"
    PROVIDER_ERROR = "provider_error"
    AUTHORIZATION_ANOMALY = "authorization_anomaly"
    CATALOG_IMPORT_FAILED = "catalog_import_failed"
    PAYMENT_INCIDENT = "payment_incident"


#: Patterns that should never appear in an alert's context. Razorpay keys
#: start with ``rzp_`` or ``rzp_test_``; live keys are 24+ chars; HMAC
#: signatures are long hex.
_KEY_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^ak_[A-Za-z0-9_\-]+$"),  # AgentPay API keys
    re.compile(r"^rzp_(live|test)_[A-Za-z0-9]+$"),  # Razorpay key_id
    re.compile(r"^[a-f0-9]{64}$"),  # Raw SHA-256 digest (treat as opaque)
)
#: Length cutoff for "definitely a token, not free text".
_LONG_TOKEN_LENGTH = 80


def _is_secret_like(value: Any) -> bool:
    """Heuristic check for credential-shaped strings. Conservative: false positives drop the field."""
    if not isinstance(value, str):
        return False
    if len(value) >= _LONG_TOKEN_LENGTH:
        return True
    for pattern in _KEY_PREFIX_PATTERNS:
        if pattern.match(value):
            return True
    return False


@dataclass(slots=True)
class Alert:
    """A single alert ready for the alerting channel.

    ``context`` is intentionally typed ``dict[str, Any]`` so the producer
    is responsible for what goes in. The manager's redactor filters out
    credential-shaped values before logging, but the safe thing is for
    the caller to never put credentials in ``context`` to begin with.
    """

    kind: AlertKind
    severity: AlertSeverity
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "message": self.message,
            "context": self.context,
            "created_at": self.created_at.isoformat(),
        }


class AlertManager:
    """The process-local alert sink.

    Thread-safe. Bounded — the in-memory queue is capped at ``max_pending``
    so a run-away producer cannot exhaust memory. When the cap is hit, the
    oldest non-critical alert is dropped and a counter is incremented.
    """

    def __init__(self, *, max_pending: int = 1024) -> None:
        self._queue: deque[Alert] = deque(maxlen=max_pending)
        self._dropped_count = 0
        self._lock = threading.Lock()

    def fire(self, alert: Alert) -> None:
        """Record an alert, log it, and add it to the in-memory queue.

        Logging happens *before* the queue append so a viewer of the
        worker logs sees the alert even if the queue is later drained.
        The stored alert's context is also redacted so a downstream
        exporter cannot see what the redactor caught only at log time.
        """
        safe_context = self._redact(alert.context)
        safe_alert = Alert(
            kind=alert.kind,
            severity=alert.severity,
            message=alert.message,
            context=safe_context,
            created_at=alert.created_at,
        )
        logger.warning(
            "alert fired",
            extra={
                "event": "ALERT_FIRED",
                "alert_kind": alert.kind.value,
                "alert_severity": alert.severity.value,
                "alert_message": alert.message,
                "alert_context": safe_context,
            },
        )
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                # Drop the oldest non-critical alert if the queue is full
                # so critical alerts can still get in. If the oldest is
                # critical, drop the new one instead.
                oldest = self._queue[0]
                if oldest.severity != AlertSeverity.CRITICAL:
                    self._queue.popleft()
                else:
                    self._dropped_count += 1
                    return
            self._queue.append(safe_alert)

    def drain(self) -> list[Alert]:
        """Atomically remove and return every queued alert."""
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()
        return pending

    def peek(self) -> list[Alert]:
        """Read the queue without draining it. Useful for a debug endpoint."""
        with self._lock:
            return list(self._queue)

    def dropped_count(self) -> int:
        """How many alerts were dropped because the queue was saturated."""
        with self._lock:
            return self._dropped_count

    @staticmethod
    def _redact(context: dict[str, Any]) -> dict[str, Any]:
        """Strip credential-shaped values from an alert's context.

        Conservative: better to drop a useful field than to leak a secret.
        A redacted value is replaced with the literal string ``"[redacted]"``
        so a downstream reader knows something was there.
        """
        cleaned: dict[str, Any] = {}
        for key, value in context.items():
            if _is_secret_like(value):
                cleaned[key] = "[redacted]"
            elif isinstance(value, dict):
                cleaned[key] = AlertManager._redact(value)
            elif isinstance(value, (list, tuple)):
                cleaned[key] = [
                    AlertManager._redact(v)
                    if isinstance(v, dict)
                    else ("[redacted]" if _is_secret_like(v) else v)
                    for v in value
                ]
            else:
                cleaned[key] = value
        return cleaned


#: The default process-wide manager. Tests can construct their own to keep
#: alerts isolated; production code should call :func:`alerts()` to get the
#: shared instance.
_default_manager: AlertManager | None = None
_default_lock = threading.Lock()


def alerts() -> AlertManager:
    """The shared :class:`AlertManager` for the current process."""
    global _default_manager
    with _default_lock:
        if _default_manager is None:
            _default_manager = AlertManager()
        return _default_manager


def reset_for_tests() -> None:
    """Drop the shared manager. Only used by tests."""
    global _default_manager
    with _default_lock:
        _default_manager = None


def merge_contexts(*contexts: dict[str, Any]) -> dict[str, Any]:
    """Merge several context dicts; later dicts override earlier ones.

    Convenience for callers that want to assemble a context from a base +
    alert-specific fields without an explicit ``{**a, **b}`` chain.
    """
    merged: dict[str, Any] = {}
    for ctx in contexts:
        if not ctx:
            continue
        for key, value in ctx.items():
            merged[key] = value
    return merged


__all__: Iterable[str] = (
    "Alert",
    "AlertKind",
    "AlertManager",
    "AlertSeverity",
    "alerts",
    "merge_contexts",
    "reset_for_tests",
)
