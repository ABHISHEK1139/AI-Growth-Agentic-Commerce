"""SQLAlchemy ORM models for policy rules, buyer policies, and policy decisions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class BuyerPolicy(Base):
    __tablename__ = "buyer_policy"

    buyer_id: Mapped[str] = mapped_column(String, ForeignKey("buyer.buyer_id"), primary_key=True)
    version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    max_transaction_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    auto_approval_limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allowed_merchants: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class PolicyDecisionRecord(Base):
    __tablename__ = "policy_decision"

    decision_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkout_id: Mapped[str] = mapped_column(
        String, ForeignKey("checkout.checkout_id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String, nullable=False)  # ALLOW, REQUIRE_APPROVAL, BLOCK
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
