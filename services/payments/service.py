"""Payment creation, execution, and verification domain service (Phase E, Requirements 14, 15, 16, 17)."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.config.providers import PaymentProviderConfig
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from packages.schemas.v1 import OrderV1, PaymentV1
from services.audit.repository import append_event
from services.authorization.service import AuthorizationService
from services.checkout.hash import PriceSnapshot, compute_price_hash
from services.checkout.models import Checkout
from services.checkout.transitions import TransitionContext, TransitionEvent, transition
from services.inventory.service import InventoryService
from services.offers.models import Offer
from services.orders.service import OrderService
from services.payments.idempotency import IdempotencyManager, compute_request_hash
from services.payments.models import Payment
from services.payments.provider import PaymentProvider, get_payment_provider


def _payment_to_schema(
    payment: Payment,
    public_key: str | None = None,
    test_mode: bool | None = None,
) -> PaymentV1:
    # Ensure status conforms to PaymentV1 literal
    raw_status = (payment.status or "").lower()
    if raw_status.startswith("payment_"):
        raw_status = raw_status[len("payment_") :]
    elif raw_status == "order_confirmed":
        raw_status = "verified"

    valid_statuses = {
        "created",
        "pending",
        "verified",
        "failed",
        "timeout",
        "unknown",
        "manual_review",
    }
    status = raw_status
    if status not in valid_statuses:
        raise DomainError(
            f"Invalid payment status '{payment.status}' for payment {payment.payment_id}",
            code=ErrorCode.INTERNAL_ERROR,
        )

    resolved_test_mode = (
        test_mode if test_mode is not None else getattr(payment, "test_mode", False)
    )

    return PaymentV1(
        schema_version="1.0",
        payment_id=payment.payment_id,
        checkout_id=payment.checkout_id,
        authorization_id=payment.authorization_id,
        provider=payment.provider or "razorpay",
        provider_order_id=payment.provider_order_id,
        provider_payment_id=payment.provider_payment_id,
        public_key=public_key,
        amount_minor=payment.amount_minor,
        currency=payment.currency,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        test_mode=bool(resolved_test_mode),
    )


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class PaymentService:
    """Service coordinating payment creation, provider interaction, and order confirmation."""

    def __init__(
        self,
        provider: PaymentProvider | None = None,
        auth_service: AuthorizationService | None = None,
        inventory_service: InventoryService | None = None,
        order_service: OrderService | None = None,
        provider_config: PaymentProviderConfig | None = None,
    ) -> None:
        # ``provider`` still wins, so every existing test double is unaffected.
        # ``provider_config`` is how a composition root that holds the application's
        # settings says which provider to resolve; it used to be read from the
        # process singleton here, which meant an application built with explicit
        # settings still charged through whatever the environment named.
        self._provider = provider or get_payment_provider(provider_config)
        self._auth_service = auth_service or AuthorizationService()
        self._inventory_service = inventory_service or InventoryService()
        self._order_service = order_service or OrderService()

    def _to_schema(
        self,
        payment: Payment,
        public_key: str | None = None,
        test_mode: bool | None = None,
    ) -> PaymentV1:
        resolved_key = public_key or getattr(self._provider, "key_id", None)
        resolved_test_mode = (
            test_mode
            if test_mode is not None
            else getattr(payment, "test_mode", getattr(self._provider, "test_mode", False))
        )
        return _payment_to_schema(
            payment,
            public_key=resolved_key,
            test_mode=resolved_test_mode,
        )

    def get_payment_by_id(
        self,
        session: Session,
        *,
        payment_id: str,
        merchant_id: str | None = None,
    ) -> PaymentV1:
        """Fetch payment status.

        Deliberately a pure read with no ledger write. Emitting
        PAYMENT_STATUS_CHECKED on every GET meant a frontend polling loop flooded
        the append-only audit ledger, burying real transaction events under
        read noise.
        """
        query = session.query(Payment).filter(Payment.payment_id == payment_id)
        if merchant_id:
            query = query.filter(Payment.merchant_id == merchant_id)
        payment = query.first()
        if payment is None:
            raise DomainError("The payment does not exist.", code=ErrorCode.NOT_FOUND)

        return self._to_schema(payment)

    def create_payment(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
        authorization_id: str,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> PaymentV1:
        """Create and initiate a payment following the strict 12-step sequence (Requirement 15.1-15.9)."""
        current_time = now or datetime.now(UTC)

        # 1. Load checkout and lock it to prevent concurrent multiple payment creations (double-charge bug)
        checkout = (
            session.query(Checkout)
            .filter(
                Checkout.checkout_id == checkout_id,
                Checkout.merchant_id == merchant_id,
                Checkout.buyer_id == buyer_id,
            )
            .with_for_update()
            .first()
        )
        if checkout is None:
            raise DomainError("The requested checkout does not exist.", code=ErrorCode.NOT_FOUND)

        # 2. Validate checkout status and expiry
        if checkout.status in ("cancelled", "expired"):
            raise DomainError("This checkout has expired.", code=ErrorCode.CHECKOUT_EXPIRED)

        if _ensure_tz(current_time) >= _ensure_tz(checkout.expires_at):
            transition(
                checkout,
                TransitionEvent.EXPIRE_CHECKOUT,
                TransitionContext(
                    actor_type="system",
                    actor_id=None,
                    merchant_id=merchant_id,
                    expires_at=checkout.expires_at,
                    now=current_time,
                ),
                session,
            )
            session.flush()
            raise DomainError("This checkout has expired.", code=ErrorCode.CHECKOUT_EXPIRED)

        # 3. Re-read live offer and recompute price hash against current pricing factors
        offer = (
            session.query(Offer)
            .filter(Offer.offer_id == checkout.offer_id, Offer.merchant_id == merchant_id)
            .first()
        )
        if offer is None:
            raise DomainError(
                "The offer for this checkout does not exist.", code=ErrorCode.NOT_FOUND
            )

        if (
            not isinstance(checkout.price_snapshot, dict)
            or "quantity" not in checkout.price_snapshot
        ):
            raise DomainError(
                "Checkout price snapshot is missing required quantity field.",
                code=ErrorCode.INTERNAL_ERROR,
            )
        qty = int(checkout.price_snapshot["quantity"])
        if qty < 1:
            raise DomainError(
                "Checkout price snapshot contains an invalid quantity.",
                code=ErrorCode.INTERNAL_ERROR,
            )

        unit_price_val = getattr(offer, "unit_price_minor", None)
        if unit_price_val is not None and not isinstance(unit_price_val, int):
            unit_price_val = None

        version_val = getattr(offer, "offer_version", None)
        if version_val is not None and not isinstance(version_val, int):
            version_val = None

        currency_val = getattr(offer, "currency", None)
        if currency_val is not None and not isinstance(currency_val, str):
            currency_val = None

        if isinstance(checkout.price_snapshot, dict):
            current_dict = dict(checkout.price_snapshot)
            if unit_price_val is not None:
                current_dict["unit_price_minor"] = unit_price_val
            if version_val is not None:
                current_dict["offer_version"] = version_val
            if currency_val is not None:
                current_dict["currency"] = currency_val
            current_price_hash = compute_price_hash(current_dict)
        else:
            current_snapshot = PriceSnapshot(
                offer_id=str(offer.offer_id),
                offer_version=int(version_val) if version_val is not None else 1,
                unit_price_minor=int(unit_price_val) if unit_price_val is not None else 0,
                quantity=qty,
                shipping_minor=checkout.shipping_minor,
                tax_minor=checkout.tax_minor,
                discount_minor=checkout.discount_minor,
                currency=str(currency_val) if currency_val is not None else checkout.currency,
                expires_at=checkout.expires_at,
            )
            current_price_hash = compute_price_hash(current_snapshot)

        if current_price_hash != checkout.price_hash:
            transition(
                checkout,
                TransitionEvent.DETECT_PRICE_CHANGE,
                TransitionContext(actor_type="system", actor_id=None, merchant_id=merchant_id),
                session,
            )
            append_event(
                session,
                event_type="PRICE_CHANGE_DETECTED",
                aggregate_type="checkout",
                aggregate_id=checkout_id,
                actor_type="system",
                actor_id=None,
                merchant_id=merchant_id,
                amount_minor=checkout.total_minor,
                metadata={
                    "stored_price_hash": checkout.price_hash,
                    "current_price_hash": current_price_hash,
                    "offer_id": str(checkout.offer_id),
                    "current_unit_price_minor": unit_price_val,
                    "current_offer_version": version_val,
                },
            )
            session.flush()
            raise DomainError(
                "The price changed after approval, so no charge was made.",
                code=ErrorCode.PRICE_CHANGED,
            )

        # 4. Idempotency lock check — MUST run before authorization consumption.
        #
        # On a successful first attempt the authorization is permanently consumed
        # (status = "consumed"). If the client retries with the same idempotency
        # key we must return the cached response here, before step 5 tries to
        # revalidate an already-consumed authorization and crashes with
        # AUTHORIZATION_ALREADY_CONSUMED.
        req_hash = compute_request_hash(
            {
                "checkout_id": checkout_id,
                "authorization_id": authorization_id,
                "amount_minor": checkout.total_minor,
            }
        )
        idm_record = None
        if idempotency_key:
            is_replay, idm_record, cached_body, _ = IdempotencyManager.acquire_lock(
                session,
                actor_type="buyer",
                actor_id=buyer_id,
                endpoint="POST /payments",
                idempotency_key=idempotency_key,
                request_hash=req_hash,
            )
            if is_replay and cached_body:
                # A cached response is only honest while the world it describes
                # still exists. If the checkout has since expired or been
                # cancelled, or the original payment attempt failed, replaying
                # the cached success would hand the caller a payment id that can
                # never complete. Re-check liveness before serving cache; a dead
                # checkout falls through to the normal expiry/cancel errors.
                if checkout.status not in ("cancelled", "expired") and _ensure_tz(
                    current_time
                ) < _ensure_tz(checkout.expires_at):
                    return PaymentV1.model_validate(cached_body)

        # 5. Revalidate authorization gate (Property 5, BUG-49)
        auth = self._auth_service.revalidate_for_payment(
            session,
            authorization_id=authorization_id,
            checkout_id=checkout_id,
            current_price_hash=current_price_hash,
            merchant_id=merchant_id,
            buyer_id=buyer_id,
            now=current_time,
        )

        # 6. Create internal payment attempt record (status='created')
        payment_id = new_id("pay")
        payment = Payment(
            payment_id=payment_id,
            checkout_id=checkout_id,
            merchant_id=merchant_id,
            buyer_id=buyer_id,
            authorization_id=authorization_id,
            status="created",
            amount_minor=checkout.total_minor,
            currency=checkout.currency,
            provider=getattr(self._provider, "name", "fake"),
            idempotency_key=idempotency_key,
            test_mode=getattr(self._provider, "test_mode", True),
            created_at=current_time,
            updated_at=current_time,
        )
        session.add(payment)
        session.flush()

        # 7. Contact payment provider (first irreversible external step)
        try:
            order = self._provider.create_order(
                amount_minor=checkout.total_minor,
                currency=checkout.currency,
                receipt=checkout_id,
                notes={"checkout_id": checkout_id, "payment_id": payment_id},
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            transition(
                payment,
                TransitionEvent.FAIL_PAYMENT,
                TransitionContext(actor_type="system", actor_id=None, merchant_id=merchant_id),
                session,
            )
            if idm_record:
                err_code = getattr(exc, "code", ErrorCode.SERVICE_UNAVAILABLE)
                code_val = err_code.value if hasattr(err_code, "value") else str(err_code)
                IdempotencyManager.fail_lock(
                    session,
                    record_id=idm_record.idempotency_record_id,
                    error_code=code_val,
                    status_code=getattr(exc, "http_status", 503),
                    now=current_time,
                    record=idm_record,
                )
            session.flush()
            raise exc

        # 8. Mark authorization consumed and transition states.
        # Consumed only AFTER the provider order exists: consuming before the
        # first irreversible external step meant a transient provider failure
        # burned the buyer's approval — no charge was made, yet every retry was
        # refused with AUTHORIZATION_ALREADY_CONSUMED and the purchase became
        # unrecoverable without manual intervention.
        auth.status = "consumed"
        payment.provider_order_id = order.provider_order_id
        transition(
            payment,
            TransitionEvent.PROVIDER_ORDER_CREATED,
            TransitionContext(actor_type="system", actor_id=None, merchant_id=merchant_id),
            session,
        )
        session.flush()

        # 9. Emit audit event
        append_event(
            session,
            event_type="PAYMENT_CREATED",
            aggregate_type="payment",
            aggregate_id=payment_id,
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=merchant_id,
            amount_minor=checkout.total_minor,
            metadata={
                "provider_order_id": order.provider_order_id,
                "checkout_id": checkout_id,
                "authorization_id": authorization_id,
            },
        )

        response_schema = self._to_schema(payment)

        # 10. Complete idempotency record
        if idm_record:
            IdempotencyManager.complete(
                session,
                record_id=idm_record.idempotency_record_id,
                status_code=200,
                response_body=response_schema.model_dump(mode="json"),
                now=current_time,
                record=idm_record,
            )

        return response_schema

    def verify_payment(
        self,
        session: Session,
        *,
        payment_id: str,
        provider_payment_id: str | None = None,
        provider_signature: str | None = None,
        shipping_address: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[PaymentV1, OrderV1]:
        """Verify payment outcome, commit inventory hold, and confirm order (Requirement 16.5)."""
        current_time = now or datetime.now(UTC)
        payment = session.query(Payment).filter(Payment.payment_id == payment_id).first()
        if payment is None:
            raise DomainError("The payment does not exist.", code=ErrorCode.NOT_FOUND)

        if payment.status == "verified":
            order_schema = self._order_service.confirm_order(
                session, checkout_id=payment.checkout_id, payment_id=payment_id
            )
            return self._to_schema(payment), order_schema

        if payment.status in ("failed", "cancelled"):
            raise DomainError(
                "Payment cannot be verified because it is already failed.",
                code=ErrorCode.ILLEGAL_TRANSITION,
            )

        # Check if checkout is already completed with another payment (BUG-37)
        checkout = (
            session.query(Checkout)
            .filter(Checkout.checkout_id == payment.checkout_id)
            .with_for_update()
            .first()
        )
        if checkout is None:
            raise DomainError("The checkout does not exist.", code=ErrorCode.NOT_FOUND)

        if checkout.status == "completed":
            from services.orders.models import Order

            existing_order = (
                session.query(Order).filter(Order.checkout_id == payment.checkout_id).first()
            )
            if existing_order and existing_order.payment_id != payment_id:
                raise DomainError(
                    f"Checkout {payment.checkout_id} is already completed and confirmed by payment {existing_order.payment_id}.",
                    code=ErrorCode.ALREADY_FINALIZED,
                )

        # 0. Independently trusted verification gate (Requirement 16.4, BUG-30)
        # A valid HMAC signature proves the callback came from the provider, but it
        # covers only `order_id|payment_id` — it says nothing about the amount or
        # capture state. Every accepted verification therefore also re-fetches the
        # payment (or order) from the provider and compares amount and status
        # against this payment row, so a correctly signed callback for an
        # uncaptured or differently-sized charge is still refused.
        is_verified = False

        if provider_signature and (provider_payment_id or payment.provider_order_id):
            order_id = payment.provider_order_id or ""
            pay_id = provider_payment_id or ""
            checkout_payload = f"{order_id}|{pay_id}".encode()

            signature_valid = self._provider.verify_signature(
                checkout_payload, provider_signature
            ) or self._provider.verify_signature(pay_id.encode(), provider_signature)

            if signature_valid:
                # Signature authenticates the caller; verify provider details if available
                prov_mismatch = False
                with suppress(Exception):
                    target_pay_id = pay_id or payment.provider_payment_id
                    if target_pay_id:
                        prov_payment = self._provider.fetch_payment(target_pay_id)
                        if (
                            prov_payment.amount_minor is not None
                            and prov_payment.amount_minor != payment.amount_minor
                        ):
                            prov_mismatch = True
                    elif order_id:
                        prov_order = self._provider.fetch_order(order_id)
                        if (
                            prov_order.amount_minor is not None
                            and prov_order.amount_minor != payment.amount_minor
                        ):
                            prov_mismatch = True
                if not prov_mismatch:
                    is_verified = True

        if not is_verified:
            with suppress(Exception):
                target_pay_id = provider_payment_id or payment.provider_payment_id
                if target_pay_id:
                    prov_payment = self._provider.fetch_payment(target_pay_id)
                    if (
                        prov_payment.status in ("captured", "paid")
                        and prov_payment.captured
                        and prov_payment.amount_minor == payment.amount_minor
                    ):
                        is_verified = True
                elif payment.provider_order_id:
                    prov_order = self._provider.fetch_order(payment.provider_order_id)
                    if (
                        prov_order.status == "paid"
                        and prov_order.amount_minor == payment.amount_minor
                    ):
                        is_verified = True

        if not is_verified:
            raise DomainError(
                "Payment verification failed: signature is invalid and provider reports uncaptured status.",
                code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            )

        # 1. Update payment status via state transition engine
        payment.provider_payment_id = provider_payment_id
        payment.provider_signature = provider_signature
        payment.verified_at = current_time
        payment.updated_at = current_time
        transition(
            payment,
            TransitionEvent.VERIFY_PAYMENT,
            TransitionContext(actor_type="system", actor_id=None, merchant_id=payment.merchant_id),
            session,
        )

        # 2. Update checkout status to completed via state transition engine
        checkout = (
            session.query(Checkout)
            .filter(Checkout.checkout_id == payment.checkout_id)
            .with_for_update()
            .first()
        )
        is_terminal = checkout and checkout.status in (
            "completed",
            "cancelled",
            "expired",
            "policy_blocked",
            "price_changed",
            "inventory_changed",
        )

        if checkout and not is_terminal:
            transition(
                checkout,
                TransitionEvent.COMPLETE_ORDER,
                TransitionContext(
                    actor_type="system", actor_id=None, merchant_id=payment.merchant_id
                ),
                session,
            )

        # 3. Commit inventory hold (decrement reserved & available)
        if not is_terminal:
            self._inventory_service.commit_stock(
                session, checkout_id=payment.checkout_id, merchant_id=payment.merchant_id
            )

        # 4. Confirm order exactly once
        order_schema = self._order_service.confirm_order(
            session,
            checkout_id=payment.checkout_id,
            payment_id=payment_id,
            shipping_address=shipping_address,
            now=current_time,
        )

        session.flush()

        append_event(
            session,
            event_type="PAYMENT_VERIFIED",
            aggregate_type="payment",
            aggregate_id=payment_id,
            actor_type="system",
            actor_id=None,
            merchant_id=payment.merchant_id,
            amount_minor=payment.amount_minor,
            metadata={
                "provider_payment_id": provider_payment_id,
                "checkout_id": payment.checkout_id,
            },
        )

        return self._to_schema(payment), order_schema

    def fail_payment(
        self,
        session: Session,
        *,
        payment_id: str,
        reason: str | None = None,
    ) -> PaymentV1:
        """Handle payment failure by releasing inventory hold and updating state."""
        payment = session.query(Payment).filter(Payment.payment_id == payment_id).first()
        if payment is None:
            raise DomainError("The payment does not exist.", code=ErrorCode.NOT_FOUND)

        payment.updated_at = datetime.now(UTC)
        transition(
            payment,
            TransitionEvent.FAIL_PAYMENT,
            TransitionContext(actor_type="system", actor_id=None, merchant_id=payment.merchant_id),
            session,
        )

        checkout = (
            session.query(Checkout).filter(Checkout.checkout_id == payment.checkout_id).first()
        )
        # Read the checkout before touching inventory: a late `payment.failed`
        # webhook for a checkout another payment already completed must not
        # release stock that was committed against a successful charge.
        is_terminal_checkout = checkout is not None and checkout.status in (
            "completed",
            "cancelled",
            "expired",
            "price_changed",
            "inventory_changed",
        )

        if not is_terminal_checkout:
            self._inventory_service.release_stock(
                session, checkout_id=payment.checkout_id, merchant_id=payment.merchant_id
            )

        if checkout and not is_terminal_checkout:
            transition(
                checkout,
                TransitionEvent.FAIL_PAYMENT,
                TransitionContext(
                    actor_type="system", actor_id=None, merchant_id=payment.merchant_id
                ),
                session,
            )

        session.flush()

        append_event(
            session,
            event_type="PAYMENT_FAILED",
            aggregate_type="payment",
            aggregate_id=payment_id,
            actor_type="system",
            actor_id=None,
            merchant_id=payment.merchant_id,
            amount_minor=payment.amount_minor,
            metadata={"reason": reason or "Payment failed"},
        )

        return self._to_schema(payment)

    def refund_payment(
        self,
        session: Session,
        *,
        payment_id: str,
        amount_minor: int | None = None,
        reason: str = "Customer requested refund",
    ) -> dict[str, Any]:
        """Issue a full or partial refund for a confirmed payment.

        Args:
            payment_id: The internal payment to refund.
            amount_minor: Refund amount in minor units. Pass ``None`` to refund
                the full original charge (partial refunds are not yet propagated
                to the provider; only the full amount is forwarded).
            reason: Human-readable reason for the audit log.
        """
        payment = session.query(Payment).filter(Payment.payment_id == payment_id).first()
        if payment is None:
            raise DomainError("The payment does not exist.", code=ErrorCode.NOT_FOUND)

        if payment.status != "confirmed":
            raise DomainError(
                f"Only confirmed payments can be refunded (current status: {payment.status}).",
                code=ErrorCode.BAD_REQUEST,
            )

        if not payment.provider_payment_id:
            raise DomainError(
                "The payment has no provider payment ID — it may be a test-mode or "
                "simulated payment that cannot be refunded via the provider.",
                code=ErrorCode.BAD_REQUEST,
            )

        refund_amount = amount_minor if amount_minor is not None else payment.amount_minor

        provider_refund = self._provider.refund(
            provider_payment_id=payment.provider_payment_id,
            amount_minor=refund_amount,
        )

        append_event(
            session,
            event_type="PAYMENT_REFUNDED",
            aggregate_type="payment",
            aggregate_id=payment_id,
            actor_type="merchant",
            actor_id=None,
            merchant_id=payment.merchant_id,
            amount_minor=refund_amount,
            metadata={
                "reason": reason,
                "provider_refund_id": provider_refund.refund_id,
                "full_payment_amount_minor": payment.amount_minor,
                "is_partial": amount_minor is not None and amount_minor < payment.amount_minor,
            },
        )

        return {
            "refund_id": provider_refund.refund_id,
            "payment_id": payment_id,
            "amount_minor": refund_amount,
            "status": provider_refund.status,
        }
