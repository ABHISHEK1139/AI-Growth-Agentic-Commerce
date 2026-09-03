"""SQLAlchemy ORM model for human and automated authorizations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Authorization(Base):
    __tablename__ = "authorization"

    authorization_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkout_id: Mapped[str] = mapped_column(
        String, ForeignKey("checkout.checkout_id"), nullable=False, unique=True
    )
    buyer_id: Mapped[str] = mapped_column(String, ForeignKey("buyer.buyer_id"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    amount_ceiling_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")
    price_hash: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    @property
    def aggregate_id(self) -> str:
        return self.authorization_id

    @property
    def aggregate_type(self) -> str:
        return "authorization"
