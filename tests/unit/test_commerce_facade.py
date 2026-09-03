"""Transaction scope of the tool-facing commerce facade (Task 30, Requirement 8.5, 23.1).

The facade is the only holder of a session on the agent's path to commerce, so the
properties worth pinning are about the unit of work: one per call, committed on
success, rolled back whole on failure, and never handed outward.

These tests need no database. The session is a recording double, which is exactly
what lets them assert the *ordering* of commit, rollback, and close -- something a
real connection would hide.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from apps.api.commerce import SessionScopedCommerceFacade, get_commerce_facade
from packages.commerce import CommerceFacade
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import AuthorizationV1, CheckoutV1, OfferV1, PaymentV1
from services.audit.repository import append_event
from tests.fake_commerce import (
    make_authorization,
    make_checkout,
    make_offer,
    make_payment,
)

DATABASE_TYPE = re.compile(
    r"\b(Session|sessionmaker|scoped_session|Connection|Engine|Result|Row)\b"
)


class RecordingSession:
    """Enough of a session to observe how the unit of work is driven."""

    def __init__(self) -> None:
        self.lifecycle: list[str] = []
        self.statements: list[tuple[str, Any]] = []

    def execute(self, statement: Any, params: Any = None) -> None:
        self.statements.append((str(statement), params))

    def commit(self) -> None:
        self.lifecycle.append("commit")

    def rollback(self) -> None:
        self.lifecycle.append("rollback")

    def close(self) -> None:
        self.lifecycle.append("close")


class RecordingCheckoutService:
    """Mirrors the real service's shape: audit event and state change, one session."""

    def __init__(self, *, fail_after_audit: bool = False) -> None:
        self.fail_after_audit = fail_after_audit
        self.sessions: list[Any] = []

    def create_checkout(
        self,
        session: Any,
        *,
        buyer_id: str,
        merchant_id: str,
        offer_id: str,
        quantity: int = 1,
        ttl_minutes: int = 15,
        now: Any = None,
    ) -> CheckoutV1:
        self.sessions.append(session)
        append_event(
            session,
            event_type="CHECKOUT_CREATED",
            aggregate_type="checkout",
            aggregate_id="chk_1",
            actor_type="buyer",
            actor_id=buyer_id,
            merchant_id=merchant_id,
            amount_minor=6_500_000,
            metadata={"offer_id": offer_id, "quantity": quantity},
        )
        if self.fail_after_audit:
            raise DomainError(
                "The requested quantity is not available.",
                code=ErrorCode.INVENTORY_UNAVAILABLE,
            )
        return make_checkout(buyer_id=buyer_id, merchant_id=merchant_id, quantity=quantity)


class StubOfferService:
    def __init__(self) -> None:
        self.sessions: list[Any] = []

    def search_offers(self, session: Any, **kwargs: Any) -> list[OfferV1]:
        self.sessions.append(session)
        return [make_offer()]

    def get_offer_by_id(self, session: Any, *, merchant_id: str, offer_id: str) -> OfferV1:
        self.sessions.append(session)
        return make_offer(offer_id=offer_id, merchant_id=merchant_id)


class StubAuthorizationService:
    def request_authorization(self, session: Any, **kwargs: Any) -> AuthorizationV1:
        return make_authorization()


class StubPaymentService:
    def create_payment(self, session: Any, **kwargs: Any) -> PaymentV1:
        return make_payment()


def build_facade(
    session: RecordingSession,
    **overrides: Any,
) -> SessionScopedCommerceFacade:
    defaults: dict[str, Any] = {
        "offer_service": StubOfferService(),
        "checkout_service": RecordingCheckoutService(),
        "authorization_service": StubAuthorizationService(),
        "payment_service": StubPaymentService(),
    }
    defaults.update(overrides)
    return SessionScopedCommerceFacade(lambda: session, **defaults)


# ---------------------------------------------------------------------------
# The port keeps persistence out of its own vocabulary
# ---------------------------------------------------------------------------


def test_the_port_exposes_no_database_type_in_any_signature():
    offenders = []
    for name, member in inspect.getmembers(CommerceFacade, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        signature = inspect.signature(member)
        rendered = [str(parameter.annotation) for parameter in signature.parameters.values()] + [
            str(signature.return_annotation)
        ]
        offenders.extend(f"{name}: {text}" for text in rendered if DATABASE_TYPE.search(text))
    assert offenders == []


def test_the_implementation_satisfies_the_port():
    assert isinstance(build_facade(RecordingSession()), CommerceFacade)


def test_the_default_facade_is_constructible_without_a_database():
    # Constructing must not open a connection; the factory is resolved per call.
    assert isinstance(get_commerce_facade(), CommerceFacade)


# ---------------------------------------------------------------------------
# One unit of work per call
# ---------------------------------------------------------------------------


def test_a_read_commits_and_closes_exactly_once():
    session = RecordingSession()
    facade = build_facade(session)

    offers = facade.search_offers(merchant_id="merch_1", limit=5)

    assert [offer.offer_id for offer in offers] == ["off_1"]
    assert session.lifecycle == ["commit", "close"]


def test_a_comparison_reads_every_offer_in_one_unit_of_work():
    session = RecordingSession()
    offer_service = StubOfferService()
    facade = build_facade(session, offer_service=offer_service)

    offers = facade.compare_offers(merchant_id="merch_1", offer_ids=["off_1", "off_2"])

    assert [offer.offer_id for offer in offers] == ["off_1", "off_2"]
    # Same session for both reads, and a single commit for the pair.
    assert offer_service.sessions == [session, session]
    assert session.lifecycle == ["commit", "close"]


def test_a_state_change_and_its_audit_event_share_one_transaction():
    session = RecordingSession()
    checkout_service = RecordingCheckoutService()
    facade = build_facade(session, checkout_service=checkout_service)

    facade.create_checkout(buyer_id="buy_1", merchant_id="merch_1", offer_id="off_1")

    audit_inserts = [
        statement for statement, _ in session.statements if "INSERT INTO audit_event" in statement
    ]
    assert len(audit_inserts) == 1
    # The service wrote its audit event on the very session the facade commits,
    # so the state change and the event cannot land in different transactions.
    assert checkout_service.sessions == [session]
    assert session.lifecycle == ["commit", "close"]


def test_an_agent_event_is_its_own_unit_of_work():
    session = RecordingSession()
    facade = build_facade(session)

    event_id = facade.record_agent_event(
        event_type="INTENT_EXTRACTED",
        aggregate_id="run_1",
        actor_type="buyer",
        actor_id="buy_1",
        merchant_id="merch_1",
        model_version="mock-model-v1",
        metadata={"query": "laptop"},
    )

    assert event_id.startswith("aud_")
    assert session.lifecycle == ["commit", "close"]


# ---------------------------------------------------------------------------
# A failure discards the whole unit of work
# ---------------------------------------------------------------------------


def test_a_failure_mid_transaction_rolls_back_and_never_commits():
    session = RecordingSession()
    checkout_service = RecordingCheckoutService(fail_after_audit=True)
    facade = build_facade(session, checkout_service=checkout_service)

    with pytest.raises(DomainError) as exc_info:
        facade.create_checkout(buyer_id="buy_1", merchant_id="merch_1", offer_id="off_1")

    assert exc_info.value.code == ErrorCode.INVENTORY_UNAVAILABLE
    # The audit insert was issued before the failure, and is discarded with the
    # state change rather than surviving it.
    assert any("INSERT INTO audit_event" in statement for statement, _ in session.statements)
    assert session.lifecycle == ["rollback", "close"]
    assert "commit" not in session.lifecycle


def test_the_session_never_escapes_the_facade():
    """No facade method returns anything holding the session."""
    session = RecordingSession()
    facade = build_facade(session)

    results = [
        facade.search_offers(merchant_id="merch_1"),
        facade.get_offer(merchant_id="merch_1", offer_id="off_1"),
        facade.compare_offers(merchant_id="merch_1", offer_ids=["off_1"]),
        facade.create_checkout(buyer_id="buy_1", merchant_id="merch_1", offer_id="off_1"),
        facade.request_authorization(buyer_id="buy_1", merchant_id="merch_1", checkout_id="chk_1"),
        facade.create_payment(
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
            authorization_id="ath_1",
        ),
    ]

    flattened: list[Any] = []
    for result in results:
        flattened.extend(result) if isinstance(result, list) else flattened.append(result)

    for value in flattened:
        assert value is not session
        assert not hasattr(value, "execute")
        # Every returned value is a versioned public contract, not an ORM entity.
        assert hasattr(value, "model_dump")
