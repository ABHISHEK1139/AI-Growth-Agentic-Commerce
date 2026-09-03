"""Correlation identifiers must survive async boundaries and worker handoff."""

from __future__ import annotations

import asyncio

import pytest

from packages.observability.context import (
    adopt,
    correlation_scope,
    current_ids,
    new_id,
    reset_ids,
    set_ids,
)


def test_ids_are_empty_by_default() -> None:
    assert current_ids().as_dict() == {}


def test_scope_sets_and_restores() -> None:
    with correlation_scope(request_id="req_1"):
        assert current_ids().request_id == "req_1"

    assert current_ids().request_id is None


def test_nested_scopes_shadow_then_restore() -> None:
    with correlation_scope(request_id="req_outer"):
        with correlation_scope(request_id="req_inner"):
            assert current_ids().request_id == "req_inner"
        assert current_ids().request_id == "req_outer"


def test_unknown_identifier_is_a_loud_error() -> None:
    """A typo'd identifier name would otherwise be invisible in production."""
    with pytest.raises(KeyError, match="reqest_id"):
        set_ids(reqest_id="req_1")


def test_prefixed_ids_are_unique_and_self_describing() -> None:
    first, second = new_id("chk"), new_id("chk")

    assert first != second
    assert first.startswith("chk_")


def test_ids_propagate_into_awaited_coroutines() -> None:
    async def scenario() -> str | None:
        async def inner() -> str | None:
            return current_ids().checkout_id

        with correlation_scope(checkout_id="chk_42"):
            return await inner()

    assert asyncio.run(scenario()) == "chk_42"


def test_a_worker_task_can_adopt_a_captured_snapshot() -> None:
    """The webhook/expiry-sweep case: work is scheduled by one request and
    executed later in a different context, and must still be correlated."""
    with correlation_scope(trace_id="trc_1", payment_id="pay_1"):
        captured = current_ids()

    assert current_ids().trace_id is None

    tokens = adopt(captured)
    try:
        assert current_ids().trace_id == "trc_1"
        assert current_ids().payment_id == "pay_1"
    finally:
        reset_ids(tokens)

    assert current_ids().trace_id is None
