from __future__ import annotations

import contextlib

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from services.audit.repository import append_transition_event
from services.inventory.errors import InventoryUnavailableError
from services.inventory.models import Reservation
from services.inventory.repository import (
    commit,
    get_reservation,
    release,
    reserve,
)


def _resolve_merchant_id(
    session: Session,
    *,
    offer_id: str | None = None,
    checkout_id: str | None = None,
    merchant_id: str | None = None,
) -> str | None:
    if merchant_id is not None:
        return merchant_id
    if checkout_id:
        with contextlib.suppress(Exception):
            from services.checkout.models import Checkout

            chk = session.get(Checkout, checkout_id)
            if chk and getattr(chk, "merchant_id", None):
                return str(chk.merchant_id)
            chk_q = session.query(Checkout).filter(Checkout.checkout_id == checkout_id).first()
            if chk_q and getattr(chk_q, "merchant_id", None):
                return str(chk_q.merchant_id)
    if offer_id:
        with contextlib.suppress(Exception):
            from services.offers.models import Offer

            off = session.get(Offer, offer_id)
            if off and getattr(off, "merchant_id", None):
                return str(off.merchant_id)
            off_q = session.query(Offer).filter(Offer.offer_id == offer_id).first()
            if off_q and getattr(off_q, "merchant_id", None):
                return str(off_q.merchant_id)
    return None


class InventoryService:
    """Coordinates inventory reservation lifecycle."""

    def reserve_stock(
        self,
        session: Session,
        offer_id: str,
        checkout_id: str,
        quantity: int,
        merchant_id: str | None = None,
    ) -> Reservation:
        """Reserve inventory for a checkout.

        Raises InventoryUnavailableError if stock is insufficient.
        """
        # Try to reserve quantity atomically
        result = reserve(session, offer_id, quantity)
        if result is None:
            raise InventoryUnavailableError()

        from datetime import UTC, datetime

        # Create reservation record
        reservation = Reservation(
            reservation_id=new_id("rsv"),
            checkout_id=checkout_id,
            offer_id=offer_id,
            quantity=quantity,
            status="held",
            created_at=datetime.now(UTC),
        )
        session.add(reservation)
        session.flush()

        resolved_merchant_id = _resolve_merchant_id(
            session,
            offer_id=offer_id,
            checkout_id=checkout_id,
            merchant_id=merchant_id,
        )

        append_transition_event(
            session,
            aggregate_type="inventory",
            aggregate_id=offer_id,
            event_type="INVENTORY_CHANGE_DETECTED",
            actor_type="system",
            actor_id=None,
            merchant_id=resolved_merchant_id,
            metadata={
                "action": "reserve",
                "checkout_id": checkout_id,
                "quantity": quantity,
                "new_available": result.available_quantity,
                "new_reserved": result.reserved_quantity,
                "version": result.version,
            },
        )

        return reservation

    def release_stock(
        self,
        session: Session,
        checkout_id: str,
        merchant_id: str | None = None,
    ) -> None:
        """Release reserved inventory. Idempotent."""
        reservation = get_reservation(session, checkout_id)
        if not reservation or reservation.status != "held":
            return

        if release(session, reservation.reservation_id):
            resolved_merchant_id = _resolve_merchant_id(
                session,
                offer_id=reservation.offer_id,
                checkout_id=checkout_id,
                merchant_id=merchant_id,
            )

            append_transition_event(
                session,
                aggregate_type="inventory",
                aggregate_id=reservation.offer_id,
                event_type="INVENTORY_CHANGE_DETECTED",
                actor_type="system",
                actor_id=None,
                merchant_id=resolved_merchant_id,
                metadata={
                    "action": "release",
                    "checkout_id": checkout_id,
                    "reservation_id": reservation.reservation_id,
                    "quantity": reservation.quantity,
                },
            )

    def commit_stock(
        self,
        session: Session,
        checkout_id: str,
        merchant_id: str | None = None,
    ) -> None:
        """Commit reserved inventory after payment (Requirement 10.7, BUG-39)."""
        reservation = get_reservation(session, checkout_id)
        if not reservation:
            raise DomainError(
                f"No inventory reservation found for checkout {checkout_id}.",
                code=ErrorCode.INVENTORY_UNAVAILABLE,
            )
        if reservation.status == "committed":
            return
        if reservation.status != "held":
            raise DomainError(
                f"Cannot commit inventory: reservation {reservation.reservation_id} is in status '{reservation.status}'.",
                code=ErrorCode.ILLEGAL_TRANSITION,
            )

        if not commit(session, reservation.reservation_id):
            raise DomainError(
                f"Failed to commit inventory for reservation {reservation.reservation_id}: insufficient stock or version conflict.",
                code=ErrorCode.INVENTORY_UNAVAILABLE,
            )

        resolved_merchant_id = _resolve_merchant_id(
            session,
            offer_id=reservation.offer_id,
            checkout_id=checkout_id,
            merchant_id=merchant_id,
        )

        append_transition_event(
            session,
            aggregate_type="inventory",
            aggregate_id=reservation.offer_id,
            event_type="INVENTORY_CHANGE_DETECTED",
            actor_type="system",
            actor_id=None,
            merchant_id=resolved_merchant_id,
            metadata={
                "action": "commit",
                "checkout_id": checkout_id,
                "reservation_id": reservation.reservation_id,
                "quantity": reservation.quantity,
            },
        )
