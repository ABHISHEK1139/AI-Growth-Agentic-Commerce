"""Policy rules and decision repositories."""

from typing import Any, ClassVar

from sqlalchemy.orm import Session

from packages.db.repository import TenantScopedRepository
from packages.security.tenancy import TenantScope
from services.catalog.models import MerchantRules
from services.policy.models import BuyerPolicy, PolicyDecisionRecord


class PolicyDecisionRepository:
    """Policy decision persistence.

    The ``policy_decision`` table has no ``merchant_id`` column — the merchant
    relationship is indirect, through the ``checkout`` that the decision references.
    Write operations use a classmethod that takes a raw session, because tenant
    scoping is enforced at the checkout level, not the decision level.
    """

    @classmethod
    def save_decision(
        cls,
        session: Session,
        *,
        decision_id: str,
        checkout_id: str,
        decision: str,
        reason_code: str,
        policy_version: str,
        inputs_hash: str,
    ) -> PolicyDecisionRecord:
        record = PolicyDecisionRecord(
            decision_id=decision_id,
            checkout_id=checkout_id,
            decision=decision,
            reason_code=reason_code,
            policy_version=policy_version,
            inputs_hash=inputs_hash,
        )
        session.add(record)
        session.flush()
        return record


class MerchantRulesRepository(TenantScopedRepository[MerchantRules]):
    """Repository for merchant policy configuration rules (TenantScoped)."""

    model: ClassVar[Any] = MerchantRules
    merchant_column: ClassVar[str] = "merchant_id"

    def __init__(
        self, session: Session, scope_or_merchant_id: TenantScope | str | None = None
    ) -> None:
        if isinstance(scope_or_merchant_id, TenantScope):
            scope = scope_or_merchant_id
        elif isinstance(scope_or_merchant_id, str):
            scope = TenantScope(merchant_id=scope_or_merchant_id)
        else:
            scope = TenantScope(merchant_id="mer_default")
        super().__init__(session, scope)

    def get_by_merchant_id(self, merchant_id: str | None = None) -> MerchantRules | None:
        statement = self.scoped_select()
        return self._session.execute(statement).scalars().first()

    def upsert_rules(
        self,
        merchant_id: str | None = None,
        *,
        max_transaction_minor: int,
        auto_approval_limit_minor: int,
        max_discount_basis_points: int = 500,
        allowed_categories: list[str] | None = None,
        blocked_categories: list[str] | None = None,
        allowed_payment_methods: list[str] | None = None,
        allow_out_of_stock: bool = False,
    ) -> MerchantRules:
        from datetime import UTC, datetime

        target_merchant_id = merchant_id or self._scope.merchant_id
        existing = self.get_by_merchant_id(target_merchant_id)
        if existing is None:
            existing = MerchantRules(
                merchant_id=target_merchant_id,
                version="1.0",
                max_transaction_minor=max_transaction_minor,
                auto_approval_limit_minor=auto_approval_limit_minor,
                max_discount_basis_points=max_discount_basis_points,
                allowed_categories=allowed_categories or [],
                blocked_categories=blocked_categories or [],
                allowed_payment_methods=allowed_payment_methods or ["card", "upi"],
                allow_out_of_stock=allow_out_of_stock,
            )
            self._session.add(existing)
        else:
            existing.max_transaction_minor = max_transaction_minor
            existing.auto_approval_limit_minor = auto_approval_limit_minor
            existing.max_discount_basis_points = max_discount_basis_points
            if allowed_categories is not None:
                existing.allowed_categories = allowed_categories
            if blocked_categories is not None:
                existing.blocked_categories = blocked_categories
            if allowed_payment_methods is not None:
                existing.allowed_payment_methods = allowed_payment_methods
            existing.allow_out_of_stock = allow_out_of_stock
            existing.updated_at = datetime.now(UTC)
        self._session.flush()
        return existing


class BuyerPolicyRepository:
    """Repository for buyer spending limits and policy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_buyer_id(self, buyer_id: str) -> BuyerPolicy | None:
        return self._session.query(BuyerPolicy).filter(BuyerPolicy.buyer_id == buyer_id).first()
