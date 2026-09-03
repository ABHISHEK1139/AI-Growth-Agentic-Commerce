from unittest.mock import MagicMock

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.inventory.errors import InventoryUnavailableError
from services.inventory.models import Reservation
from services.inventory.repository import release, reserve
from services.inventory.service import InventoryService


def test_reserve_creates_reservation():
    session = MagicMock()
    # Mocking the repository function
    session.execute.return_value.fetchone.return_value = (10, 5, 2)

    service = InventoryService()
    reservation = service.reserve_stock(session, "offer_1", "checkout_1", 2)

    assert reservation.checkout_id == "checkout_1"
    assert reservation.offer_id == "offer_1"
    assert reservation.quantity == 2
    assert reservation.status == "held"
    session.add.assert_called_once_with(reservation)


def test_reserve_insufficient_stock_raises():
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = None

    service = InventoryService()
    with pytest.raises(InventoryUnavailableError):
        service.reserve_stock(session, "offer_1", "checkout_1", 20)


def test_release_held_reservation(monkeypatch):
    session = MagicMock()
    reservation = Reservation(reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="held")

    # Mock get_reservation
    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)
    # Mock release repository
    monkeypatch.setattr("services.inventory.service.release", lambda s, r: True)

    service = InventoryService()
    service.release_stock(session, "checkout_1")

    # Should append an audit event
    assert session.execute.called


def test_release_already_released_is_noop(monkeypatch):
    session = MagicMock()
    reservation = Reservation(
        reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="released"
    )

    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)

    service = InventoryService()
    service.release_stock(session, "checkout_1")

    # Session shouldn't be touched for update or events
    session.execute.assert_not_called()


def test_commit_reservation(monkeypatch):
    session = MagicMock()
    reservation = Reservation(reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="held")

    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)
    monkeypatch.setattr("services.inventory.service.commit", lambda s, r: True)

    service = InventoryService()
    service.commit_stock(session, "checkout_1")

    # Should append an audit event
    assert session.execute.called


def test_commit_stock_raises_when_reservation_missing(monkeypatch):
    """Attempting to commit a non-existent reservation raises INVENTORY_UNAVAILABLE (BUG-39)."""
    session = MagicMock()
    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: None)

    service = InventoryService()
    with pytest.raises(DomainError) as exc_info:
        service.commit_stock(session, "chk_nonexistent")
    assert exc_info.value.code == ErrorCode.INVENTORY_UNAVAILABLE


def test_commit_stock_raises_when_reservation_already_released(monkeypatch):
    """Attempting to commit a released reservation raises ILLEGAL_TRANSITION (BUG-39)."""
    session = MagicMock()
    reservation = Reservation(
        reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="released"
    )
    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)

    service = InventoryService()
    with pytest.raises(DomainError) as exc_info:
        service.commit_stock(session, "chk_released")
    assert exc_info.value.code == ErrorCode.ILLEGAL_TRANSITION


def test_commit_stock_raises_when_commit_fails(monkeypatch):
    """If DB-level commit returns False, commit_stock raises INVENTORY_UNAVAILABLE (BUG-39)."""
    session = MagicMock()
    reservation = Reservation(reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="held")
    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)
    monkeypatch.setattr("services.inventory.service.commit", lambda s, r: False)

    service = InventoryService()
    with pytest.raises(DomainError) as exc_info:
        service.commit_stock(session, "chk_failed_commit")
    assert exc_info.value.code == ErrorCode.INVENTORY_UNAVAILABLE


def test_commit_stock_idempotent_when_already_committed(monkeypatch):
    """Attempting to commit an already committed reservation returns without error."""
    session = MagicMock()
    reservation = Reservation(
        reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="committed"
    )
    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)

    service = InventoryService()
    service.commit_stock(session, "chk_already_committed")
    session.execute.assert_not_called()


def test_reservation_status_tracking():
    # To test repository functions behavior on tracking status
    session = MagicMock()

    # release() returns True when both reservation and inventory rows update
    session.execute.return_value.fetchone.return_value = ("offer_1", 2)
    assert release(session, "rsv_1") is True

    # release() returns False when reservation not found / not held
    session.execute.return_value.fetchone.return_value = None
    assert release(session, "rsv_1") is False


def test_release_fails_when_inventory_guard_matches_zero_rows():
    """Verify release() returns False if the inventory decrement matches zero rows (BUG-36)."""
    session = MagicMock()
    # First statement (reservation UPDATE) returns valid row, second statement (inventory UPDATE) returns None
    session.execute.return_value.fetchone.side_effect = [("offer_1", 2), None]
    assert release(session, "rsv_1") is False


def test_commit_fails_when_inventory_guard_matches_zero_rows():
    """Verify commit() returns False if the inventory decrement matches zero rows (BUG-36)."""
    from services.inventory.repository import commit

    session = MagicMock()
    # First statement (reservation UPDATE) returns valid row, second statement (inventory UPDATE) returns None
    session.execute.return_value.fetchone.side_effect = [("offer_1", 2), None]
    assert commit(session, "rsv_1") is False


def test_commit_succeeds_when_both_reservation_and_inventory_update():
    """Verify commit() returns True when both statements update rows."""
    from services.inventory.repository import commit

    session = MagicMock()
    session.execute.return_value.fetchone.side_effect = [("offer_1", 2), (8, 3, 2)]
    assert commit(session, "rsv_1") is True


def test_quantities_never_negative():
    session = MagicMock()
    # Test DB level constraint logic inside reserve which conditionally updates
    session.execute.return_value.fetchone.return_value = None
    assert reserve(session, "offer_1", 50) is None

    session.execute.return_value.fetchone.return_value = (10, 10, 2)
    res = reserve(session, "offer_1", 2)
    assert res is not None
    assert res.available_quantity == 10
    assert res.reserved_quantity == 10


def test_inventory_audit_events_preserve_merchant_id(monkeypatch):
    """Inventory audit events must have merchant_id populated so list_events can query them (BUG-42)."""
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = (10, 5, 2)

    service = InventoryService()
    # 1. reserve_stock with explicit merchant_id
    service.reserve_stock(session, "offer_1", "checkout_1", 2, merchant_id="mrc_demo")

    call_args = session.execute.call_args
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
    assert params["merchant_id"] == "mrc_demo"
    assert params["event_type"] == "INVENTORY_CHANGE_DETECTED"

    # 2. release_stock with explicit merchant_id
    reservation = Reservation(reservation_id="rsv_1", offer_id="offer_1", quantity=2, status="held")
    monkeypatch.setattr("services.inventory.service.get_reservation", lambda s, c: reservation)
    monkeypatch.setattr("services.inventory.service.release", lambda s, r: True)

    service.release_stock(session, "checkout_1", merchant_id="mrc_demo")
    call_args_rel = session.execute.call_args
    params_rel = call_args_rel[0][1] if len(call_args_rel[0]) > 1 else call_args_rel[1]
    assert params_rel["merchant_id"] == "mrc_demo"
    assert params_rel["event_type"] == "INVENTORY_CHANGE_DETECTED"
