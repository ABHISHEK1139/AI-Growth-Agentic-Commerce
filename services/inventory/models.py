from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Inventory(Base):
    __tablename__ = "inventory"

    offer_id: Mapped[str] = mapped_column(String, primary_key=True)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Reservation(Base):
    __tablename__ = "reservation"

    reservation_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkout_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    offer_id: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
