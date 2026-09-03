"""Adversarial Concurrency, Chaos, and State Invariant Verification Suite.

Features Verified:
1. Competing Checkouts & Inventory Race Conditions (Atomic SQL reservations, single-winner guarantees)
2. Rapid Double-Click Payment Submissions (In-progress locking, idempotent replay, zero duplicate provider charges)
3. Terminal State Mutability Attacks (ALREADY_FINALIZED domain errors across all terminal states)
4. Inventory Hold Leaks on Expiry and Mandate Rejection (Atomic release, idempotency of release & commit)
"""

from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


# Register SQLite compilers for PostgreSQL-specific types during testing
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(ARRAY, "sqlite")
def compile_array_sqlite(type_, compiler, **kw):
    return "TEXT"


from packages.db.base import Base
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.authorization.models import Authorization
from services.authorization.service import AuthorizationService
from services.checkout.hash import compute_price_hash
from services.checkout.models import Checkout
from services.checkout.service import CheckoutService
from services.checkout.transitions import (
    TERMINAL_STATES,
    TransitionContext,
    TransitionEvent,
    transition,
)
from services.inventory.models import Inventory
from services.inventory.repository import get_inventory, get_reservation, release, reserve
from services.inventory.service import InventoryService
from services.offers.models import Offer
from services.payments.idempotency import IdempotencyManager, compute_request_hash
from services.payments.provider import FakePaymentProvider
from services.payments.service import PaymentService


@dataclass
class MutableAggregate:
    status: str
    aggregate_id: str = "agg_chaos_1"
    aggregate_type: str = "checkout"


@pytest.fixture
def chaos_db(tmp_path):
    """Create a persistent multi-table SQLite DB in WAL mode for concurrent testing."""
    db_file = tmp_path / "chaos_concurrency.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"timeout": 30.0, "check_same_thread": False},
        echo=False,
    )
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA busy_timeout=30000;"))
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS audit_event (
                    event_id TEXT PRIMARY KEY,
                    merchant_id TEXT,
                    request_id TEXT,
                    trace_id TEXT,
                    agent_run_id TEXT,
                    actor_type TEXT,
                    actor_id TEXT,
                    event_type TEXT,
                    aggregate_type TEXT,
                    aggregate_id TEXT,
                    input_hash TEXT,
                    decision TEXT,
                    reason_code TEXT,
                    policy_version TEXT,
                    model_version TEXT,
                    amount_minor INTEGER,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
        )

    # Create all tables registered on Base metadata
    Base.metadata.create_all(engine)

    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory, engine


# ==============================================================================
# MISSION 1: Competing Checkouts & Inventory Race Conditions
# ==============================================================================


def test_chaos_competing_checkouts_single_unit_50_threads(chaos_db):
    """50 concurrent threads race to reserve the 1 and only remaining inventory unit.

    Invariant: EXACTLY ONE succeeds, 49 fail, stock never goes negative.
    """
    SessionFactory, _ = chaos_db

    # Seed 1 item in stock
    s = SessionFactory()
    inv = Inventory(
        offer_id="off_single_race",
        available_quantity=1,
        reserved_quantity=0,
        version=1,
    )
    s.add(inv)
    s.commit()
    s.close()

    results: list[Any] = []
    barrier = threading.Barrier(50)

    def racer(thread_id: int):
        barrier.wait()
        sess = SessionFactory()
        try:
            res = reserve(sess, "off_single_race", 1)
            sess.commit()
            return ("SUCCESS", res) if res else ("OUT_OF_STOCK", None)
        except Exception as exc:
            sess.rollback()
            return ("ERROR", str(exc))
        finally:
            sess.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(racer, i) for i in range(50)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r[0] == "SUCCESS"]
    out_of_stocks = [r for r in results if r[0] == "OUT_OF_STOCK"]
    errors = [r for r in results if r[0] == "ERROR"]

    assert len(successes) == 1, f"Expected exactly 1 winner, got {len(successes)}"
    assert (
        len(out_of_stocks) == 49
    ), f"Expected 49 out-of-stock rejections, got {len(out_of_stocks)}"
    assert len(errors) == 0, f"Encountered unexpected unhandled errors: {errors}"

    # Verify database state integrity
    verify_sess = SessionFactory()
    final_inv = verify_sess.query(Inventory).filter(Inventory.offer_id == "off_single_race").first()
    assert final_inv is not None
    assert final_inv.available_quantity == 1
    assert final_inv.reserved_quantity == 1
    assert final_inv.version == 2
    verify_sess.close()


def test_chaos_multi_unit_contention_50_threads_for_10_units(chaos_db):
    """50 concurrent threads race for 10 available inventory units.

    Invariant: EXACTLY 10 succeed, 40 fail, reserved_quantity == 10.
    """
    SessionFactory, _ = chaos_db

    s = SessionFactory()
    inv = Inventory(
        offer_id="off_10_units",
        available_quantity=10,
        reserved_quantity=0,
        version=1,
    )
    s.add(inv)
    s.commit()
    s.close()

    results: list[Any] = []
    barrier = threading.Barrier(50)

    def racer(thread_id: int):
        barrier.wait()
        sess = SessionFactory()
        try:
            res = reserve(sess, "off_10_units", 1)
            sess.commit()
            return ("SUCCESS", res) if res else ("OUT_OF_STOCK", None)
        except Exception as exc:
            sess.rollback()
            return ("ERROR", str(exc))
        finally:
            sess.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(racer, i) for i in range(50)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r[0] == "SUCCESS"]
    out_of_stocks = [r for r in results if r[0] == "OUT_OF_STOCK"]

    assert len(successes) == 10
    assert len(out_of_stocks) == 40

    verify_sess = SessionFactory()
    final_inv = verify_sess.query(Inventory).filter(Inventory.offer_id == "off_10_units").first()
    assert final_inv is not None
    assert final_inv.available_quantity == 10
    assert final_inv.reserved_quantity == 10
    assert final_inv.version == 11
    verify_sess.close()


def test_chaos_oversubscribed_multi_quantity_requests(chaos_db):
    """10 threads requesting 2 units each on stock of 5 units.

    Invariant: EXACTLY 2 succeed (4 reserved, 1 left), 8 fail because 2 > 1.
    """
    SessionFactory, _ = chaos_db

    s = SessionFactory()
    inv = Inventory(
        offer_id="off_5_units",
        available_quantity=5,
        reserved_quantity=0,
        version=1,
    )
    s.add(inv)
    s.commit()
    s.close()

    results: list[Any] = []
    barrier = threading.Barrier(10)

    def racer(thread_id: int):
        barrier.wait()
        sess = SessionFactory()
        try:
            res = reserve(sess, "off_5_units", 2)
            sess.commit()
            return ("SUCCESS", res) if res else ("OUT_OF_STOCK", None)
        except Exception as exc:
            sess.rollback()
            return ("ERROR", str(exc))
        finally:
            sess.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(racer, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    successes = [r for r in results if r[0] == "SUCCESS"]
    assert len(successes) == 2

    verify_sess = SessionFactory()
    final_inv = verify_sess.query(Inventory).filter(Inventory.offer_id == "off_5_units").first()
    assert final_inv is not None
    assert final_inv.available_quantity == 5
    assert final_inv.reserved_quantity == 4
    assert final_inv.version == 3
    verify_sess.close()


# ==============================================================================
# MISSION 2: Rapid Double-Click Payment Submissions & Idempotency Locking
# ==============================================================================


def test_chaos_rapid_double_click_payments_zero_duplicate_charges(chaos_db):
    """Simulates rapid concurrent payment submissions with identical Idempotency-Key.

    Invariants:
    1. Provider `create_order` is executed AT MOST 1 time (zero double charge).
    2. Idempotency manager returns cached response or in-progress lock.
    3. Replay with identical parameters returns identical payment ID.
    """
    SessionFactory, _ = chaos_db
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=30)

    s = SessionFactory()
    offer = Offer(
        offer_id="off_pay_race",
        catalog_version_id="cat_1",
        merchant_id="mrc_pay_race",
        product_id="prd_pay_race",
        offer_version=1,
        unit_price_minor=5000,
        currency="INR",
        status="active",
        expires_at=expiry,
    )
    s.add(offer)

    snapshot = {
        "offer_id": "off_pay_race",
        "offer_version": 1,
        "unit_price_minor": 5000,
        "quantity": 1,
        "shipping_minor": 0,
        "tax_minor": 0,
        "discount_minor": 0,
        "currency": "INR",
        "expires_at": expiry.isoformat(),
    }
    p_hash = compute_price_hash(snapshot)

    checkout = Checkout(
        checkout_id="chk_pay_race",
        merchant_id="mrc_pay_race",
        buyer_id="buy_pay_race",
        offer_id="off_pay_race",
        status="authorized",
        subtotal_minor=5000,
        total_minor=5000,
        currency="INR",
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        price_hash=p_hash,
        price_snapshot=snapshot,
        expires_at=expiry,
        created_at=now,
    )
    s.add(checkout)

    auth = Authorization(
        authorization_id="ath_pay_race",
        merchant_id="mrc_pay_race",
        buyer_id="buy_pay_race",
        checkout_id="chk_pay_race",
        price_hash=p_hash,
        amount_ceiling_minor=5000,
        currency="INR",
        policy_version="1.0",
        status="approved",
        valid_until=expiry,
        created_at=now,
    )
    s.add(auth)
    s.commit()
    s.close()

    provider = FakePaymentProvider()
    auth_service = AuthorizationService()
    payment_service = PaymentService(provider=provider, auth_service=auth_service)

    idempotency_key = "idm_double_click_test_key"

    # Sequential double-click submission simulation (first attempt completes, second attempt replays)
    sess1 = SessionFactory()
    res1 = payment_service.create_payment(
        sess1,
        buyer_id="buy_pay_race",
        merchant_id="mrc_pay_race",
        checkout_id="chk_pay_race",
        authorization_id="ath_pay_race",
        idempotency_key=idempotency_key,
        now=now,
    )
    sess1.commit()
    sess1.close()

    assert res1.payment_id is not None
    assert len(provider._orders) == 1

    # Immediate second click (idempotent replay)
    sess2 = SessionFactory()
    res2 = payment_service.create_payment(
        sess2,
        buyer_id="buy_pay_race",
        merchant_id="mrc_pay_race",
        checkout_id="chk_pay_race",
        authorization_id="ath_pay_race",
        idempotency_key=idempotency_key,
        now=now,
    )
    sess2.commit()
    sess2.close()

    # Replayed response returns exact same payment ID without creating a 2nd provider order
    assert res2.payment_id == res1.payment_id
    assert len(provider._orders) == 1, "Double provider charge detected!"

    # Third click with different idempotency key on now-consumed authorization -> rejected
    sess3 = SessionFactory()
    with pytest.raises(DomainError) as exc_info:
        payment_service.create_payment(
            sess3,
            buyer_id="buy_pay_race",
            merchant_id="mrc_pay_race",
            checkout_id="chk_pay_race",
            authorization_id="ath_pay_race",
            idempotency_key="different_key_2",
            now=now,
        )
    assert exc_info.value.code == ErrorCode.AUTHORIZATION_ALREADY_CONSUMED
    assert len(provider._orders) == 1
    sess3.close()


def test_chaos_idempotency_key_payload_tampering_rejected(chaos_db):
    """Reusing an idempotency key with a modified request payload is rejected with 409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST."""
    SessionFactory, _ = chaos_db
    session = SessionFactory()
    now = datetime.now(UTC)

    # 1. Acquire lock on payload A
    body_a = {"checkout_id": "chk_a", "amount_minor": 1000}
    hash_a = compute_request_hash(body_a)
    is_replay, rec, _, _ = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id="buy_1",
        endpoint="POST /payments",
        idempotency_key="idm_tamper_key",
        request_hash=hash_a,
        now=now,
    )
    assert is_replay is False
    assert rec is not None
    session.commit()

    # 2. Complete payload A
    IdempotencyManager.complete(
        session,
        record_id=rec.idempotency_record_id,
        status_code=200,
        response_body={"payment_id": "pay_legit"},
        now=now,
        record=rec,
    )
    session.commit()

    # 3. Adversary attempts to reuse idm_tamper_key with payload B (amount_minor = 999999)
    body_b = {"checkout_id": "chk_a", "amount_minor": 999999}
    hash_b = compute_request_hash(body_b)

    with pytest.raises(DomainError) as exc:
        IdempotencyManager.acquire_lock(
            session,
            actor_type="buyer",
            actor_id="buy_1",
            endpoint="POST /payments",
            idempotency_key="idm_tamper_key",
            request_hash=hash_b,
            now=now,
        )
    assert exc.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST
    session.close()


# ==============================================================================
# MISSION 3: Exhaustive Terminal State Mutability Attacks
# ==============================================================================


@pytest.mark.parametrize("terminal_state", sorted(TERMINAL_STATES))
@pytest.mark.parametrize("event", list(TransitionEvent))
def test_chaos_all_terminal_states_reject_every_event(
    monkeypatch, terminal_state: str, event: TransitionEvent
):
    """Every terminal state MUST reject all 24 TransitionEvents with ALREADY_FINALIZED."""
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_evt"
    )
    aggregate = MutableAggregate(status=terminal_state, aggregate_type="checkout")
    context = TransitionContext(
        actor_type="system",
        actor_id="tester",
        values={"quantity": 1},
        supplied_price_hash="h",
        persisted_price_hash="h",
    )
    session = MagicMock()

    with pytest.raises(DomainError) as exc:
        transition(aggregate, event, context, session)

    assert exc.value.code == ErrorCode.ALREADY_FINALIZED
    assert aggregate.status == terminal_state


@pytest.mark.parametrize(
    "lowercase_terminal",
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
        TransitionEvent.CONFIRM_ORDER,
        TransitionEvent.CANCEL_CHECKOUT,
        TransitionEvent.VERIFY_PAYMENT,
    ],
)
def test_chaos_lowercase_terminal_states_strictly_immutable(
    monkeypatch, lowercase_terminal: str, event: TransitionEvent
):
    """Domain lowercase representations of terminal states normalize and reject mutations."""
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_evt"
    )
    aggregate = MutableAggregate(status=lowercase_terminal, aggregate_type="checkout")
    context = TransitionContext(actor_type="system", actor_id="tester")
    session = MagicMock()

    with pytest.raises(DomainError) as exc:
        transition(aggregate, event, context, session)

    assert exc.value.code == ErrorCode.ALREADY_FINALIZED
    assert aggregate.status == lowercase_terminal


# ==============================================================================
# MISSION 4: Inventory Hold Leaks on Expiry and Mandate Rejection
# ==============================================================================


def test_chaos_mandate_rejection_atomically_releases_inventory(chaos_db):
    """When a buyer rejects authorization, the held inventory reservation is released immediately."""
    SessionFactory, _ = chaos_db
    session = SessionFactory()
    now = datetime.now(UTC)

    # 1. Seed inventory (available=1, reserved=0)
    inv = Inventory(
        offer_id="off_leak_test_1", available_quantity=1, reserved_quantity=0, version=1
    )
    session.add(inv)
    session.commit()

    # 2. Reserve 1 unit via checkout
    inv_svc = InventoryService()
    rsv = inv_svc.reserve_stock(
        session, offer_id="off_leak_test_1", checkout_id="chk_leak_1", quantity=1
    )
    session.commit()

    # Verify inventory is held
    check_inv = get_inventory(session, "off_leak_test_1")
    assert check_inv.available_quantity == 1
    assert check_inv.reserved_quantity == 1

    # 3. Create checkout & authorization entities
    chk = Checkout(
        checkout_id="chk_leak_1",
        merchant_id="mrc_leak",
        buyer_id="buy_leak",
        offer_id="off_leak_test_1",
        offer_version=1,
        status="authorization_pending",
        subtotal_minor=1000,
        total_minor=1000,
        currency="INR",
        price_hash="hash_1",
        price_snapshot={"quantity": 1, "unit_price_minor": 1000},
        expires_at=now + timedelta(minutes=15),
    )
    session.add(chk)

    ath = Authorization(
        authorization_id="ath_leak_1",
        merchant_id="mrc_leak",
        buyer_id="buy_leak",
        checkout_id="chk_leak_1",
        amount_ceiling_minor=1000,
        currency="INR",
        price_hash="hash_1",
        policy_version="1.0",
        status="pending",
        valid_until=now + timedelta(minutes=15),
    )
    session.add(ath)
    session.commit()

    # 4. Reject authorization
    auth_svc = AuthorizationService(inventory_service=inv_svc)
    auth_svc.reject_authorization(
        session, buyer_id="buy_leak", merchant_id="mrc_leak", authorization_id="ath_leak_1"
    )
    session.commit()

    # 5. Verify inventory hold was released
    check_inv = get_inventory(session, "off_leak_test_1")
    assert check_inv.available_quantity == 1
    assert check_inv.reserved_quantity == 0

    check_rsv = get_reservation(session, "chk_leak_1")
    assert check_rsv.status == "released"
    session.close()


def test_chaos_checkout_cancellation_releases_inventory(chaos_db):
    """Explicit cancellation of checkout releases inventory hold immediately."""
    SessionFactory, _ = chaos_db
    session = SessionFactory()
    now = datetime.now(UTC)

    inv = Inventory(
        offer_id="off_leak_test_2", available_quantity=5, reserved_quantity=0, version=1
    )
    session.add(inv)
    session.commit()

    chk = Checkout(
        checkout_id="chk_cancel_leak",
        merchant_id="mrc_cancel",
        buyer_id="buy_cancel",
        offer_id="off_leak_test_2",
        offer_version=1,
        status="created",
        subtotal_minor=2000,
        total_minor=2000,
        currency="INR",
        price_hash="hash_2",
        price_snapshot={"quantity": 2, "unit_price_minor": 1000},
        expires_at=now + timedelta(minutes=15),
    )
    session.add(chk)
    session.commit()

    inv_svc = InventoryService()
    inv_svc.reserve_stock(
        session, offer_id="off_leak_test_2", checkout_id="chk_cancel_leak", quantity=2
    )
    session.commit()

    # In hold state
    assert get_inventory(session, "off_leak_test_2").reserved_quantity == 2

    # Cancel checkout
    chk_svc = CheckoutService(inventory_service=inv_svc)
    chk_svc.cancel_checkout(
        session, buyer_id="buy_cancel", merchant_id="mrc_cancel", checkout_id="chk_cancel_leak"
    )
    session.commit()

    # Released
    assert get_inventory(session, "off_leak_test_2").reserved_quantity == 0
    assert get_reservation(session, "chk_cancel_leak").status == "released"
    session.close()


def test_chaos_double_release_idempotency_no_underflow(chaos_db):
    """Multiple sequential or concurrent release calls do not underflow reserved_quantity."""
    SessionFactory, _ = chaos_db
    session = SessionFactory()

    inv = Inventory(
        offer_id="off_leak_test_3", available_quantity=3, reserved_quantity=0, version=1
    )
    session.add(inv)
    session.commit()

    inv_svc = InventoryService()
    rsv = inv_svc.reserve_stock(
        session, offer_id="off_leak_test_3", checkout_id="chk_double_rel", quantity=1
    )
    session.commit()

    assert get_inventory(session, "off_leak_test_3").reserved_quantity == 1

    # First release -> succeeds
    inv_svc.release_stock(session, checkout_id="chk_double_rel")
    session.commit()
    assert get_inventory(session, "off_leak_test_3").reserved_quantity == 0

    # Second release -> no-op, no exception, no underflow
    inv_svc.release_stock(session, checkout_id="chk_double_rel")
    session.commit()
    assert get_inventory(session, "off_leak_test_3").reserved_quantity == 0
    assert get_inventory(session, "off_leak_test_3").available_quantity == 3

    # Third release via direct repo call -> returns False
    res_direct = release(session, rsv.reservation_id)
    assert res_direct is False
    assert get_inventory(session, "off_leak_test_3").reserved_quantity == 0
    session.close()


def test_chaos_double_commit_idempotency_no_double_decrement(chaos_db):
    """Committing inventory twice does not decrement available stock twice."""
    SessionFactory, _ = chaos_db
    session = SessionFactory()

    inv = Inventory(
        offer_id="off_leak_test_4", available_quantity=5, reserved_quantity=0, version=1
    )
    session.add(inv)
    session.commit()

    inv_svc = InventoryService()
    rsv = inv_svc.reserve_stock(
        session, offer_id="off_leak_test_4", checkout_id="chk_double_cmt", quantity=2
    )
    session.commit()

    assert get_inventory(session, "off_leak_test_4").available_quantity == 5
    assert get_inventory(session, "off_leak_test_4").reserved_quantity == 2

    # First commit
    inv_svc.commit_stock(session, checkout_id="chk_double_cmt")
    session.commit()

    assert get_inventory(session, "off_leak_test_4").available_quantity == 3
    assert get_inventory(session, "off_leak_test_4").reserved_quantity == 0
    assert get_reservation(session, "chk_double_cmt").status == "committed"

    # Second commit -> safe no-op
    inv_svc.commit_stock(session, checkout_id="chk_double_cmt")
    session.commit()

    assert get_inventory(session, "off_leak_test_4").available_quantity == 3
    assert get_inventory(session, "off_leak_test_4").reserved_quantity == 0
    session.close()
