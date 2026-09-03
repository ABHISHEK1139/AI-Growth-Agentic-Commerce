"""Gate registry defining all audit gates and execution commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GateDefinition:
    gate_id: str
    description: str
    command: str
    cwd: str | None = None
    timeout_seconds: float = 300.0


ALL_GATES: list[GateDefinition] = [
    GateDefinition(
        gate_id="STATIC-LINT",
        description="Backend Ruff lint analysis",
        command="python -m ruff check .",
    ),
    GateDefinition(
        gate_id="STATIC-FORMAT",
        description="Backend Ruff formatting check",
        command="python -m ruff format --check .",
    ),
    GateDefinition(
        gate_id="STATIC-WEB-BUILD",
        description="Next.js production build and TypeScript verification",
        command="npm run build",
        cwd="apps/web",
        timeout_seconds=120.0,
    ),
    GateDefinition(
        gate_id="TEST-UNIT",
        description="Core unit test suite (1,000+ tests)",
        command="python -m pytest tests/unit/ -q",
        timeout_seconds=180.0,
    ),
    GateDefinition(
        gate_id="TEST-CONTRACT",
        description="External buyer agent contract verification",
        command="python -m pytest tests/contract/ -q",
        timeout_seconds=60.0,
    ),
    GateDefinition(
        gate_id="TEST-SECURITY",
        description="Security, token scopes, and tenant isolation tests",
        command="python -m pytest tests/security/ -q",
        timeout_seconds=60.0,
    ),
    GateDefinition(
        gate_id="TEST-EVAL",
        description="Adversarial evaluation & prompt injection tests",
        command="python -m pytest tests/evaluation/ -q",
        timeout_seconds=60.0,
    ),
    GateDefinition(
        gate_id="TEST-TRACK1-SCENARIOS",
        description="Track 1 Agentic Commerce 20 end-to-end scenarios",
        command="python -m pytest tests/integration/test_track1_agentic_commerce_20_scenarios.py -q",
        timeout_seconds=120.0,
    ),
    GateDefinition(
        gate_id="GUARD-FAILCLOSED",
        description="Meta Llama Guard 3 Layer 2 fail-closed security verification",
        command="python -m pytest tests/unit/test_meta_llama_guard.py tests/unit/test_agent_guardrails.py -q",
        timeout_seconds=60.0,
    ),
    GateDefinition(
        gate_id="GROWTH-CROSS-SELL",
        description="Revenue growth contextual cross-sell recommendation test suite",
        command="python -m pytest tests/unit/test_cross_sell.py -q",
        timeout_seconds=60.0,
    ),
    GateDefinition(
        gate_id="GROWTH-CAMPAIGNS",
        description="Merchant Campaign Orchestrator & policy gate verification",
        command="python -m pytest tests/unit/test_campaign_orchestrator.py -q",
        timeout_seconds=60.0,
    ),
]
