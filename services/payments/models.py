"""SQLAlchemy ORM models for payments, idempotency records, and provider webhook events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Payment(Base):
    __tablename__ = "payment"

    payment_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkout_id: Mapped[str] = mapped_column(
        String, ForeignKey("checkout.checkout_id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    buyer_id: Mapped[str] = mapped_column(String, ForeignKey("buyer.buyer_id"), nullable=False)
    authorization_id: Mapped[str] = mapped_column(
        String, ForeignKey("authorization.authorization_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="created")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")
    provider: Mapped[str] = mapped_column(String, nullable=False, default="fake")
    provider_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_signature: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    @property
    def aggregate_id(self) -> str:
        return self.payment_id

    @property
    def aggregate_type(self) -> str:
        return "payment"


class ProviderEvent(Base):
    __tablename__ = "provider_event"

    provider_event_id: Mapped[str] = mapped_column(String, primary_key=True)
    payment_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("payment.payment_id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    signature: Mapped[str | None] = mapped_column(String, nullable=True)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_body_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="processed")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class FailedWebhook(Base):
    """Dead-letter queue for webhooks that failed processing and need manual replay.

    A webhook is moved here when ``WebhookProcessor.process_webhook`` raises an
    exception after signature validation (i.e. the webhook is authentic but could
    not be applied due to a DB error, missing payment, etc.). The worker
    periodically retries entries whose ``next_retry_at`` has passed.
    Once manually resolved the entry can be marked ``resolved``.
    """

    __tablename__ = "failed_webhook"

    failed_webhook_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    signature: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_body_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Current state
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # Retry bookkeeping
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Resolution
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_record"

    idempotency_record_id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_type: Mapped[str] = mapped_column(String, nullable=False, default="buyer")
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="in_progress")
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC) + timedelta(hours=24),
    )

    __table_args__ = (
        UniqueConstraint(
            "actor_type",
            "actor_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_record_actor_type_id_endpoint_key",
        ),
    )
