"""SQLAlchemy ORM model for confirmed orders."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Order(Base):
    __tablename__ = "order"

    order_id: Mapped[str] = mapped_column(String, primary_key=True)
    order_number: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    checkout_id: Mapped[str] = mapped_column(
        String, ForeignKey("checkout.checkout_id"), nullable=False, unique=True
    )
    payment_id: Mapped[str] = mapped_column(
        String, ForeignKey("payment.payment_id"), nullable=False, unique=True
    )
    buyer_id: Mapped[str] = mapped_column(String, ForeignKey("buyer.buyer_id"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")
    shipping_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
