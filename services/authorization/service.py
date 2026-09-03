"""Authorization service governing human-in-the-loop approvals and payment gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from packages.schemas.v1 import AuthorizationV1, PolicyDecisionV1
from packages.security.tenancy import TenantScope
from services.audit.repository import append_event
from services.authorization.models import Authorization
from services.authorization.repository import AuthorizationRepository
from services.catalog.models import Product
from services.checkout.models import Checkout
from services.checkout.transitions import TransitionContext, TransitionEvent, transition
from services.inventory.service import InventoryService
from services.offers.models import Offer
from services.policy.models import PolicyDecisionRecord
from services.policy.service import PolicyService


def _authorization_to_schema(
    auth: Authorization,
    category: str,
    policy_decision: PolicyDecisionRecord | None,
) -> AuthorizationV1:
    pol_schema = (
        PolicyDecisionV1(
            decision=policy_decision.decision,  # type: ignore[arg-type]
            reason_code=policy_decision.reason_code,
            policy_version=policy_decision.policy_version,
        )
        if policy_decision
        else PolicyDecisionV1(
            decision="ALLOW" if auth.status == "approved" else "REQUIRE_APPROVAL",
            reason_code="OK",
            policy_version=auth.policy_version,
        )
    )

    valid_until_str = (
        auth.valid_until.isoformat()
        if isinstance(auth.valid_until, datetime)
        else str(auth.valid_until)
    )

    return AuthorizationV1(
        schema_version="1.0",
        authorization_id=auth.authorization_id,
        buyer_id=auth.buyer_id,
        merchant_id=auth.merchant_id,
        checkout_id=auth.checkout_id,
        amount_ceiling_minor=auth.amount_ceiling_minor,
        currency=auth.currency,  # type: ignore[arg-type]
        category=category,
        price_hash=auth.price_hash,
        status=auth.status,  # type: ignore[arg-type]
        valid_until=valid_until_str,
        policy=pol_schema,
    )


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class AuthorizationService:
    """Service managing buyer approvals, expiry countdowns, and pre-payment gates."""

    def __init__(
        self,
        policy_service: PolicyService | None = None,
        inventory_service: InventoryService | None = None,
    ) -> None:
        self._policy_service = policy_service or PolicyService()
        self._inventory_service = inventory_service or InventoryService()

    def request_authorization(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
        ttl_minutes: int = 15,
        now: datetime | None = None,
    ) -> AuthorizationV1:
        """Evaluate policy and create a bound authorization for a checkout."""
        current_time = now or datetime.now(UTC)

        # 1. Load checkout
        checkout = (
            session.query(Checkout)
            .filter(
                Checkout.checkout_id == checkout_id,
                Checkout.merchant_id == merchant_id,
                Checkout.buyer_id == buyer_id,
            )
            .first()
        )
        if checkout is None:
            raise DomainError("The requested checkout does not exist.", code=ErrorCode.NOT_FOUND)

        if checkout.status in ("cancelled", "expired"):
            raise DomainError("This checkout has expired.", code=ErrorCode.CHECKOUT_EXPIRED)

        if checkout.status in ("completed", "price_changed"):
            raise DomainError(
                f"Checkout {checkout_id} is in terminal state '{checkout.status}'.",
                code=ErrorCode.ALREADY_FINALIZED,
            )

        # Check for existing authorization on this checkout (BUG-44)
        existing_auth = (
            session.query(Authorization)
            .filter(
                Authorization.checkout_id == checkout_id,
                Authorization.merchant_id == merchant_id,
                Authorization.buyer_id == buyer_id,
            )
            .first()
        )
        if (
            existing_auth is not None
            and getattr(existing_auth, "authorization_id", None) is not None
        ):
            if existing_auth.status in ("approved", "pending"):
                # An approved-but-expired authorization must not be handed back
                # as if valid; the payment gate would refuse it later anyway, so
                # surface expiry here where the caller can act on it.
                if _ensure_tz(current_time) >= _ensure_tz(existing_auth.valid_until):
                    existing_auth.status = "expired"
                    session.flush()
                    raise DomainError(
                        "The approval has expired.", code=ErrorCode.AUTHORIZATION_EXPIRED
                    )
                offer = session.query(Offer).filter(Offer.offer_id == checkout.offer_id).first()
                product = (
                    session.query(Product).filter(Product.product_id == offer.product_id).first()
                    if offer
                    else None
                )
                category = product.category_id if product else "general"
                policy_record = (
                    session.query(PolicyDecisionRecord)
                    .filter(PolicyDecisionRecord.checkout_id == checkout_id)
                    .order_by(PolicyDecisionRecord.created_at.desc())
                    .first()
                )
                return _authorization_to_schema(existing_auth, category, policy_record)
            if existing_auth.status == "consumed":
                raise DomainError(
                    f"Checkout {checkout_id} has already been authorized and consumed.",
                    code=ErrorCode.AUTHORIZATION_ALREADY_CONSUMED,
                )
            raise DomainError(
                f"Authorization for checkout {checkout_id} is in terminal state '{existing_auth.status}'.",
                code=ErrorCode.FORBIDDEN,
            )

        # 2. Evaluate policy
        policy_result = self._policy_service.evaluate_checkout_policy(
            session,
            checkout_id=checkout_id,
            merchant_id=merchant_id,
            buyer_id=buyer_id,
            now=current_time,
        )
        if policy_result.decision == "BLOCK":
            code = ErrorCode.POLICY_BLOCKED
            if policy_result.reason_code == ErrorCode.AMOUNT_ABOVE_MAX_LIMIT.value:
                code = ErrorCode.AMOUNT_ABOVE_MAX_LIMIT
            elif policy_result.reason_code == ErrorCode.CATEGORY_NOT_ALLOWED.value:
                code = ErrorCode.CATEGORY_NOT_ALLOWED
            elif policy_result.reason_code == ErrorCode.MERCHANT_NOT_ALLOWED.value:
                code = ErrorCode.MERCHANT_NOT_ALLOWED
            raise DomainError(
                "This action is not permitted by policy.",
                code=code,
                details={"reason_code": policy_result.reason_code},
            )

        # Status: ALLOW -> approved (auto), REQUIRE_APPROVAL -> pending
        status = "approved" if policy_result.decision == "ALLOW" else "pending"
        valid_until = current_time + timedelta(minutes=ttl_minutes)

        auth_id = new_id("ath")
        auth = Authorization(
            authorization_id=auth_id,
            checkout_id=checkout_id,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            amount_ceiling_minor=checkout.total_minor,
            currency=checkout.currency,
            price_hash=checkout.price_hash,
            policy_version=policy_result.policy_version,
            status=status,
            valid_until=valid_until,
            created_at=current_time,
        )
        session.add(auth)

        # Update checkout status through validated state transition engine
        if status == "approved":
            transition(
                checkout,
                TransitionEvent.USE_EXISTING_AUTHORIZATION,
                TransitionContext(actor_type="system", actor_id=None, merchant_id=merchant_id),
                session,
            )
        else:
            transition(
                checkout,
                TransitionEvent.REQUIRE_APPROVAL,
                TransitionContext(actor_type="system", actor_id=None, merchant_id=merchant_id),
                session,
            )

        session.flush()

        append_event(
            session,
            event_type="AUTHORIZATION_REQUESTED"
            if status == "pending"
            else "AUTHORIZATION_GRANTED",
            aggregate_type="authorization",
            aggregate_id=auth_id,
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=merchant_id,
            amount_minor=checkout.total_minor,
            decision=policy_result.decision,
            reason_code=policy_result.reason_code,
            policy_version=policy_result.policy_version,
            metadata={"checkout_id": checkout_id, "status": status},
        )

        offer = session.query(Offer).filter(Offer.offer_id == checkout.offer_id).first()
        product = (
            session.query(Product).filter(Product.product_id == offer.product_id).first()
            if offer
            else None
        )
        category = product.category_id if product else "general"

        policy_record = (
            session.query(PolicyDecisionRecord)
            .filter(PolicyDecisionRecord.checkout_id == checkout_id)
            .order_by(PolicyDecisionRecord.created_at.desc())
            .first()
        )

        return _authorization_to_schema(auth, category, policy_record)

    def approve_authorization(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        authorization_id: str,
        now: datetime | None = None,
    ) -> AuthorizationV1:
        """Buyer explicitly grants approval for a pending authorization."""
        current_time = now or datetime.now(UTC)
        scope = TenantScope(merchant_id=merchant_id, buyer_id=buyer_id)
        repo = AuthorizationRepository(session, scope)
        auth = repo.get_by_id(authorization_id)

        if auth is None:
            raise DomainError("The requested approval does not exist.", code=ErrorCode.NOT_FOUND)

        if auth.buyer_id != buyer_id or auth.merchant_id != merchant_id:
            raise DomainError(
                "You are not authorized to approve this authorization.",
                code=ErrorCode.FORBIDDEN,
            )

        if auth.status == "approved":
            # Idempotent re-approval
            return self.get_authorization(
                session,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                authorization_id=authorization_id,
            )

        if auth.status == "consumed":
            raise DomainError(
                "The approval has already been used.", code=ErrorCode.AUTHORIZATION_ALREADY_CONSUMED
            )

        if auth.status in ("rejected", "revoked"):
            raise DomainError("The approval is not valid.", code=ErrorCode.FORBIDDEN)

        if _ensure_tz(current_time) >= _ensure_tz(auth.valid_until):
            auth.status = "expired"
            session.flush()
            raise DomainError("The approval has expired.", code=ErrorCode.AUTHORIZATION_EXPIRED)

        auth.status = "approved"

        # Update checkout status through validated state transition engine
        checkout = session.query(Checkout).filter(Checkout.checkout_id == auth.checkout_id).first()
        if checkout:
            transition(
                checkout,
                TransitionEvent.APPROVE_AUTHORIZATION,
                TransitionContext(actor_type="buyer", actor_id=buyer_id, merchant_id=merchant_id),
                session,
            )

        session.flush()

        append_event(
            session,
            event_type="AUTHORIZATION_GRANTED",
            aggregate_type="authorization",
            aggregate_id=authorization_id,
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=merchant_id,
            amount_minor=auth.amount_ceiling_minor,
            metadata={"action": "approve", "checkout_id": auth.checkout_id},
        )

        return self.get_authorization(
            session, buyer_id=buyer_id, merchant_id=merchant_id, authorization_id=authorization_id
        )

    def reject_authorization(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        authorization_id: str,
        now: datetime | None = None,
    ) -> AuthorizationV1:
        """Buyer explicitly rejects approval, releasing inventory and cancelling checkout (BUG-43)."""
        current_time = now or datetime.now(UTC)
        scope = TenantScope(merchant_id=merchant_id, buyer_id=buyer_id)
        repo = AuthorizationRepository(session, scope)
        auth = repo.get_by_id(authorization_id)

        if auth is None:
            raise DomainError("The requested approval does not exist.", code=ErrorCode.NOT_FOUND)

        if auth.buyer_id != buyer_id or auth.merchant_id != merchant_id:
            raise DomainError(
                "You are not authorized to reject this authorization.",
                code=ErrorCode.FORBIDDEN,
            )

        if auth.status == "rejected":
            # Idempotent re-rejection
            return self.get_authorization(
                session,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                authorization_id=authorization_id,
            )

        if auth.status == "consumed":
            raise DomainError(
                "The approval has already been used.", code=ErrorCode.AUTHORIZATION_ALREADY_CONSUMED
            )

        if auth.status in ("revoked", "expired"):
            raise DomainError("The approval is not valid.", code=ErrorCode.FORBIDDEN)

        if _ensure_tz(current_time) >= _ensure_tz(auth.valid_until):
            auth.status = "expired"
            session.flush()
            raise DomainError("The approval has expired.", code=ErrorCode.AUTHORIZATION_EXPIRED)

        # Check if checkout is already final / completed / paid
        checkout = session.query(Checkout).filter(Checkout.checkout_id == auth.checkout_id).first()
        if checkout and checkout.status in ("completed", "price_changed", "cancelled", "expired"):
            raise DomainError(
                f"Checkout {checkout.checkout_id} is in terminal state '{checkout.status}' and cannot be rejected.",
                code=ErrorCode.ALREADY_FINALIZED,
            )

        auth.status = "rejected"

        # Release inventory and cancel checkout through transition engine
        if checkout:
            transition(
                checkout,
                TransitionEvent.REJECT_AUTHORIZATION,
                TransitionContext(actor_type="buyer", actor_id=buyer_id, merchant_id=merchant_id),
                session,
            )
            self._inventory_service.release_stock(
                session, checkout_id=checkout.checkout_id, merchant_id=merchant_id
            )

        session.flush()

        append_event(
            session,
            event_type="AUTHORIZATION_REJECTED",
            aggregate_type="authorization",
            aggregate_id=authorization_id,
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=merchant_id,
            metadata={"action": "reject", "checkout_id": auth.checkout_id},
        )

        return self.get_authorization(
            session, buyer_id=buyer_id, merchant_id=merchant_id, authorization_id=authorization_id
        )

    def get_authorization(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        authorization_id: str,
    ) -> AuthorizationV1:
        """Fetch authorization details within tenant scope."""
        scope = TenantScope(merchant_id=merchant_id, buyer_id=buyer_id)
        repo = AuthorizationRepository(session, scope)
        auth = repo.get_by_id(authorization_id)

        if auth is None:
            raise DomainError("The requested approval does not exist.", code=ErrorCode.NOT_FOUND)

        checkout = session.query(Checkout).filter(Checkout.checkout_id == auth.checkout_id).first()
        offer = (
            session.query(Offer).filter(Offer.offer_id == checkout.offer_id).first()
            if checkout
            else None
        )
        product = (
            session.query(Product).filter(Product.product_id == offer.product_id).first()
            if offer
            else None
        )
        category = product.category_id if product else "general"

        policy_record = (
            session.query(PolicyDecisionRecord)
            .filter(PolicyDecisionRecord.checkout_id == auth.checkout_id)
            .order_by(PolicyDecisionRecord.created_at.desc())
            .first()
        )

        return _authorization_to_schema(auth, category, policy_record)

    def revalidate_for_payment(
        self,
        session: Session,
        *,
        authorization_id: str,
        checkout_id: str,
        current_price_hash: str,
        merchant_id: str | None = None,
        buyer_id: str | None = None,
        now: datetime | None = None,
    ) -> Authorization:
        """Pre-payment revalidation gate (Requirement 13, Property 5, BUG-49)."""
        current_time = now or datetime.now(UTC)

        # 0. Enforce tenant-scoped lookup via AuthorizationRepository (BUG-49)
        if merchant_id or buyer_id:
            scope = TenantScope(merchant_id=merchant_id or "", buyer_id=buyer_id)
            repo = AuthorizationRepository(session, scope)
            auth = repo.get_by_id(authorization_id)
        else:
            auth = (
                session.query(Authorization)
                .filter(Authorization.authorization_id == authorization_id)
                .first()
            )

        if auth is None:
            raise DomainError("The approval does not exist.", code=ErrorCode.NOT_FOUND)

        # 1. Checkout mismatch check
        if auth.checkout_id != checkout_id:
            raise DomainError(
                "The approval does not belong to this checkout.",
                code=ErrorCode.AUTHORIZATION_CHECKOUT_MISMATCH,
            )

        # 2. Already consumed check
        if auth.status == "consumed":
            raise DomainError(
                "The approval has already been used.",
                code=ErrorCode.AUTHORIZATION_ALREADY_CONSUMED,
            )

        # 3. Approved status check
        if auth.status != "approved":
            raise DomainError(
                "The approval has not been granted.",
                code=ErrorCode.FORBIDDEN,
            )

        # 4. Expiry check
        if _ensure_tz(current_time) >= _ensure_tz(auth.valid_until):
            auth.status = "expired"
            session.flush()
            raise DomainError(
                "The approval has expired.",
                code=ErrorCode.AUTHORIZATION_EXPIRED,
            )

        # 5. Price hash integrity check
        if auth.price_hash != current_price_hash:
            raise DomainError(
                "The price changed after approval, so no charge was made.",
                code=ErrorCode.PRICE_CHANGED,
            )

        return auth
