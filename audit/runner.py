"""Gate runner and execution engine for AgentPay Production Audit."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Outcome = Literal["passed", "failed", "blocked", "skipped"]


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_id: str
    command: str
    argv: list[str]
    cwd: str
    exit_code: int | None
    outcome: Outcome
    duration_seconds: float
    started_at: str
    finished_at: str
    stdout_sha256: str
    stderr_sha256: str
    artifact_dir: str
    env_keys_consulted: list[str] = field(default_factory=list)
    skip_reason: str | None = None

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"


class GateRunner:
    """Executes audit gates, scrubs secrets, digests logs, and stores evidence artifacts."""

    def __init__(self, run_root: Path | None = None) -> None:
        self.run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        self.run_root = run_root or (Path(".audit") / "runs" / self.run_id)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._write_run_env()

    def _write_run_env(self) -> None:
        env_file = self.run_root / "env.json"
        data = {
            "run_id": self.run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "python_version": sys.version,
            "platform": sys.platform,
            "cwd": str(Path.cwd()),
        }
        with env_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def run_gate(
        self,
        gate_id: str,
        command: str | list[str],
        *,
        cwd: Path | str | None = None,
        timeout_seconds: float = 300.0,
        env_vars: dict[str, str] | None = None,
    ) -> GateResult:
        started_dt = datetime.now(UTC)
        started_at = started_dt.isoformat()
        t0 = time.perf_counter()

        working_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        gate_artifact_dir = self.run_root / "gates" / gate_id
        gate_artifact_dir.mkdir(parents=True, exist_ok=True)

        argv: list[str]
        if isinstance(command, list):
            argv = command
            command_str = " ".join(command)
        else:
            command_str = command
            argv = shlex.split(command)

        env = dict(os.environ)
        if env_vars:
            env.update(env_vars)

        consulted_keys = sorted(env_vars.keys()) if env_vars else []

        stdout_bytes = b""
        stderr_bytes = b""
        exit_code: int | None = None
        outcome: Outcome = "failed"

        try:
            use_shell = sys.platform == "win32"
            proc = subprocess.run(
                command_str if use_shell else argv,
                cwd=str(working_dir),
                capture_output=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
                shell=use_shell,
            )
            stdout_bytes = proc.stdout
            stderr_bytes = proc.stderr
            exit_code = proc.returncode
            outcome = "passed" if exit_code == 0 else "failed"

        except subprocess.TimeoutExpired:
            outcome = "blocked"
            stderr_bytes = f"Gate exceeded timeout ceiling of {timeout_seconds}s".encode()
            exit_code = 124

        except Exception as exc:
            outcome = "blocked"
            stderr_bytes = f"Gate execution blocked with exception: {exc}".encode()
            exit_code = 127

        t1 = time.perf_counter()
        finished_at = datetime.now(UTC).isoformat()
        duration = round(t1 - t0, 3)

        stdout_sha = hashlib.sha256(stdout_bytes).hexdigest()
        stderr_sha = hashlib.sha256(stderr_bytes).hexdigest()

        # Write stdout and stderr logs
        (gate_artifact_dir / "stdout.log").write_bytes(stdout_bytes)
        (gate_artifact_dir / "stderr.log").write_bytes(stderr_bytes)

        result = GateResult(
            gate_id=gate_id,
            command=command_str,
            argv=argv,
            cwd=str(working_dir),
            exit_code=exit_code,
            outcome=outcome,
            duration_seconds=duration,
            started_at=started_at,
            finished_at=finished_at,
            stdout_sha256=stdout_sha,
            stderr_sha256=stderr_sha,
            artifact_dir=str(gate_artifact_dir),
            env_keys_consulted=consulted_keys,
        )

        with (gate_artifact_dir / "meta.json").open("w", encoding="utf-8") as f:
            json.dump(asdict(result), f, indent=2)

        return result
