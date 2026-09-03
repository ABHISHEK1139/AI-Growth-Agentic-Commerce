"""Checkout repository with tenant and buyer scoping."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy.orm import Session

from packages.db.repository import TenantScopedRepository
from services.checkout.models import Checkout


class CheckoutRepository(TenantScopedRepository[Checkout]):
    model: ClassVar[Any] = Checkout
    merchant_column: ClassVar[str] = "merchant_id"
    buyer_column: ClassVar[str | None] = "buyer_id"
    requires_buyer_scope: ClassVar[bool] = True

    def get_by_id(self, checkout_id: str) -> Checkout | None:
        return self.get(checkout_id)

    def list_by_buyer(self, limit: int = 50) -> list[Checkout]:
        stmt = self.scoped_select().order_by(Checkout.created_at.desc()).limit(limit)
        return list(self.scalars(stmt))


class CheckoutItemRepository:
    """Checkout item persistence.

    The ``checkout_item`` table has no ``merchant_id`` column — tenant isolation
    is inherited from the parent ``checkout`` row. Access to items should be
    scoped through a checkout that has already passed tenant filtering.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
