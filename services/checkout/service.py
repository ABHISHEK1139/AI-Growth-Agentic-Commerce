"""Checkout domain service managing lifecycle, price integrity, and state transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.money import (
    calculate_total_minor,
    multiply_minor_units,
)
from packages.observability.context import new_id
from packages.schemas.v1 import CheckoutV1, PriceBreakdownV1
from packages.security.tenancy import TenantScope
from services.audit.repository import append_event
from services.checkout.hash import PriceSnapshot, compute_price_hash
from services.checkout.models import Checkout, CheckoutItem
from services.checkout.repository import CheckoutRepository
from services.checkout.transitions import TransitionContext, TransitionEvent, transition
from services.inventory.service import InventoryService
from services.offers.service import OfferService


def _checkout_to_schema(checkout: Checkout, quantity: int | None = None) -> CheckoutV1:
    qty = 1
    if isinstance(quantity, int):
        qty = quantity
    elif isinstance(checkout.price_snapshot, dict) and "quantity" in checkout.price_snapshot:
        try:
            qty = int(checkout.price_snapshot["quantity"])
        except Exception:
            qty = 1

    pricing = PriceBreakdownV1(
        unit_price_minor=int(checkout.price_snapshot.get("unit_price_minor", 0)),
        quantity=qty,
        subtotal_minor=checkout.subtotal_minor,
        shipping_minor=checkout.shipping_minor,
        tax_minor=checkout.tax_minor,
        discount_minor=checkout.discount_minor,
        total_minor=checkout.total_minor,
        currency=checkout.currency,  # type: ignore[arg-type]
    )
    expires_str = (
        checkout.expires_at.isoformat()
        if isinstance(checkout.expires_at, datetime)
        else str(checkout.expires_at)
    )
    return CheckoutV1(
        schema_version="1.0",
        checkout_id=checkout.checkout_id,
        buyer_id=checkout.buyer_id,
        merchant_id=checkout.merchant_id,
        offer_id=checkout.offer_id,
        offer_version=checkout.offer_version,
        product_id=str(checkout.price_snapshot.get("product_id", "")),
        status=checkout.status,  # type: ignore[arg-type]
        pricing=pricing,
        price_hash=checkout.price_hash,
        expires_at=expires_str,
    )


class CheckoutAggregate:
    """Wrapper adapting Checkout ORM model to the Aggregate protocol for transitions."""

    def __init__(self, checkout: Checkout) -> None:
        self._checkout = checkout

    @property
    def aggregate_id(self) -> str:
        return self._checkout.checkout_id

    @property
    def aggregate_type(self) -> str:
        return "checkout"

    @property
    def status(self) -> str:
        return self._checkout.status

    @status.setter
    def status(self, value: str) -> None:
        self._checkout.status = str(value)


class CheckoutService:
    """Service coordinating checkout creation, freezing pricing snapshots, and cancellations."""

    def __init__(
        self,
        offer_service: OfferService | None = None,
        inventory_service: InventoryService | None = None,
    ) -> None:
        self._offer_service = offer_service or OfferService()
        self._inventory_service = inventory_service or InventoryService()

    def create_checkout(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        offer_id: str,
        quantity: int = 1,
        ttl_minutes: int = 15,
        now: datetime | None = None,
    ) -> CheckoutV1:
        """Create a checkout with immutable price snapshot and reserved inventory.

        Client-supplied amounts are never accepted; all totals are computed server-side.
        """
        current_time = now or datetime.now(UTC)
        checkout_id = new_id("chk")

        # 1. Fetch & validate offer
        offer = self._offer_service.get_offer_by_id(
            session, merchant_id=merchant_id, offer_id=offer_id
        )
        if offer.status != "active":
            raise DomainError(
                "The selected offer is no longer valid.", code=ErrorCode.OFFER_EXPIRED
            )

        offer_expires = datetime.fromisoformat(offer.expires_at.replace("Z", "+00:00"))
        if offer_expires.tzinfo is None:
            offer_expires = offer_expires.replace(tzinfo=UTC)
        if current_time >= offer_expires:
            raise DomainError(
                "The selected offer is no longer valid.", code=ErrorCode.OFFER_EXPIRED
            )

        # 2. Compute server-side monetary amounts
        subtotal_minor = multiply_minor_units(offer.unit_price_minor, quantity)
        shipping_minor = 0
        tax_minor = 0
        discount_minor = 0
        total_minor = calculate_total_minor(
            offer.unit_price_minor,
            quantity,
            shipping_minor=shipping_minor,
            tax_minor=tax_minor,
            discount_minor=discount_minor,
        )

        expires_at = current_time + timedelta(minutes=ttl_minutes)

        # 3. Build frozen price snapshot & compute price hash
        price_snapshot = PriceSnapshot(
            offer_id=offer_id,
            offer_version=offer.offer_version,
            unit_price_minor=offer.unit_price_minor,
            quantity=quantity,
            shipping_minor=shipping_minor,
            tax_minor=tax_minor,
            discount_minor=discount_minor,
            currency=offer.currency,
            expires_at=expires_at,
        )
        price_hash = compute_price_hash(price_snapshot)

        snapshot_dict = price_snapshot.to_canonical_dict()
        snapshot_dict["product_id"] = offer.product_id

        # 4. Persist checkout entity first so reservation foreign key is satisfied
        checkout = Checkout(
            checkout_id=checkout_id,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            offer_id=offer_id,
            offer_version=offer.offer_version,
            status="created",
            subtotal_minor=subtotal_minor,
            shipping_minor=shipping_minor,
            tax_minor=tax_minor,
            discount_minor=discount_minor,
            total_minor=total_minor,
            currency=offer.currency,
            price_hash=price_hash,
            price_snapshot=snapshot_dict,
            expires_at=expires_at,
            created_at=current_time,
        )
        session.add(checkout)

        checkout_item = CheckoutItem(
            checkout_item_id=new_id("cki"),
            checkout_id=checkout_id,
            offer_id=offer_id,
            quantity=quantity,
            unit_price_minor=offer.unit_price_minor,
            total_minor=subtotal_minor,
        )
        session.add(checkout_item)
        session.flush()

        # 5. Reserve inventory atomically
        self._inventory_service.reserve_stock(
            session, offer_id=offer_id, checkout_id=checkout_id, quantity=quantity
        )

        append_event(
            session,
            event_type="CHECKOUT_CREATED",
            aggregate_type="checkout",
            aggregate_id=checkout_id,
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=merchant_id,
            amount_minor=total_minor,
            metadata={
                "offer_id": offer_id,
                "quantity": quantity,
                "total_minor": total_minor,
                "price_hash": price_hash,
            },
        )

        return _checkout_to_schema(checkout, quantity)

    def get_checkout(
        self, session: Session, *, buyer_id: str, merchant_id: str, checkout_id: str
    ) -> CheckoutV1:
        """Fetch checkout details within buyer and merchant tenant scope."""
        scope = TenantScope(merchant_id=merchant_id, buyer_id=buyer_id)
        repo = CheckoutRepository(session, scope)
        checkout = repo.get_by_id(checkout_id)
        if checkout is None:
            raise DomainError("The requested checkout does not exist.", code=ErrorCode.NOT_FOUND)

        item = session.query(CheckoutItem).filter(CheckoutItem.checkout_id == checkout_id).first()
        quantity = item.quantity if item else 1
        return _checkout_to_schema(checkout, quantity)

    def cancel_checkout(
        self, session: Session, *, buyer_id: str, merchant_id: str, checkout_id: str
    ) -> CheckoutV1:
        """Explicitly cancel a checkout and release its inventory hold."""
        scope = TenantScope(merchant_id=merchant_id, buyer_id=buyer_id)
        repo = CheckoutRepository(session, scope)
        checkout = repo.get_by_id(checkout_id)
        if checkout is None:
            raise DomainError("The requested checkout does not exist.", code=ErrorCode.NOT_FOUND)

        if checkout.status.lower() in ("cancelled", "expired"):
            item = (
                session.query(CheckoutItem).filter(CheckoutItem.checkout_id == checkout_id).first()
            )
            return _checkout_to_schema(checkout, item.quantity if item else 1)

        # Release inventory hold
        self._inventory_service.release_stock(
            session, checkout_id=checkout_id, merchant_id=merchant_id
        )

        # Transition checkout to cancelled via state transition engine
        transition(
            CheckoutAggregate(checkout),
            TransitionEvent.CANCEL_CHECKOUT,
            TransitionContext(actor_type="buyer", actor_id=buyer_id, merchant_id=merchant_id),
            session,
        )
        session.flush()

        item = session.query(CheckoutItem).filter(CheckoutItem.checkout_id == checkout_id).first()
        return _checkout_to_schema(checkout, item.quantity if item else 1)
