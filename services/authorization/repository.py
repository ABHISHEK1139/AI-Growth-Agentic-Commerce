"""Authorization repository with tenant and buyer scoping."""

from __future__ import annotations

from typing import Any, ClassVar

from packages.db.repository import TenantScopedRepository
from services.authorization.models import Authorization


class AuthorizationRepository(TenantScopedRepository[Authorization]):
    model: ClassVar[Any] = Authorization
    merchant_column: ClassVar[str] = "merchant_id"
    buyer_column: ClassVar[str | None] = "buyer_id"
    requires_buyer_scope: ClassVar[bool] = True

    def get_by_id(self, authorization_id: str) -> Authorization | None:
        return self.get(authorization_id)

    def get_by_checkout_id(self, checkout_id: str) -> Authorization | None:
        stmt = self.scoped_select().where(Authorization.checkout_id == checkout_id)
        rows = list(self.scalars(stmt))
        return rows[0] if rows else None
