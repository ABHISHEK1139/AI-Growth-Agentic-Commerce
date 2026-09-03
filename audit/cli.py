"""Command line interface for AgentPay Production Audit."""

from __future__ import annotations

import argparse
import sys

from audit.gates import ALL_GATES
from audit.ledger import DefectLedger
from audit.manifest import build_coverage_manifest, write_manifest_records
from audit.runner import GateRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentPay Production Audit & Gate Runner")
    parser.add_argument(
        "gate", nargs="?", help="Specific gate ID to run (e.g. TEST-UNIT, STATIC-LINT)"
    )
    parser.add_argument("--all", action="store_true", help="Run all defined audit gates")
    parser.add_argument("--manifest", action="store_true", help="Build and dump coverage manifest")
    parser.add_argument("--ledger", action="store_true", help="Print defect ledger status summary")

    args = parser.parse_args()

    if args.manifest:
        entries = build_coverage_manifest()
        out = write_manifest_records(entries)
        print(f"[OK] Coverage manifest written to {out} ({len(entries)} files tracked)")
        return 0

    if args.ledger:
        ledger = DefectLedger()
        print(f"Defect Ledger has {len(ledger.entries)} entries:")
        for e in ledger.entries:
            print(f"  [{e.status.upper()}] {e.id}: {e.title} ({e.severity})")
        return 0

    runner = GateRunner()
    gates_to_run = (
        ALL_GATES
        if args.all
        else ([g for g in ALL_GATES if g.gate_id == args.gate] if args.gate else [])
    )

    if not gates_to_run:
        print("Please specify a valid gate ID or pass --all, --manifest, or --ledger.")
        print("Available gates:")
        for g in ALL_GATES:
            print(f"  - {g.gate_id}: {g.description}")
        return 1

    print(f"Starting audit run {runner.run_id} ({len(gates_to_run)} gates)...\n")
    failed_count = 0

    for g in gates_to_run:
        print(f"[RUN] {g.gate_id} ({g.description})...", end=" ", flush=True)
        res = runner.run_gate(
            gate_id=g.gate_id,
            command=g.command,
            cwd=g.cwd,
            timeout_seconds=g.timeout_seconds,
        )
        if res.passed:
            print(f"[PASSED] ({res.duration_seconds}s)")
        else:
            print(f"[{res.outcome.upper()}] (exit code {res.exit_code}, {res.duration_seconds}s)")
            failed_count += 1

    print(f"\nAudit complete. Artifacts saved in: {runner.run_root}")
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
