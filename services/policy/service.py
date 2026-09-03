"""Policy evaluation service coordinating rules loading, evaluation, and persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from packages.schemas.v1 import PolicyDecisionV1
from services.audit import AuditService
from services.catalog.models import Product
from services.checkout.models import Checkout
from services.inventory.models import Inventory
from services.offers.models import Offer
from services.policy.engine import (
    BuyerPolicyRules,
    MerchantPolicyRules,
    PolicyDecisionResult,
    PolicyInputs,
    evaluate_policy,
)
from services.policy.repository import (
    BuyerPolicyRepository,
    MerchantRulesRepository,
    PolicyDecisionRepository,
)


class PolicyService:
    """Service evaluating policy rules for checkouts."""

    def evaluate_checkout_policy(
        self,
        session: Session,
        *,
        checkout_id: str,
        merchant_id: str | None = None,
        buyer_id: str | None = None,
        now: datetime | None = None,
    ) -> PolicyDecisionV1:
        """Load checkout, rules, evaluate policy, persist decision, and record audit event."""
        current_time = now or datetime.now(UTC)

        # 1. Load checkout
        query = session.query(Checkout).filter(Checkout.checkout_id == checkout_id)
        if merchant_id is not None:
            query = query.filter(Checkout.merchant_id == merchant_id)
        if buyer_id is not None:
            query = query.filter(Checkout.buyer_id == buyer_id)
        checkout = query.first()
        if checkout is None:
            raise DomainError("The requested checkout does not exist.", code=ErrorCode.NOT_FOUND)

        # 2. Load offer & product
        offer = session.query(Offer).filter(Offer.offer_id == checkout.offer_id).first()
        product = (
            session.query(Product).filter(Product.product_id == offer.product_id).first()
            if offer
            else None
        )
        inventory = (
            session.query(Inventory).filter(Inventory.offer_id == checkout.offer_id).first()
            if offer
            else None
        )

        category_id = product.category_id if product else "unknown"
        offer_status = offer.status if offer else "inactive"
        if offer:
            if isinstance(offer.expires_at, str):
                offer_expires_at = datetime.fromisoformat(offer.expires_at.replace("Z", "+00:00"))
            else:
                offer_expires_at = offer.expires_at
            if offer_expires_at.tzinfo is None:
                offer_expires_at = offer_expires_at.replace(tzinfo=UTC)
        else:
            offer_expires_at = current_time
        from services.inventory.models import Reservation

        reservation = (
            session.query(Reservation)
            .filter(
                Reservation.checkout_id == checkout_id,
                Reservation.offer_id == checkout.offer_id,
                Reservation.status == "held",
            )
            .first()
        )
        held_by_this_checkout = reservation.quantity if reservation else 0
        available_qty = (
            (inventory.available_quantity - inventory.reserved_quantity) + held_by_this_checkout
            if inventory
            else 0
        )

        # 3. Load merchant rules & buyer policy (with safe defaults if not configured)
        merchant_repo = MerchantRulesRepository(session, checkout.merchant_id)
        buyer_repo = BuyerPolicyRepository(session)

        db_merchant_rules = merchant_repo.get_by_merchant_id(checkout.merchant_id)
        db_buyer_policy = buyer_repo.get_by_buyer_id(checkout.buyer_id)

        merchant_rules = (
            MerchantPolicyRules(
                merchant_id=checkout.merchant_id,
                version=db_merchant_rules.version,
                max_transaction_minor=db_merchant_rules.max_transaction_minor,
                auto_approval_limit_minor=db_merchant_rules.auto_approval_limit_minor,
                max_discount_basis_points=db_merchant_rules.max_discount_basis_points,
                allowed_categories=tuple(db_merchant_rules.allowed_categories or ()),
                blocked_categories=tuple(db_merchant_rules.blocked_categories or ()),
                allowed_payment_methods=tuple(db_merchant_rules.allowed_payment_methods or ()),
                allow_out_of_stock=db_merchant_rules.allow_out_of_stock,
            )
            if db_merchant_rules
            else MerchantPolicyRules(
                merchant_id=checkout.merchant_id,
                version="1.0",
                max_transaction_minor=10000000,  # 1 lakh default
                auto_approval_limit_minor=500000,  # 5,000 INR default auto approval
            )
        )

        buyer_policy = (
            BuyerPolicyRules(
                buyer_id=checkout.buyer_id,
                version=db_buyer_policy.version,
                max_transaction_minor=db_buyer_policy.max_transaction_minor,
                auto_approval_limit_minor=db_buyer_policy.auto_approval_limit_minor,
                allowed_merchants=tuple(db_buyer_policy.allowed_merchants or ()),
                allowed_categories=tuple(db_buyer_policy.allowed_categories or ()),
            )
            if db_buyer_policy
            else BuyerPolicyRules(
                buyer_id=checkout.buyer_id,
                version="1.0",
                max_transaction_minor=10000000,
                auto_approval_limit_minor=500000,
            )
        )

        # 4. Build inputs
        inputs = PolicyInputs(
            buyer_id=checkout.buyer_id,
            merchant_id=checkout.merchant_id,
            category_id=category_id,
            amount_minor=checkout.total_minor,
            currency=checkout.currency,
            offer_status=offer_status,
            offer_expires_at=offer_expires_at,
            available_quantity=available_qty,
            policy_version=buyer_policy.version,
        )

        # 5. Pure evaluation
        result: PolicyDecisionResult = evaluate_policy(
            inputs, merchant_rules, buyer_policy, now=current_time
        )

        # 6. Persist decision
        decision_id = new_id("pld")
        PolicyDecisionRepository.save_decision(
            session,
            decision_id=decision_id,
            checkout_id=checkout_id,
            decision=result.decision,
            reason_code=result.reason_code,
            policy_version=result.policy_version,
            inputs_hash=result.inputs_hash,
        )

        # 7. Emit audit event
        AuditService.record_policy_evaluated(
            session,
            checkout_id=checkout_id,
            decision=result.decision,
            reason_code=result.reason_code,
            policy_version=result.policy_version,
            inputs_hash=result.inputs_hash,
            amount_minor=checkout.total_minor,
            merchant_id=checkout.merchant_id,
        )

        return PolicyDecisionV1(
            decision=result.decision,  # type: ignore[arg-type]
            reason_code=result.reason_code,
            policy_version=result.policy_version,
        )
