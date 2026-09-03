"""Order confirmation and tracking service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from packages.schemas.v1 import OrderV1
from packages.security.tenancy import TenantScope
from services.audit.repository import append_event
from services.checkout.models import Checkout
from services.orders.models import Order
from services.orders.repository import OrderRepository

#: Ceiling on how many orders one read may return. A buyer surface that accepts an
#: unbounded page size is a surface that can be asked to serialize the whole table.
MAX_ORDER_PAGE_SIZE = 50

#: What a caller gets when it does not ask for a page size.
DEFAULT_ORDER_PAGE_SIZE = 20


def _order_to_schema(order: Order) -> OrderV1:
    confirmed_str = (
        order.confirmed_at.isoformat()
        if isinstance(order.confirmed_at, datetime)
        else str(order.confirmed_at)
    )
    if order.status not in ("confirmed", "completed", "cancelled"):
        raise DomainError(
            f"Invalid order status '{order.status}' for order {order.order_id}",
            code=ErrorCode.INTERNAL_ERROR,
        )
    return OrderV1(
        schema_version="1.0",
        order_id=order.order_id,
        checkout_id=order.checkout_id,
        payment_id=order.payment_id,
        buyer_id=order.buyer_id,
        merchant_id=order.merchant_id,
        amount_minor=order.total_minor,
        currency=order.currency,  # type: ignore[arg-type]
        status=order.status,  # type: ignore[arg-type]
        confirmed_at=confirmed_str,
    )


class OrderService:
    """Service managing order creation and deduplicated confirmation."""

    def confirm_order(
        self,
        session: Session,
        *,
        checkout_id: str,
        payment_id: str,
        shipping_address: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> OrderV1:
        """Confirm an order exactly once for a verified payment (Requirement 8.8, 16.5)."""
        current_time = now or datetime.now(UTC)

        # Idempotency check: Return existing order if already confirmed by this exact payment
        existing = session.query(Order).filter(Order.checkout_id == checkout_id).first()
        if existing is not None:
            if existing.payment_id == payment_id:
                return _order_to_schema(existing)
            # Another distinct payment attempted to confirm an already confirmed checkout
            raise DomainError(
                f"Checkout {checkout_id} is already confirmed by payment {existing.payment_id}.",
                code=ErrorCode.ALREADY_FINALIZED,
            )

        existing_payment_order = session.query(Order).filter(Order.payment_id == payment_id).first()
        if existing_payment_order is not None:
            return _order_to_schema(existing_payment_order)

        checkout = session.query(Checkout).filter(Checkout.checkout_id == checkout_id).first()
        if checkout is None:
            raise DomainError("The checkout does not exist.", code=ErrorCode.NOT_FOUND)

        from services.payments.models import Payment

        payment = session.query(Payment).filter(Payment.payment_id == payment_id).first()
        if payment is None:
            raise DomainError("The payment does not exist.", code=ErrorCode.NOT_FOUND)

        order_id = new_id("ord")
        order_number = f"ORD-{new_id('num')[:8].upper()}"

        order = Order(
            order_id=order_id,
            order_number=order_number,
            checkout_id=checkout_id,
            payment_id=payment_id,
            buyer_id=checkout.buyer_id,
            merchant_id=checkout.merchant_id,
            status="confirmed",
            total_minor=checkout.total_minor,
            amount_minor=checkout.total_minor,
            currency=checkout.currency,
            shipping_address=shipping_address,
            confirmed_at=current_time,
            created_at=current_time,
        )
        try:
            nested = session.begin_nested()  # SAVEPOINT — keeps session usable on error
            session.add(order)
            session.flush()
        except Exception as exc:
            nested.rollback()  # roll back to SAVEPOINT; outer transaction stays clean
            existing = session.query(Order).filter(Order.checkout_id == checkout_id).first()
            if existing is not None and existing.payment_id == payment_id:
                return _order_to_schema(existing)
            raise DomainError(
                f"Checkout {checkout_id} could not be confirmed.",
                code=ErrorCode.ALREADY_FINALIZED,
            ) from exc

        append_event(
            session,
            event_type="ORDER_CONFIRMED",
            aggregate_type="order",
            aggregate_id=order_id,
            actor_type="system",
            actor_id=None,
            merchant_id=checkout.merchant_id,
            amount_minor=checkout.total_minor,
            metadata={
                "order_number": order_number,
                "checkout_id": checkout_id,
                "payment_id": payment_id,
            },
        )

        return _order_to_schema(order)

    # --- Buyer-scoped reads ------------------------------------------------
    #
    # Both methods below go through :class:`OrderRepository`, which is declared
    # ``requires_buyer_scope``: the statement carries the merchant *and* the buyer
    # predicate before it is executable, and the repository base refuses any
    # statement it did not build. That is what makes "a buyer sees only their own
    # orders" a property of the query rather than of the handler remembering to
    # filter.

    def list_orders_for_buyer(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        limit: int = DEFAULT_ORDER_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[OrderV1], int]:
        """The buyer's own orders, newest first, plus how many they have in total.

        The count is taken through the same scope, so it is the number of orders
        *this* buyer has and never a total that leaks another buyer's volume.
        """
        if not 1 <= limit <= MAX_ORDER_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_ORDER_PAGE_SIZE}")
        if offset < 0:
            raise ValueError("offset must not be negative")

        repo = OrderRepository(session, TenantScope(merchant_id=merchant_id, buyer_id=buyer_id))
        statement = (
            repo.scoped_select()
            # `order_id` breaks the tie so two orders confirmed in the same
            # transaction still come back in a stable order across pages.
            .order_by(Order.confirmed_at.desc(), Order.order_id.desc())
            .limit(limit)
            .offset(offset)
        )
        orders = [_order_to_schema(order) for order in repo.scalars(statement)]
        return orders, repo.count()

    def get_order_for_buyer(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        order_id: str,
    ) -> OrderV1:
        """One order the buyer owns.

        An order belonging to another buyer or another tenant is reported as
        ``NOT_FOUND``, the same answer as an identifier that never existed. The
        distinction is withheld on purpose: telling a caller that an order exists
        but is not theirs confirms the identifier.
        """
        repo = OrderRepository(session, TenantScope(merchant_id=merchant_id, buyer_id=buyer_id))
        order = repo.get_by_id(order_id)
        if order is None:
            raise DomainError("The order does not exist.", code=ErrorCode.NOT_FOUND)
        return _order_to_schema(order)
