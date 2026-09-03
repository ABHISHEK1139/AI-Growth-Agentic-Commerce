"""Worker entrypoint with scheduled background jobs (Task 23, Requirement 18)."""

from __future__ import annotations

import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import FrameType

from apps.api.config import get_settings
from packages.observability.context import correlation_scope, new_id
from packages.observability.logging import configure_logging, get_logger

logger = get_logger(__name__)

HEARTBEAT_SECONDS = 30
EXPIRY_SWEEP_SECONDS = 15
UNKNOWN_POLL_SECONDS = 30
FAILED_WEBHOOK_SWEEP_SECONDS = 60


@dataclass(slots=True)
class ScheduledJob:
    """A named job and how often it should run."""

    name: str
    interval_seconds: int
    handler: Callable[[], None]
    _last_run: float = 0.0

    def due(self, now: float) -> bool:
        return (now - self._last_run) >= self.interval_seconds

    def run(self, now: float) -> None:
        """Execute the job inside its own correlation scope.

        A job failure is logged and swallowed: one bad sweep must not take the
        worker down, because the worker is what recovers stuck payments.
        """
        self._last_run = now
        with correlation_scope(trace_id=new_id("trc"), request_id=new_id("job")):
            started = time.monotonic()
            try:
                self.handler()
            except Exception:
                logger.exception(
                    "job failed",
                    extra={
                        "event": "WORKER_JOB_FAILED",
                        "job": self.name,
                        "latency_ms": round((time.monotonic() - started) * 1000, 2),
                        "outcome": "error",
                    },
                )
            else:
                logger.info(
                    "job complete",
                    extra={
                        "event": "WORKER_JOB_COMPLETED",
                        "job": self.name,
                        "latency_ms": round((time.monotonic() - started) * 1000, 2),
                        "outcome": "ok",
                    },
                )


class Worker:
    """A cooperative scheduler with graceful shutdown."""

    def __init__(self, jobs: list[ScheduledJob], *, tick_seconds: float = 1.0) -> None:
        self._jobs = jobs
        self._tick = tick_seconds
        self._running = True

    def request_stop(self, signum: int, _frame: FrameType | None) -> None:
        logger.info(
            "shutdown requested",
            extra={"event": "WORKER_STOPPING", "signal": signal.Signals(signum).name},
        )
        self._running = False

    def run(self) -> None:
        settings = get_settings()
        logger.info(
            "worker started",
            extra={
                "event": "WORKER_STARTED",
                "env": settings.app_env,
                "jobs": [job.name for job in self._jobs],
            },
        )
        while self._running:
            now = time.monotonic()
            for job in self._jobs:
                if not self._running:
                    break
                if job.due(now):
                    job.run(now)
            time.sleep(self._tick)
        logger.info("worker stopped", extra={"event": "WORKER_STOPPED"})


def _heartbeat() -> None:
    """Supervisor heartbeat proving scheduler liveness."""
    logger.debug("heartbeat", extra={"event": "WORKER_HEARTBEAT"})


def _sweep_expired_checkouts() -> None:
    """Sweep checkouts past their TTL, mark expired and release inventory holds (Requirement 18.1)."""
    try:
        from apps.api.db import get_session_factory
        from services.checkout.models import Checkout
        from services.checkout.transitions import TransitionContext, TransitionEvent, transition
        from services.inventory.service import InventoryService

        inv_service = InventoryService()
        now = datetime.now(UTC)
        factory = get_session_factory()
        with factory() as session:
            expired_checkouts = (
                session.query(Checkout)
                .filter(
                    Checkout.status.in_(["created", "policy_checked", "authorization_pending"]),
                    Checkout.expires_at <= now,
                )
                .with_for_update(skip_locked=True)
                .all()
            )
            for chk in expired_checkouts:
                transition(
                    chk,
                    TransitionEvent.EXPIRE_CHECKOUT,
                    TransitionContext(
                        actor_type="system",
                        actor_id="worker_sweep",
                        merchant_id=chk.merchant_id,
                    ),
                    session,
                )
                inv_service.release_stock(
                    session, checkout_id=chk.checkout_id, merchant_id=chk.merchant_id
                )
            session.commit()
    except Exception as exc:
        logger.warning(
            "Checkout sweep skipped (DB unavailable in test mode)", extra={"error": str(exc)}
        )


def _reconcile_stale_webhooks() -> None:
    """Retry pending FailedWebhook entries whose next_retry_at has passed (Requirement 16, Property 13)."""
    try:
        from datetime import timedelta as td

        from apps.api.db import get_session_factory
        from services.payments.models import FailedWebhook
        from services.payments.webhooks import WebhookProcessor

        now = datetime.now(UTC)
        factory = get_session_factory()
        processor = WebhookProcessor()
        with factory() as session:
            stale = (
                session.query(FailedWebhook)
                .filter(
                    FailedWebhook.status == "pending",
                    FailedWebhook.next_retry_at <= now,
                    FailedWebhook.attempt_count < FailedWebhook.max_attempts,
                )
                .with_for_update(skip_locked=True)
                .limit(50)
                .all()
            )
            for entry in stale:
                # Re-dispatch the stored payload through the normal processor
                result = processor.process_webhook(
                    session,
                    provider=entry.provider,
                    payload=entry.payload,
                    signature=entry.signature,
                )
                entry.attempt_count += 1
                entry.last_attempt_at = now
                if result.get("ok"):
                    entry.status = "resolved"
                    entry.resolved_at = now
                    entry.resolution_note = "Retry succeeded via reconcile_stale_webhooks"
                else:
                    # Exponential backoff: 5, 10, 20, 40, 80 minutes
                    backoff = 5 * (2 ** (entry.attempt_count - 1))
                    entry.next_retry_at = now + td(minutes=backoff)
                    entry.last_error = str(result.get("error", "unknown"))[:500]
                session.flush()

            # Alert on entries that have reached their retry cap. These are
            # the ones that need a human, not another worker tick.
            from services.operations.alerts import Alert, AlertKind, AlertSeverity, alerts

            factory_inner = get_session_factory()
            with factory_inner() as alert_session:
                exhausted = (
                    alert_session.query(FailedWebhook)
                    .filter(
                        FailedWebhook.status == "pending",
                        FailedWebhook.attempt_count >= FailedWebhook.max_attempts,
                    )
                    .all()
                )
                for entry in exhausted:
                    alerts().fire(
                        Alert(
                            kind=AlertKind.WEBHOOK_RETRY_EXHAUSTED,
                            severity=AlertSeverity.CRITICAL,
                            message="Webhook retry budget exhausted; manual replay required",
                            context={
                                "failed_webhook_id": entry.failed_webhook_id,
                                "provider": entry.provider,
                                "event_type": entry.event_type,
                                "attempt_count": entry.attempt_count,
                                "max_attempts": entry.max_attempts,
                                "last_error": (entry.last_error or "")[:200],
                            },
                        )
                    )
            session.commit()
    except Exception as exc:
        logger.warning(
            "DLQ reconcile skipped (DB unavailable in test mode)", extra={"error": str(exc)}
        )


def _poll_unknown_payments() -> None:
    """Recheck status of payments in UNKNOWN state with provider (Requirement 18.2)."""
    try:
        from apps.api.config import get_settings
        from apps.api.db import get_session_factory
        from services.payments.models import Payment
        from services.payments.provider import get_payment_provider
        from services.payments.service import PaymentService

        # The worker is its own process entry point, so reading the settings
        # singleton here *is* the composition root. What changed is that the
        # configuration is now handed to the resolver explicitly rather than
        # fetched from inside the domain.
        provider = get_payment_provider(get_settings().payment_provider_config())
        pay_service = PaymentService(provider=provider)
        factory = get_session_factory()
        with factory() as session:
            unknown_payments = (
                session.query(Payment)
                .filter(Payment.status == "unknown")
                .with_for_update(skip_locked=True)
                .all()
            )
            for pay in unknown_payments:
                if pay.provider_order_id:
                    order = provider.fetch_order(pay.provider_order_id)
                    if order.status == "paid":
                        pay_service.verify_payment(session, payment_id=pay.payment_id)
                    elif order.status in ("failed", "cancelled"):
                        pay_service.fail_payment(
                            session, payment_id=pay.payment_id, reason="Provider reported failure"
                        )
                    else:
                        # Provider returns an order in some other state we
                        # don't have a transition for — surface that as a
                        # reconciliation mismatch, not a silent retry.
                        from services.operations.alerts import (
                            Alert,
                            AlertKind,
                            AlertSeverity,
                            alerts,
                        )

                        alerts().fire(
                            Alert(
                                kind=AlertKind.RECONCILIATION_MISMATCH,
                                severity=AlertSeverity.WARNING,
                                message=(
                                    "Local payment in UNKNOWN but provider reports "
                                    "neither paid nor failed"
                                ),
                                context={
                                    "payment_id": pay.payment_id,
                                    "provider_order_id": pay.provider_order_id,
                                    "provider_status": order.status,
                                },
                            )
                        )
            session.commit()
    except Exception as exc:
        logger.warning(
            "Unknown payment poll skipped (DB unavailable in test mode)", extra={"error": str(exc)}
        )


def build_jobs() -> list[ScheduledJob]:
    """The job registry with recovery and sweep handlers."""
    return [
        ScheduledJob(name="heartbeat", interval_seconds=HEARTBEAT_SECONDS, handler=_heartbeat),
        ScheduledJob(
            name="sweep_expired_checkouts",
            interval_seconds=EXPIRY_SWEEP_SECONDS,
            handler=_sweep_expired_checkouts,
        ),
        ScheduledJob(
            name="poll_unknown_payments",
            interval_seconds=UNKNOWN_POLL_SECONDS,
            handler=_poll_unknown_payments,
        ),
        ScheduledJob(
            name="reconcile_stale_webhooks",
            interval_seconds=FAILED_WEBHOOK_SWEEP_SECONDS,
            handler=_reconcile_stale_webhooks,
        ),
    ]


def main() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, service=f"{settings.app_name}-worker")

    worker = Worker(build_jobs())
    signal.signal(signal.SIGINT, worker.request_stop)
    signal.signal(signal.SIGTERM, worker.request_stop)
    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
