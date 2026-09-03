"""Unit tests for the production audit harness library (Phase A & Task 1)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from audit.ledger import DefectLedger
from audit.manifest import build_coverage_manifest
from audit.redaction import is_secret_like, scrub
from audit.runner import GateRunner
from services.agent.guard import PromptSafetyClassifier


def test_gate_runner_passes_successful_command():
    with tempfile.TemporaryDirectory() as tmp:
        runner = GateRunner(run_root=Path(tmp))
        res = runner.run_gate("TEST-ECHO", "python -c \"print('audit test')\"")
        assert res.passed is True
        assert res.outcome == "passed"
        assert res.exit_code == 0
        assert (Path(res.artifact_dir) / "meta.json").exists()
        assert (Path(res.artifact_dir) / "stdout.log").exists()


def test_gate_runner_handles_failed_command():
    with tempfile.TemporaryDirectory() as tmp:
        runner = GateRunner(run_root=Path(tmp))
        res = runner.run_gate("TEST-FAIL", 'python -c "import sys; sys.exit(1)"')
        assert res.passed is False
        assert res.outcome == "failed"
        assert res.exit_code == 1


def test_defect_ledger_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "ledger.jsonl"
        ledger = DefectLedger(ledger_path)
        assert len(ledger.entries) == 0

        # Record defect
        entry = ledger.record_defect(
            title="Layer 2 fail-open defect",
            severity="high",
            area="services/agent/guard.py",
            location="services/agent/guard.py:145",
            reproduction={"command": "pytest tests/unit/test_prompt_guard.py"},
            observed="is_safe=True returned on error",
            expected="is_safe=False returned on error",
        )
        assert entry.id == "APD-0001"
        assert entry.status == "open"
        assert len(ledger.entries) == 1

        # Mark fixed
        fixed = ledger.mark_fixed(
            "APD-0001",
            fix_artifact="services/agent/guard.py",
            regression_test="test_guard_fail_closed",
        )
        assert fixed.status == "fixed"


def test_redaction_engine_scrubs_credentials():
    secret_text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and rzp_test_1234567890ABCD"
    assert is_secret_like(secret_text) is True
    cleaned = scrub(secret_text)
    assert "Bearer eyJ" not in cleaned
    assert "rzp_test_" not in cleaned
    assert "[REDACTED]" in cleaned

    # Hash should NOT be scrubbed
    sample_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert is_secret_like(sample_sha) is False


def test_coverage_manifest_discovery():
    manifest = build_coverage_manifest()
    assert len(manifest) > 50
    paths = {e.path for e in manifest}
    assert any("services/agent/guard.py" in p for p in paths)
    assert any("README.md" in p for p in paths)


def test_guard_fails_closed_on_error():
    # Simulate network exception in Layer 2 Meta Llama Guard
    with patch("httpx.Client.post", side_effect=Exception("Connection refused")):
        assessment = PromptSafetyClassifier.evaluate_meta_llama_guard(
            "Hello world",
            api_key="fake-key-12345",
        )
        assert assessment.is_safe is False
        assert assessment.threat_category == "GUARD_TRANSPORT_ERROR"
        assert "failclosed" in assessment.evaluator
