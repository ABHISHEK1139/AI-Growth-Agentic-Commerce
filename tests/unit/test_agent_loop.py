"""Unit tests for Model Gateway and Bounded Agent Tool Loop (Tasks 28 & 30 / Tasks 32 & 33).

Every test here runs the loop with no database, no session, and no transaction.
The loop's only commerce dependency is the facade Protocol, so a plain in-memory
double is enough to exercise it.
"""

from __future__ import annotations

import pytest

from packages.commerce import CommerceFacade
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.agent import loop as loop_module
from services.agent.loop import AgentLoopRunner
from services.agent.model import MockModelProvider
from tests.fake_commerce import FakeCommerceFacade, make_offer


def test_mock_model_provider_lifecycle():
    provider = MockModelProvider(model_version="mock-v1")
    res = provider.generate("I want a laptop under 50k")
    assert res.model_version == "mock-v1"
    assert res.parsed_json is not None
    assert res.parsed_json["category"] == "laptop"

    # Behavior injection
    provider.set_behavior("timeout")
    with pytest.raises(DomainError) as exc_info:
        provider.generate("test")
    assert exc_info.value.code == ErrorCode.GATEWAY_TIMEOUT


def test_fake_facade_satisfies_the_commerce_protocol():
    assert isinstance(FakeCommerceFacade(), CommerceFacade)


def test_agent_tool_confirmation_gate():
    commerce = FakeCommerceFacade()
    runner = AgentLoopRunner(commerce)

    # Mutating tool without confirmation
    res = runner.execute_tool(
        tool_name="create_checkout",
        arguments={"offer_id": "off_1", "quantity": 1},
        merchant_id="merch_1",
        buyer_id="buy_1",
        confirmed=False,
    )
    assert res.is_state_changing is True
    assert res.requires_confirmation is True
    assert res.result["status"] == "confirmation_required"
    # The gate must hold before the commerce call, not after it.
    assert "create_checkout" not in commerce.call_names


def test_agent_tool_reaches_commerce_once_confirmed():
    commerce = FakeCommerceFacade()
    runner = AgentLoopRunner(commerce)

    res = runner.execute_tool(
        tool_name="create_checkout",
        arguments={"offer_id": "off_1", "quantity": 2},
        merchant_id="merch_1",
        buyer_id="buy_1",
        confirmed=True,
    )
    assert res.requires_confirmation is False
    assert res.result["checkout"]["checkout_id"] == "chk_1"
    assert (
        "create_checkout",
        {
            "buyer_id": "buy_1",
            "merchant_id": "merch_1",
            "offer_id": "off_1",
            "quantity": 2,
        },
    ) in commerce.calls


def test_agent_loop_bounded_execution():
    commerce = FakeCommerceFacade(offers=[make_offer("off_1"), make_offer("off_2")])
    runner = AgentLoopRunner(commerce)

    summary = runner.run_bounded_agent(
        user_prompt="I need a laptop under 80,000 INR with 16GB RAM.",
        merchant_id="merch_1",
        buyer_id="buy_1",
    )
    assert summary.is_completed is True
    assert summary.steps_executed > 0
    assert len(summary.tool_calls) > 0
    assert summary.tool_calls[0].tool_name == "search_products"
    assert summary.tool_calls[0].result["count"] == 2

    # The run is auditable: prompt safety and intent extraction are recorded
    assert [event["event_type"] for event in commerce.events] == [
        "PROMPT_SAFETY_CHECKED",
        "INTENT_EXTRACTED",
    ]
    intent_event = next(e for e in commerce.events if e["event_type"] == "INTENT_EXTRACTED")
    assert intent_event["model_version"] == "mock-model-v1"
    assert intent_event["aggregate_id"] == summary.run_id


def test_agent_loop_stops_at_the_step_limit(monkeypatch: pytest.MonkeyPatch):
    """The step bound is checked before a tool runs, so a exhausted budget executes nothing.

    Driving the limit down rather than up is what makes this assertion sharp: the
    loop currently plans a single step, so a limit of zero is the only way to
    observe the bound being enforced rather than merely never reached.
    """
    monkeypatch.setattr(loop_module, "MAX_STEPS", 0)
    commerce = FakeCommerceFacade()
    runner = AgentLoopRunner(commerce)

    summary = runner.run_bounded_agent(
        user_prompt="I need a laptop under 80,000 INR.",
        merchant_id="merch_1",
        buyer_id="buy_1",
    )

    assert summary.steps_executed == 0
    assert summary.tool_calls == []
    # No tool ran, so no commerce read happened either.
    assert "search_offers" not in commerce.call_names
    # Prompt safety and intent extraction still happened and are still audited.
    assert [event["event_type"] for event in commerce.events] == [
        "PROMPT_SAFETY_CHECKED",
        "INTENT_EXTRACTED",
    ]


def test_agent_loop_stops_at_the_wall_clock_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop_module, "MAX_WALL_CLOCK_SECONDS", 0.0)
    commerce = FakeCommerceFacade()
    runner = AgentLoopRunner(commerce)

    summary = runner.run_bounded_agent(
        user_prompt="I need a laptop under 80,000 INR.",
        merchant_id="merch_1",
        buyer_id="buy_1",
    )

    assert summary.steps_executed == 0
    assert summary.tool_calls == []


def test_non_allowlisted_tool_is_blocked_before_any_commerce_call():
    commerce = FakeCommerceFacade()
    runner = AgentLoopRunner(commerce)

    with pytest.raises(DomainError) as exc_info:
        runner.execute_tool(
            tool_name="drop_all_tables",
            arguments={},
            merchant_id="merch_1",
            buyer_id="buy_1",
        )

    assert exc_info.value.code == ErrorCode.TOOL_BLOCKED
    assert commerce.calls == []
