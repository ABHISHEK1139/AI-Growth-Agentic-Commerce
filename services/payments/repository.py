"""Payment repository with tenant and buyer scoping."""

from __future__ import annotations

from typing import Any, ClassVar

from packages.db.repository import TenantScopedRepository
from services.payments.models import Payment


class PaymentRepository(TenantScopedRepository[Payment]):
    model: ClassVar[Any] = Payment
    merchant_column: ClassVar[str] = "merchant_id"
    buyer_column: ClassVar[str | None] = "buyer_id"
    requires_buyer_scope: ClassVar[bool] = True

    def get_by_id(self, payment_id: str) -> Payment | None:
        return self.get(payment_id)

    def get_by_checkout_id(self, checkout_id: str) -> Payment | None:
        stmt = self.scoped_select().where(Payment.checkout_id == checkout_id)
        rows = list(self.scalars(stmt))
        return rows[0] if rows else None
