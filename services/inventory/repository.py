from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.inventory.models import Inventory, Reservation


@dataclass(frozen=True, slots=True)
class ReservationResult:
    available_quantity: int
    reserved_quantity: int
    version: int


def reserve(session: Session, offer_id: str, quantity: int) -> ReservationResult | None:
    """Attempt to conditionally increment reserved quantity for an offer.

    Returns new quantities and version on success, None if unavailable.
    """
    stmt = text(
        """
        UPDATE inventory
           SET reserved_quantity = reserved_quantity + :qty,
               version = version + 1
         WHERE offer_id = :offer_id
           AND (available_quantity - reserved_quantity) >= :qty
        RETURNING available_quantity, reserved_quantity, version;
        """
    )
    result = session.execute(stmt, {"qty": quantity, "offer_id": offer_id}).fetchone()
    if not result:
        return None
    return ReservationResult(
        available_quantity=result[0],
        reserved_quantity=result[1],
        version=result[2],
    )


def release(session: Session, reservation_id: str) -> bool:
    """Release a held reservation.

    Status checks prevent double-releases.
    """
    stmt_res = text(
        """
        UPDATE reservation
           SET status = 'released',
               released_at = CURRENT_TIMESTAMP
         WHERE reservation_id = :reservation_id
           AND status = 'held'
        RETURNING offer_id, quantity;
        """
    )
    res_row = session.execute(stmt_res, {"reservation_id": reservation_id}).fetchone()
    if not res_row:
        return False

    offer_id = res_row[0]
    quantity = res_row[1]

    stmt_inv = text(
        """
        UPDATE inventory
           SET reserved_quantity = reserved_quantity - :qty,
               version = version + 1
         WHERE offer_id = :offer_id AND reserved_quantity >= :qty
        RETURNING available_quantity, reserved_quantity, version;
        """
    )
    inv_res = session.execute(stmt_inv, {"qty": quantity, "offer_id": offer_id}).fetchone()
    return bool(inv_res)


def commit(session: Session, reservation_id: str) -> bool:
    """Commit a held reservation, decrementing both counters on the inventory."""
    # First, mark the reservation as committed
    stmt_res = text(
        """
        UPDATE reservation
           SET status = 'committed',
               committed_at = CURRENT_TIMESTAMP
         WHERE reservation_id = :reservation_id
           AND status = 'held'
        RETURNING offer_id, quantity;
        """
    )
    res_row = session.execute(stmt_res, {"reservation_id": reservation_id}).fetchone()
    if not res_row:
        return False

    offer_id = res_row[0]
    quantity = res_row[1]

    # Then decrement inventory counters
    stmt_inv = text(
        """
        UPDATE inventory
           SET reserved_quantity = reserved_quantity - :qty,
               available_quantity = available_quantity - :qty,
               version = version + 1
         WHERE offer_id = :offer_id AND reserved_quantity >= :qty
        RETURNING available_quantity, reserved_quantity, version;
        """
    )
    inv_res = session.execute(stmt_inv, {"qty": quantity, "offer_id": offer_id}).fetchone()
    return bool(inv_res)


def get_reservation(session: Session, checkout_id: str) -> Reservation | None:
    """Get reservation state for a given checkout."""
    return session.query(Reservation).filter(Reservation.checkout_id == checkout_id).first()


def get_inventory(session: Session, offer_id: str) -> Inventory | None:
    """Get inventory for a given offer."""
    return session.query(Inventory).filter(Inventory.offer_id == offer_id).first()
