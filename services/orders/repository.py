"""Order repository with tenant and buyer scoping."""

from __future__ import annotations

from typing import Any, ClassVar

from packages.db.repository import TenantScopedRepository
from services.orders.models import Order


class OrderRepository(TenantScopedRepository[Order]):
    model: ClassVar[Any] = Order
    merchant_column: ClassVar[str] = "merchant_id"
    buyer_column: ClassVar[str | None] = "buyer_id"
    requires_buyer_scope: ClassVar[bool] = True

    def get_by_id(self, order_id: str) -> Order | None:
        return self.get(order_id)

    def get_by_checkout_id(self, checkout_id: str) -> Order | None:
        stmt = self.scoped_select().where(Order.checkout_id == checkout_id)
        rows = list(self.scalars(stmt))
        return rows[0] if rows else None
