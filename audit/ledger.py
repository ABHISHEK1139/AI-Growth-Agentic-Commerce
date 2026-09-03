"""Defect ledger module for AgentPay Production Audit."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Severity = Literal["critical", "high", "medium", "low"]
Status = Literal["open", "fixed", "deferred", "not-a-defect"]


@dataclass
class DefectEntry:
    id: str
    kind: Literal["defect", "blocker"]
    title: str
    severity: Severity
    area: str
    location: str
    reproduction: dict[str, str]
    observed: str
    expected: str
    status: Status
    gateway_criterion: str | None = None
    audit_criterion: str | None = None
    confirmation_artifact: str | None = None
    fix_artifact: str | None = None
    regression_test: str | None = None
    pre_fix_revision: str | None = None
    deferral_rationale: str | None = None
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class DefectLedger:
    """Monotonically increments and maintains the JSONL defect ledger."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path("audit") / "records" / "defect-ledger.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[DefectEntry] = self._load()

    def _load(self) -> list[DefectEntry]:
        if not self.path.exists():
            return []
        entries: list[DefectEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    data = json.loads(stripped)
                    entries.append(DefectEntry(**data))
        return entries

    def _next_id(self) -> str:
        count = len(self.entries) + 1
        return f"APD-{count:04d}"

    def record_defect(
        self,
        *,
        title: str,
        severity: Severity,
        area: str,
        location: str,
        reproduction: dict[str, str],
        observed: str,
        expected: str,
        status: Status = "open",
        kind: Literal["defect", "blocker"] = "defect",
        gateway_criterion: str | None = None,
        audit_criterion: str | None = None,
        deferral_rationale: str | None = None,
    ) -> DefectEntry:
        entry_id = self._next_id()
        entry = DefectEntry(
            id=entry_id,
            kind=kind,
            title=title,
            severity=severity,
            area=area,
            location=location,
            reproduction=reproduction,
            observed=observed,
            expected=expected,
            status=status,
            gateway_criterion=gateway_criterion,
            audit_criterion=audit_criterion,
            deferral_rationale=deferral_rationale,
        )
        self.entries.append(entry)
        self._save()
        return entry

    def mark_fixed(
        self,
        defect_id: str,
        *,
        fix_artifact: str,
        regression_test: str,
    ) -> DefectEntry:
        for e in self.entries:
            if e.id == defect_id:
                e.status = "fixed"
                e.fix_artifact = fix_artifact
                e.regression_test = regression_test
                e.updated_at = datetime.now(UTC).isoformat()
                self._save()
                return e
        raise KeyError(f"Defect {defect_id} not found")

    def _save(self) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps(asdict(e)) + "\n")
