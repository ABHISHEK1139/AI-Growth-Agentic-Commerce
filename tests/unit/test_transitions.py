"""The transition table is executable data, not duplicated conditional logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.checkout.transitions import (
    RULES,
    TRANSITIONS,
    TransitionContext,
    TransitionEvent,
    transition,
)


@dataclass
class FakeAggregate:
    status: str
    aggregate_id: str = "chk_1"
    aggregate_type: str = "checkout"


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.commits = 0

    def execute(self, statement, parameters=None):  # noqa: ANN001, ANN201
        self.calls.append((statement, parameters))

    def commit(self) -> None:
        self.commits += 1


@pytest.fixture(autouse=True)
def stable_audit_id(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(
        "services.checkout.transitions.append_transition_event", lambda *_a, **_k: "aud_1"
    )


@pytest.mark.parametrize("rule", TRANSITIONS)
def test_every_declared_transition_is_reachable(rule):  # noqa: ANN001
    aggregate = FakeAggregate(rule.source)
    values = dict.fromkeys(rule.required_fields, 1)
    context = TransitionContext(
        actor_type=next(iter(rule.actors)),
        actor_id="actor_1",
        values=values,
        supplied_price_hash="hash",
        persisted_price_hash="hash",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = transition(aggregate, rule.event, context, FakeSession())

    assert result.status == rule.target
    assert aggregate.status == rule.target
    assert (rule.source, rule.event) in RULES


def test_terminal_states_reject_every_event_without_mutation():
    aggregate = FakeAggregate("COMPLETED")

    with pytest.raises(DomainError) as raised:
        transition(
            aggregate,
            TransitionEvent.COMPLETE_ORDER,
            TransitionContext("system", "sys_1"),
            FakeSession(),
        )

    assert raised.value.code is ErrorCode.ALREADY_FINALIZED
    assert aggregate.status == "COMPLETED"


def test_rejected_checks_are_inert_and_use_deterministic_codes():
    aggregate = FakeAggregate("AUTHORIZED")
    context = TransitionContext(
        "buyer", "buy_1", supplied_price_hash="old", persisted_price_hash="new"
    )

    with pytest.raises(DomainError) as raised:
        transition(aggregate, TransitionEvent.CREATE_PAYMENT, context, FakeSession())

    assert raised.value.code is ErrorCode.PRICE_CHANGED
    assert aggregate.status == "AUTHORIZED"


def test_transition_never_commits_the_callers_transaction():
    aggregate = FakeAggregate("DISCOVERED")
    session = FakeSession()

    transition(
        aggregate, TransitionEvent.SELECT_OFFER, TransitionContext("buyer", "buy_1"), session
    )

    assert aggregate.status == "OFFER_SELECTED"
    assert session.commits == 0
