"""Cryptographically verified and deduplicated webhook processing (Requirement 16, Property 13)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.config.providers import PaymentProviderConfig
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.logging import get_logger
from services.operations.alerts import Alert, AlertKind, AlertSeverity, alerts
from services.payments.models import FailedWebhook, Payment, ProviderEvent
from services.payments.provider import PaymentProvider, get_payment_provider
from services.payments.service import PaymentService

logger = get_logger(__name__)


def _enqueue_failed_webhook(
    session: Any,
    *,
    provider: str,
    event_type: str,
    signature: str | None,
    raw_body_hash: str,
    payload: dict[str, Any],
    error: str,
    max_attempts: int = 5,
) -> FailedWebhook:
    """Persist a failed webhook into the dead-letter queue for later retry or manual replay."""
    entry = FailedWebhook(
        failed_webhook_id=f"fwhk_{hashlib.sha256(raw_body_hash.encode()).hexdigest()[:24]}",
        provider=provider,
        event_type=event_type,
        signature=signature,
        raw_body_hash=raw_body_hash,
        payload=payload,
        status="pending",
        attempt_count=0,
        max_attempts=max_attempts,
        next_retry_at=datetime.now(UTC) + timedelta(minutes=5),
        last_error=error[:500] if error else None,
    )
    session.add(entry)
    session.flush()
    return entry


class WebhookProcessor:
    """Processes incoming provider callbacks with HMAC verification and idempotency."""

    def __init__(
        self,
        provider: PaymentProvider | None = None,
        payment_service: PaymentService | None = None,
        provider_config: PaymentProviderConfig | None = None,
    ) -> None:
        # A webhook is verified against the provider's signing secret, so which
        # provider this resolves to decides whether a callback is authentic. It
        # came from the process settings singleton, which meant an application
        # built with explicit settings verified against a different secret than
        # the one it was configured with.
        self._provider = provider or get_payment_provider(provider_config)
        self._payment_service = payment_service or PaymentService(provider=self._provider)

    def process_webhook(
        self,
        session: Session,
        *,
        raw_body: bytes,
        signature: str,
        provider_name: str = "fake",
    ) -> dict[str, Any]:
        """Verify HMAC signature on raw body, deduplicate event, and drive state transitions."""
        # 1. Cryptographic HMAC signature verification on raw bytes (Requirement 16.1, 16.2)
        if not self._provider.verify_signature(raw_body, signature):
            raise DomainError(
                "The webhook signature is not valid.",
                code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            )

        # 2. Parse JSON payload
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            raise DomainError(
                "Malformed webhook JSON payload.",
                code=ErrorCode.VALIDATION_ERROR,
            ) from None

        # Compute deterministic SHA-256 hash of raw payload bytes (BUG-46)
        raw_body_hash = hashlib.sha256(raw_body).hexdigest()

        # Deterministic event ID: use provider-supplied ID if present, otherwise hash-derived ID
        raw_event_id = payload.get("event_id") or payload.get("id")
        event_id = str(raw_event_id) if raw_event_id else f"evt_hash_{raw_body_hash[:32]}"
        event_type = str(payload.get("event") or payload.get("event_type", "payment.captured"))

        # 3. Deduplication via provider_event table (Requirement 16.3, 16.4, BUG-46)
        existing_event = (
            session.query(ProviderEvent)
            .filter(
                (ProviderEvent.provider_event_id == event_id)
                | (
                    (ProviderEvent.raw_body_hash == raw_body_hash)
                    & (ProviderEvent.signature == signature)
                )
            )
            .first()
        )
        if existing_event is not None:
            return {
                "status": "already_processed",
                "ok": True,
                "event_id": existing_event.provider_event_id,
            }

        provider_event = ProviderEvent(
            provider_event_id=event_id,
            provider=provider_name,
            event_type=event_type,
            payload=payload,
            signature=signature,
            signature_valid=True,
            raw_body_hash=raw_body_hash,
            # Recorded as received, not processed: the status flips to
            # "processed" only after the handlers below succeed. Marking it
            # processed up front meant a crash between this flush and the state
            # transitions permanently swallowed a captured-payment webhook —
            # every retry was answered `already_processed` with no side effects
            # ever applied.
            status="received",
            received_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        try:
            if hasattr(session, "begin_nested"):
                with session.begin_nested():
                    session.add(provider_event)
                    session.flush()
            else:
                session.add(provider_event)
                session.flush()
        except Exception:
            # Another concurrent webhook delivery already inserted this event
            return {
                "status": "already_processed",
                "ok": True,
                "event_id": event_id,
            }

        # 4. Handle event types
        if event_type not in (
            "payment.captured",
            "payment.authorized",
            "order.paid",
            "payment.failed",
            "order.failed",
        ):
            provider_event.status = "ignored"
            session.flush()
            return {"status": "ignored", "ok": True, "event_id": event_id}

        # Extract entity identifiers without fabricating synthetic IDs (BUG-47)
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        provider_order_id = payment_entity.get("order_id") or payload.get("order_id")
        provider_payment_id = payment_entity.get("id") or payload.get("provider_payment_id")

        # Match payment by provider_order_id or internal payment_id
        payment = None
        if provider_order_id:
            payment = (
                session.query(Payment)
                .filter(Payment.provider_order_id == provider_order_id)
                .first()
            )
        if payment is None and "payment_id" in payload:
            payment = (
                session.query(Payment).filter(Payment.payment_id == payload["payment_id"]).first()
            )

        if payment is None:
            provider_event.status = "unmatched"
            session.flush()
            _enqueue_failed_webhook(
                session,
                provider=provider_name,
                event_type=event_type,
                signature=signature,
                raw_body_hash=raw_body_hash,
                payload=payload,
                error="Payment not found for webhook — enqueued for retry or manual replay",
            )
            session.commit()
            logger.warning(
                "webhook payment not found, dead-lettered",
                extra={
                    "event": "WEBHOOK_DEAD_LETTERED",
                    "provider": provider_name,
                    "event_type": event_type,
                },
            )
            alerts().fire(
                Alert(
                    kind=AlertKind.WEBHOOK_DEAD_LETTER,
                    severity=AlertSeverity.WARNING,
                    message="Webhook dead-lettered: payment not found for incoming event",
                    context={
                        "provider": provider_name,
                        "event_type": event_type,
                        "event_id": event_id,
                        "raw_body_hash": raw_body_hash,
                    },
                )
            )
            return {"status": "dead_lettered", "ok": True, "event_id": event_id}

        provider_event.payment_id = payment.payment_id

        # If provider_payment_id was not explicitly in payload, check if payment already carries it
        if not provider_payment_id and payment.provider_payment_id:
            provider_payment_id = payment.provider_payment_id

        processing_error: str | None = None
        try:
            if event_type in ("payment.captured", "payment.authorized", "order.paid"):
                # If provider_payment_id is missing, reject fabricated confirmation (BUG-47)
                if not provider_payment_id:
                    raise DomainError(
                        "Webhook payload is missing required provider payment identifier.",
                        code=ErrorCode.VALIDATION_ERROR,
                    )

                # Requirement 16.4: Map provider event to internal event, then perform
                # an independent trusted provider status fetch before finalising order/payment.
                prov_verified = False
                prov_error: str | None = None
                if provider_payment_id:
                    try:
                        prov_payment = self._provider.fetch_payment(provider_payment_id)
                        if (
                            prov_payment
                            and prov_payment.status in ("captured", "paid", "authorized")
                            and getattr(prov_payment, "captured", True)
                            and (
                                getattr(prov_payment, "amount_minor", None) is None
                                or prov_payment.amount_minor == payment.amount_minor
                            )
                        ):
                            prov_verified = True
                        else:
                            prov_error = f"Provider payment status '{getattr(prov_payment, 'status', None)}' is uncaptured"
                    except Exception as exc:
                        prov_error = f"Provider payment fetch failed: {exc}"

                if not prov_verified and provider_order_id:
                    try:
                        prov_order = self._provider.fetch_order(provider_order_id)
                        if prov_order and prov_order.status == "paid":
                            prov_verified = True
                        else:
                            prov_error = f"Provider order status '{getattr(prov_order, 'status', None)}' is unpaid"
                    except Exception as exc:
                        prov_error = f"Provider order fetch failed: {exc}"

                if not prov_verified:
                    raise DomainError(
                        f"Independent provider verification failed: {prov_error or 'provider reports uncaptured status'}.",
                        code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
                    )

                self._payment_service.verify_payment(
                    session,
                    payment_id=payment.payment_id,
                    provider_payment_id=provider_payment_id,
                    provider_signature=signature,
                )
            elif event_type in ("payment.failed", "order.failed"):
                self._payment_service.fail_payment(
                    session,
                    payment_id=payment.payment_id,
                    reason=str(payload.get("error_description", "Payment failed")),
                )
            else:
                # Unhandled event types are recorded but not treated as processed:
                # a future handler must still see them.
                provider_event.status = "ignored"
                session.flush()
                return {"status": "ignored", "ok": True, "event_id": event_id}
        except Exception as exc:
            # Processing failed after signature was verified — enqueue to DLQ for
            # retry rather than returning an error that makes the provider retry
            # the same bad payload forever.
            processing_error = str(exc)[:500]
            try:
                _enqueue_failed_webhook(
                    session,
                    provider=provider_name,
                    event_type=event_type,
                    signature=signature,
                    raw_body_hash=raw_body_hash,
                    payload=payload,
                    error=processing_error,
                )
                session.commit()
            except Exception:
                logger.exception(
                    "failed to enqueue webhook to DLQ",
                    extra={"event": "DLQ_ENQUEUE_FAILED", "event_id": event_id},
                )
            logger.warning(
                "webhook processing failed, dead-lettered",
                extra={
                    "event": "WEBHOOK_PROCESSING_FAILED",
                    "provider": provider_name,
                    "event_type": event_type,
                    "error": processing_error,
                },
            )
            alerts().fire(
                Alert(
                    kind=AlertKind.WEBHOOK_DEAD_LETTER,
                    severity=AlertSeverity.WARNING,
                    message="Webhook dead-lettered: processing failed after signature check",
                    context={
                        "provider": provider_name,
                        "event_type": event_type,
                        "event_id": event_id,
                        "raw_body_hash": raw_body_hash,
                        "error": (processing_error or "")[:200],
                    },
                )
            )
            return {"status": "dead_lettered", "ok": True, "event_id": event_id}

        # Only now, after every side effect succeeded, is the event processed.
        provider_event.status = "processed"
        provider_event.processed_at = datetime.now(UTC)
        session.flush()

        return {"status": "processed", "ok": True, "event_id": event_id}
