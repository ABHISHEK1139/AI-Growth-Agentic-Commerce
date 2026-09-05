"""A failed facade call leaves no state row and no audit event (Requirement 8.5, 9.4).

This needs PostgreSQL, so it is marked ``integration`` and excluded from the
default run. The unit-level companion in ``tests/unit/test_commerce_facade.py``
proves the facade issues ``rollback`` and never ``commit`` on failure; this one
proves the database agrees, which is the claim that actually matters -- the audit
ledger is append-only, so an event surviving a rolled-back state change would be
permanent and wrong.

The stub service writes a real state row and a real audit event on the facade's
session, samples both counts *inside* the transaction, and then fails. Sampling
inside is what makes the assertion sharp: it shows the rows existed and were
discarded, rather than never having been written. It also leaves nothing to clean
up, which matters because a ``DELETE`` on ``audit_event`` is rejected by the
append-only trigger.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.api.commerce import SessionScopedCommerceFacade
from apps.api.db import get_session_factory
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import CheckoutV1
from services.audit.repository import append_event

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory() -> Iterator[Any]:
    factory = get_session_factory()
    probe: Session = factory()
    try:
        probe.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not reachable; start the compose stack for this test")
    finally:
        probe.close()
    yield factory


def _counts(session: Session, merchant_id: str, aggregate_id: str) -> tuple[int, int]:
    merchants = session.execute(
        text("SELECT count(*) FROM merchant WHERE merchant_id = :merchant_id"),
        {"merchant_id": merchant_id},
    ).scalar_one()
    events = session.execute(
        text("SELECT count(*) FROM audit_event WHERE aggregate_id = :aggregate_id"),
        {"aggregate_id": aggregate_id},
    ).scalar_one()
    return int(merchants), int(events)


class FailingCheckoutService:
    """Writes an aggregate row and its audit event, samples both, then fails."""

    def __init__(self, merchant_id: str, aggregate_id: str) -> None:
        self.merchant_id = merchant_id
        self.aggregate_id = aggregate_id
        self.counts_inside_transaction: tuple[int, int] | None = None

    def create_checkout(
        self,
        session: Session,
        *,
        buyer_id: str,
        merchant_id: str,
        offer_id: str,
        quantity: int = 1,
        ttl_minutes: int = 15,
        now: Any = None,
    ) -> CheckoutV1:
        ts = (now or datetime.now(UTC)).isoformat()
        session.execute(
            text(
                "INSERT INTO merchant (merchant_id, name, status, created_at) "
                "VALUES (:merchant_id, :name, 'active', :created_at)"
            ),
            {"merchant_id": self.merchant_id, "name": "Atomicity probe", "created_at": ts},
        )
        append_event(
            session,
            event_type="CHECKOUT_CREATED",
            aggregate_type="checkout",
            aggregate_id=self.aggregate_id,
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=self.merchant_id,
            amount_minor=6_500_000,
            metadata={"offer_id": offer_id, "quantity": quantity},
        )
        session.flush()
        self.counts_inside_transaction = _counts(session, self.merchant_id, self.aggregate_id)
        raise DomainError(
            "The requested quantity is not available.",
            code=ErrorCode.INVENTORY_UNAVAILABLE,
        )


def test_a_failed_facade_call_persists_neither_the_state_row_nor_its_audit_event(
    session_factory: Any,
) -> None:
    suffix = uuid.uuid4().hex[:12]
    merchant_id = f"mrc_probe_{suffix}"
    aggregate_id = f"chk_probe_{suffix}"

    checkout_service = FailingCheckoutService(merchant_id, aggregate_id)
    facade = SessionScopedCommerceFacade(session_factory, checkout_service=checkout_service)

    with pytest.raises(DomainError) as exc_info:
        facade.create_checkout(buyer_id="buy_probe", merchant_id=merchant_id, offer_id="off_probe")
    assert exc_info.value.code == ErrorCode.INVENTORY_UNAVAILABLE

    # Both writes really happened inside the unit of work ...
    assert checkout_service.counts_inside_transaction == (1, 1)

    # ... and neither survived it.
    verification: Session = session_factory()
    try:
        assert _counts(verification, merchant_id, aggregate_id) == (0, 0)
    finally:
        verification.close()
