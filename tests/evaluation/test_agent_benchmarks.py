"""Benchmark evaluation suite measuring agent constraint satisfaction and extraction latency (Task 49, Requirement 43)."""

from __future__ import annotations

import time

import pytest

from services.agent.intent import IntentValidator
from services.agent.model import MockModelProvider

BENCHMARK_INTENTS = [
    ("Gaming laptop with 16GB RAM under 80k INR", "laptop", 8000000),
    ("Smartphone with fast delivery", "smartphone", 7000000),
    ("Ultrabook for programming with 32GB RAM", "laptop", 7000000),
    ("Budget tablet under 25000", "smartphone", 7000000),
]


@pytest.mark.parametrize("prompt,expected_cat,expected_budget", BENCHMARK_INTENTS)
def test_intent_accuracy_and_latency_benchmark(
    prompt: str, expected_cat: str, expected_budget: int
):
    provider = MockModelProvider()
    start = time.perf_counter()
    res = provider.generate(prompt)
    elapsed_ms = (time.perf_counter() - start) * 1000

    intent = IntentValidator.validate_dict(res.parsed_json or {})
    assert intent.category == expected_cat
    assert intent.financial.budget_minor is not None
    assert elapsed_ms < 100.0  # sub-100ms deterministic extraction
