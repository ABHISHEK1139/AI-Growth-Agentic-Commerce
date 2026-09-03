"""Empirical Challenger 1 Test Suite.

Aggressively tests:
1. Terminal state immutability in services/checkout/transitions.py
2. Atomic inventory reservation & single-winner race conditions in services/inventory/repository.py
3. Minor unit integer paise arithmetic in packages/money/
4. Price hash computation & Property 5 revalidation in services/payments/service.py
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.money import (
    MoneyValueError,
    add_minor_units,
    calculate_total_minor,
    format_currency,
    format_minor_units,
    multiply_minor_units,
    parse_major_units,
    subtract_minor_units,
    sum_minor_units,
)
from services.authorization.models import Authorization
from services.checkout.hash import PriceSnapshot, compute_price_hash
from services.checkout.models import Checkout
from services.checkout.transitions import (
    TERMINAL_STATES,
    TransitionContext,
    TransitionEvent,
    transition,
)
from services.inventory.models import Inventory, Reservation
from services.inventory.repository import commit, release, reserve
from services.offers.models import Offer
from services.payments.models import Payment
from services.payments.provider import FakePaymentProvider
from services.payments.service import PaymentService


@dataclass
class MutableAggregate:
    status: str
    aggregate_id: str = "agg_test_1"
    aggregate_type: str = "checkout"


class DummySession:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.committed = False

    def execute(self, statement, parameters=None):  # noqa: ANN001, ANN201
        self.calls.append((statement, parameters))

    def commit(self) -> None:
        self.committed = True


# ==============================================================================
# SECTION 1: Exhaustive Terminal State Immutability Verification
# ==============================================================================


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_STATES))
@pytest.mark.parametrize("event", list(TransitionEvent))
def test_all_terminal_states_reject_all_events(
    monkeypatch, terminal_state: str, event: TransitionEvent
):
    """Every terminal state MUST reject every transition event with ALREADY_FINALIZED."""
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_1"
    )
    aggregate = MutableAggregate(status=terminal_state, aggregate_type="checkout")
    context = TransitionContext(
        actor_type="system",
        actor_id="tester",
        values={"quantity": 1},
        supplied_price_hash="hash",
        persisted_price_hash="hash",
        authorization_valid=True,
    )
    session = DummySession()

    with pytest.raises(DomainError) as exc_info:
        transition(aggregate, event, context, session)

    assert exc_info.value.code == ErrorCode.ALREADY_FINALIZED
    assert aggregate.status == terminal_state  # State must remain untouched


@pytest.mark.parametrize(
    "terminal_state",
    [
        "price_changed",
        "inventory_changed",
        "policy_blocked",
        "expired",
        "cancelled",
        "payment_failed",
        "manual_review",
        "completed",
    ],
)
@pytest.mark.parametrize(
    "event",
    [
        TransitionEvent.CREATE_PAYMENT,
        TransitionEvent.COMPLETE_ORDER,
        TransitionEvent.CANCEL_CHECKOUT,
        TransitionEvent.VERIFY_PAYMENT,
    ],
)
def test_lowercase_terminal_states_normalized_and_rejected(
    monkeypatch, terminal_state: str, event: TransitionEvent
):
    """Lowercase domain representations of terminal states must be normalized and rejected."""
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_1"
    )
    aggregate = MutableAggregate(status=terminal_state, aggregate_type="checkout")
    context = TransitionContext(
        actor_type="system",
        actor_id="tester",
        values={"quantity": 1},
        supplied_price_hash="hash",
        persisted_price_hash="hash",
    )
    session = DummySession()

    with pytest.raises(DomainError) as exc_info:
        transition(aggregate, event, context, session)

    assert exc_info.value.code == ErrorCode.ALREADY_FINALIZED
    assert aggregate.status == terminal_state


def test_non_terminal_illegal_transitions_raise_illegal_transition_not_finalized(monkeypatch):
    """Non-terminal states attempting invalid transitions must raise ILLEGAL_TRANSITION."""
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_1"
    )
    aggregate = MutableAggregate(status="CHECKOUT_CREATED", aggregate_type="checkout")
    # CREATE_PAYMENT is not valid directly from CHECKOUT_CREATED (requires AUTHORIZED)
    context = TransitionContext(actor_type="buyer", actor_id="buy_1")
    session = DummySession()

    with pytest.raises(DomainError) as exc_info:
        transition(aggregate, TransitionEvent.CREATE_PAYMENT, context, session)

    assert exc_info.value.code == ErrorCode.ILLEGAL_TRANSITION
    assert aggregate.status == "CHECKOUT_CREATED"


# ==============================================================================
# SECTION 2: Atomic Inventory Reservation & Concurrency Verification
# ==============================================================================


@pytest.fixture
def sqlite_inventory_file(tmp_path):
    """Create a real SQLite database file with WAL mode for multi-threaded concurrency testing."""
    db_path = tmp_path / "inventory_race.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 30.0, "check_same_thread": False},
        echo=False,
    )
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA busy_timeout=30000;"))

    Inventory.__table__.create(engine)
    Reservation.__table__.create(engine)

    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()

    # Seed 1 item with available_quantity=1, reserved_quantity=0, version=1
    inv = Inventory(
        offer_id="off_single_unit",
        available_quantity=1,
        reserved_quantity=0,
        version=1,
    )
    session.add(inv)
    session.commit()
    session.close()

    return SessionFactory, engine


def test_atomic_inventory_single_winner_race(sqlite_inventory_file):
    """When 20 concurrent threads attempt to reserve the 1 and only available unit,

    EXACTLY ONE must succeed and 19 must fail.
    """
    SessionFactory, _ = sqlite_inventory_file

    results = []

    def attempt_reservation(thread_idx: int):
        s = SessionFactory()
        try:
            res = reserve(s, "off_single_unit", 1)
            s.commit()
            return res
        finally:
            s.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(attempt_reservation, i) for i in range(20)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r is not None]
    failures = [r for r in results if r is None]

    assert len(successes) == 1, f"Expected exactly 1 winner, got {len(successes)}"
    assert len(failures) == 19

    winner_res = successes[0]
    assert winner_res.available_quantity == 1
    assert winner_res.reserved_quantity == 1
    assert winner_res.version == 2

    # Verify final state in DB
    verify_session = SessionFactory()
    final_inv = (
        verify_session.query(Inventory).filter(Inventory.offer_id == "off_single_unit").first()
    )
    assert final_inv.available_quantity == 1
    assert final_inv.reserved_quantity == 1
    assert final_inv.version == 2
    verify_session.close()


def test_inventory_double_release_prevention(sqlite_inventory_file):
    """Releasing the same reservation ID multiple times concurrently must succeed exactly once."""
    SessionFactory, _ = sqlite_inventory_file

    # Create a held reservation
    s = SessionFactory()
    reserve(s, "off_single_unit", 1)
    resv = Reservation(
        reservation_id="rsv_race_1",
        checkout_id="chk_race_1",
        offer_id="off_single_unit",
        quantity=1,
        status="held",
    )
    s.add(resv)
    s.commit()
    s.close()

    results = []

    def attempt_release():
        sess = SessionFactory()
        try:
            outcome = release(sess, "rsv_race_1")
            sess.commit()
            return outcome
        finally:
            sess.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(attempt_release) for _ in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    assert results.count(True) == 1
    assert results.count(False) == 9

    # Inventory reserved quantity must be decremented back to 0
    verify_session = SessionFactory()
    final_inv = (
        verify_session.query(Inventory).filter(Inventory.offer_id == "off_single_unit").first()
    )
    assert final_inv.reserved_quantity == 0
    assert final_inv.available_quantity == 1
    verify_session.close()


def test_inventory_double_commit_prevention(sqlite_inventory_file):
    """Committing the same reservation ID multiple times concurrently must succeed exactly once."""
    SessionFactory, _ = sqlite_inventory_file

    # Create a held reservation
    s = SessionFactory()
    reserve(s, "off_single_unit", 1)
    resv = Reservation(
        reservation_id="rsv_commit_race",
        checkout_id="chk_commit_race",
        offer_id="off_single_unit",
        quantity=1,
        status="held",
    )
    s.add(resv)
    s.commit()
    s.close()

    results = []

    def attempt_commit():
        sess = SessionFactory()
        try:
            outcome = commit(sess, "rsv_commit_race")
            sess.commit()
            return outcome
        finally:
            sess.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(attempt_commit) for _ in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    assert results.count(True) == 1
    assert results.count(False) == 9

    # Inventory reserved quantity must be decremented to 0 and available to 0
    verify_session = SessionFactory()
    final_inv = (
        verify_session.query(Inventory).filter(Inventory.offer_id == "off_single_unit").first()
    )
    assert final_inv.reserved_quantity == 0
    assert final_inv.available_quantity == 0
    verify_session.close()


# ==============================================================================
# SECTION 3: Minor Unit Integer Paise Arithmetic Verification
# ==============================================================================


def test_money_exact_integer_arithmetic():
    """Verify exact minor unit operations."""
    # Addition
    assert add_minor_units(100, 200, 300) == 600
    assert sum_minor_units([50, 150, 200]) == 400

    # Subtraction
    assert subtract_minor_units(1000, 250) == 750
    assert subtract_minor_units(500, 500) == 0
    with pytest.raises(MoneyValueError, match="cannot exceed"):
        subtract_minor_units(500, 501)

    # Multiplication
    assert multiply_minor_units(1250, 4) == 5000
    with pytest.raises(MoneyValueError, match="at least one"):
        multiply_minor_units(1250, 0)
    with pytest.raises(MoneyValueError, match="non-negative"):
        multiply_minor_units(1250, -2)

    # Full checkout calculation
    total = calculate_total_minor(
        unit_price_minor=249900,  # ₹2,499.00
        quantity=3,  # ₹7,497.00
        shipping_minor=15000,  # +₹150.00 = ₹7,647.00
        tax_minor=134946,  # +18% GST = ₹1,349.46 = ₹8,996.46
        discount_minor=50000,  # -₹500.00 = ₹8,496.46
    )
    assert total == 849646


@pytest.mark.parametrize("bad_val", [12.50, 0.0, -1.0, float("inf"), float("nan"), True, False])
def test_money_rejects_floating_point_and_boolean(bad_val):
    """Floating point and booleans must be strictly rejected."""
    with pytest.raises(TypeError):
        add_minor_units(bad_val)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        parse_major_units(bad_val)  # type: ignore[arg-type]


def test_money_formatting_and_parsing():
    """Test format and parse with various currency representations."""
    # Format
    assert format_minor_units(10050) == "100.50"
    assert format_minor_units(10050, currency="INR") == "INR 100.50"
    assert format_currency(123456789, "INR") == "INR 1,234,567.89"

    # Parse
    assert parse_major_units("100.50") == 10050
    assert parse_major_units("INR 100.50", currency="INR") == 10050
    assert parse_major_units("₹ 1,234,567.89", currency="INR") == 123456789
    assert parse_major_units("$ 99.99", currency="USD") == 9999
    assert parse_major_units(100, currency="INR") == 10000  # Major integer unit to minor

    # Fractional paise rejection (no rounding)
    with pytest.raises(MoneyValueError, match="fractional digits"):
        parse_major_units("100.505", currency="INR")

    with pytest.raises(MoneyValueError, match="fractional digits"):
        parse_major_units("100.999", currency="USD")


# ==============================================================================
# SECTION 4: Price Hash Computation & Property 5 Revalidation
# ==============================================================================


def test_price_hash_determinism_and_sensitivity():
    """Price hash must be cryptographically deterministic and sensitive to all parameters."""
    now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    snap1 = PriceSnapshot(
        offer_id="off_test_100",
        offer_version=1,
        unit_price_minor=50000,
        quantity=2,
        shipping_minor=1000,
        tax_minor=9000,
        discount_minor=2000,
        currency="INR",
        expires_at=now,
    )
    snap2 = PriceSnapshot(
        offer_id="off_test_100",
        offer_version=1,
        unit_price_minor=50000,
        quantity=2,
        shipping_minor=1000,
        tax_minor=9000,
        discount_minor=2000,
        currency="INR",
        expires_at=now,
    )

    hash1 = compute_price_hash(snap1)
    hash2 = compute_price_hash(snap2)
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex digest

    # Modifying price by 1 paisa alters hash
    snap_price_diff = PriceSnapshot(
        offer_id="off_test_100",
        offer_version=1,
        unit_price_minor=50001,
        quantity=2,
        shipping_minor=1000,
        tax_minor=9000,
        discount_minor=2000,
        currency="INR",
        expires_at=now,
    )
    assert compute_price_hash(snap_price_diff) != hash1

    # Modifying version alters hash
    snap_ver_diff = PriceSnapshot(
        offer_id="off_test_100",
        offer_version=2,
        unit_price_minor=50000,
        quantity=2,
        shipping_minor=1000,
        tax_minor=9000,
        discount_minor=2000,
        currency="INR",
        expires_at=now,
    )
    assert compute_price_hash(snap_ver_diff) != hash1


def test_property_5_price_change_revalidation_in_payment_service(monkeypatch):
    """Property 5: Revalidation at payment creation aborts if merchant updated offer price."""
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_1"
    )
    monkeypatch.setattr("services.payments.service.append_event", lambda *_a, **_k: "aud_2")

    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=30)

    # Initial offer at 10,000 paise (₹100)
    offer = Offer(
        offer_id="off_prop5",
        catalog_version_id="cat_1",
        merchant_id="mrc_prop5",
        product_id="prd_prop5",
        offer_version=1,
        unit_price_minor=10000,
        currency="INR",
        status="active",
        expires_at=expiry,
    )

    initial_snapshot = {
        "offer_id": "off_prop5",
        "offer_version": 1,
        "unit_price_minor": 10000,
        "quantity": 1,
        "shipping_minor": 0,
        "tax_minor": 0,
        "discount_minor": 0,
        "currency": "INR",
        "expires_at": expiry.isoformat(),
    }
    initial_hash = compute_price_hash(initial_snapshot)

    checkout = Checkout(
        checkout_id="chk_prop5",
        merchant_id="mrc_prop5",
        buyer_id="buy_prop5",
        offer_id="off_prop5",
        status="authorized",
        subtotal_minor=10000,
        total_minor=10000,
        currency="INR",
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        price_hash=initial_hash,
        price_snapshot=initial_snapshot,
        expires_at=expiry,
        created_at=now,
    )

    auth = Authorization(
        authorization_id="auth_prop5",
        merchant_id="mrc_prop5",
        buyer_id="buy_prop5",
        checkout_id="chk_prop5",
        price_hash=initial_hash,
        amount_ceiling_minor=10000,
        currency="INR",
        policy_version="1.0",
        status="approved",
        valid_until=expiry,
        created_at=now,
    )

    # Mock Session
    session = MagicMock()

    def mock_query(model):
        mock = MagicMock()
        if model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Authorization:
            mock.filter.return_value.first.return_value = auth
        elif model == Payment:
            mock.filter.return_value.first.return_value = None
        return mock

    session.query.side_effect = mock_query

    # Merchant maliciously/legitimately changes price from 10000 to 15000 paise before buyer creates payment
    offer.unit_price_minor = 15000
    offer.offer_version = 2

    fake_provider = FakePaymentProvider()
    mock_auth_svc = MagicMock()
    mock_auth_svc.revalidate_for_payment.return_value = auth
    svc = PaymentService(provider=fake_provider, auth_service=mock_auth_svc)

    with pytest.raises(DomainError) as exc_info:
        svc.create_payment(
            session,
            buyer_id="buy_prop5",
            merchant_id="mrc_prop5",
            checkout_id="chk_prop5",
            authorization_id="auth_prop5",
            now=now,
        )

    assert exc_info.value.code == ErrorCode.PRICE_CHANGED

    # Checkout status must have transitioned to price_changed (terminal)
    assert checkout.status == "price_changed"

    # Crucial security guarantee: NO charge / order was created at the payment provider
    assert fake_provider.order_count_for("chk_prop5") == 0
    assert len(fake_provider._orders) == 0
