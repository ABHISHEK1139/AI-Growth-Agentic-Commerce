"""Audit service for high-level event recording."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from services.audit.repository import EventType, append_event


class AuditService:
    """Service to record audit ledger events."""

    @staticmethod
    def record_event(
        session: Session,
        *,
        event_type: str | EventType,
        aggregate_type: str,
        aggregate_id: str,
        actor_type: str,
        actor_id: str | None,
        merchant_id: str | None = None,
        input_hash: str | None = None,
        decision: str | None = None,
        reason_code: str | None = None,
        policy_version: str | None = None,
        model_version: str | None = None,
        amount_minor: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """General entry point for recording an audit event."""
        return append_event(
            session,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            actor_type=actor_type,
            actor_id=actor_id,
            merchant_id=merchant_id,
            input_hash=input_hash,
            decision=decision,
            reason_code=reason_code,
            policy_version=policy_version,
            model_version=model_version,
            amount_minor=amount_minor,
            metadata=metadata,
        )

    @staticmethod
    def record_policy_evaluated(
        session: Session,
        *,
        checkout_id: str,
        decision: str,
        reason_code: str,
        policy_version: str,
        inputs_hash: str,
        amount_minor: int | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        merchant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Helper for policy evaluation events."""
        return append_event(
            session,
            event_type=EventType.POLICY_EVALUATED,
            aggregate_type="checkout",
            aggregate_id=checkout_id,
            actor_type=actor_type,
            actor_id=actor_id,
            merchant_id=merchant_id,
            input_hash=inputs_hash,
            decision=decision,
            reason_code=reason_code,
            policy_version=policy_version,
            amount_minor=amount_minor,
            metadata=metadata,
        )

    @staticmethod
    def record_payment_created(
        session: Session,
        *,
        payment_id: str,
        checkout_id: str,
        amount_minor: int,
        actor_type: str = "system",
        actor_id: str | None = None,
        merchant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Helper for payment creation events."""
        meta = metadata or {}
        meta["checkout_id"] = checkout_id
        return append_event(
            session,
            event_type=EventType.PAYMENT_CREATED,
            aggregate_type="payment",
            aggregate_id=payment_id,
            actor_type=actor_type,
            actor_id=actor_id,
            merchant_id=merchant_id,
            amount_minor=amount_minor,
            metadata=meta,
        )
