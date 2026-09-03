"""SQLAlchemy ORM model for Offer."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Offer(Base):
    __tablename__ = "offer"

    offer_id: Mapped[str] = mapped_column(String, primary_key=True)
    catalog_version_id: Mapped[str] = mapped_column(
        String, ForeignKey("catalog_version.catalog_version_id"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String, ForeignKey("product.product_id"), nullable=False
    )
    variant_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("variant.variant_id"), nullable=True
    )
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="INR")
    delivery_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    return_period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    pricing_source: Mapped[str] = mapped_column(
        String, nullable=False, default="synthetic_band_random"
    )
    offer_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
