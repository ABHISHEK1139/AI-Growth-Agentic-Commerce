#!/usr/bin/env python3
"""PostgreSQL backup script for the AgentPay commerce database.

Phase 9 acceptance: backup and restore procedure exists and is exercised in CI.

This script streams a compressed logical backup of the configured database
into a timestamped file under the destination directory. It is the
operator's single entry point for offline snapshots; CI uses the same
script to verify the procedure in the migration + integration job.

Usage:
    python -m infra.operations.backup --dest /var/backups/agentpay
    python -m infra.operations.backup --dest /tmp/snapshots --label nightly
    python -m infra.operations.backup --dest /tmp/snapshots --retention-days 14

The script reads ``DATABASE_URL`` from the environment, falling back to the
``pg_dump``-style ``PG*`` variables when set. The URL is parsed but never
logged: a database URL with a password must not appear in a backup runbook
log line.

Required environment variables (one of these forms):
    DATABASE_URL=postgresql+psycopg://user:password@host:port/dbname
    DATABASE_URL=postgresql://user:password@host:port/dbname
    PGHOST=... PGPORT=... PGUSER=... PGPASSWORD=... PGDATABASE=...
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


def _parse_database_url(url: str) -> dict[str, str]:
    """Parse a libpq-style URL into the variables ``pg_dump`` expects.

    The ``+psycopg`` SQLAlchemy driver suffix is stripped so the same URL
    that the application uses is acceptable here. Passwords are returned
    in the dict because they are needed to run the dump; they are not
    logged.
    """
    cleaned = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    parsed = urlparse(cleaned)
    return {
        "PGHOST": parsed.hostname or "localhost",
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": parsed.username or "agentpay",
        "PGPASSWORD": parsed.password or "",
        "PGDATABASE": (parsed.path or "/").lstrip("/") or "agentpay",
    }


def _resolve_env() -> dict[str, str]:
    """Read connection variables from the environment, preferring DATABASE_URL."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return _parse_database_url(url)
    # Fall back to the raw libpq variables. The operator's choice; either
    # form is fine.
    return {
        "PGHOST": os.environ.get("PGHOST", "localhost"),
        "PGPORT": os.environ.get("PGPORT", "5432"),
        "PGUSER": os.environ.get("PGUSER", "agentpay"),
        "PGPASSWORD": os.environ.get("PGPASSWORD", ""),
        "PGDATABASE": os.environ.get("PGDATABASE", "agentpay"),
    }


def _redact_url(url: str) -> str:
    """Return ``url`` with the password replaced by ``***`` for safe logging."""
    if not url:
        return "<unset>"
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


def backup_database(dest: Path, label: str = "manual") -> Path:
    """Write a compressed ``pg_dump`` snapshot to ``dest``.

    Returns the path to the produced file. Raises ``RuntimeError`` if
    ``pg_dump`` is not on the PATH or returns non-zero.
    """
    if not dest.is_dir():
        raise FileNotFoundError(f"destination directory does not exist: {dest}")
    if shutil.which("pg_dump") is None:
        raise RuntimeError(
            "pg_dump is not installed. Install postgresql-client (apt: postgresql-client, brew: libpq)."
        )

    env = os.environ.copy()
    pg_vars = _resolve_env()
    env.update(pg_vars)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:48] or "manual"
    out_file = dest / f"agentpay_{safe_label}_{timestamp}.sql.gz"

    # We deliberately do not pass --no-password and we do set PGPASSWORD via
    # the environment; pg_dump's own password-prompt behaviour is unhelpful
    # in a non-interactive script.
    cmd = [
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "--format=plain",
        "--no-password",
    ]
    print(
        f"[backup] starting at {timestamp}: connecting to "
        f"{_redact_url(os.environ.get('DATABASE_URL', ''))} -> {out_file.name}",
        file=sys.stderr,
    )

    with out_file.open("wb") as out_fp:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        # Stream through gzip so even a multi-GB database does not need to
        # be held entirely in memory.
        import gzip

        with gzip.GzipFile(fileobj=out_fp, mode="wb", compresslevel=6) as gz:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                gz.write(chunk)
        rc = proc.wait()
        if rc != 0:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            out_file.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump exited with code {rc}: {err.strip()}")
    size_mb = out_file.stat().st_size / (1024 * 1024)
    print(f"[backup] wrote {out_file} ({size_mb:.1f} MB)", file=sys.stderr)
    return out_file


def prune_old_backups(dest: Path, retention_days: int) -> int:
    """Delete ``*.sql.gz`` files older than ``retention_days``.

    Returns the number of files removed. Skips files whose name does not
    match the expected pattern so a manual archive is safe to keep.
    """
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC).timestamp() - (retention_days * 24 * 3600)
    pattern = re.compile(r"^agentpay_[A-Za-z0-9._-]+_\d{8}T\d{6}Z\.sql\.gz$")
    removed = 0
    for path in dest.iterdir():
        if not path.is_file() or not pattern.match(path.name):
            continue
        if path.stat().st_mtime < cutoff:
            print(f"[backup] pruning old snapshot {path.name}", file=sys.stderr)
            path.unlink()
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Take a compressed PostgreSQL backup of the AgentPay database."
    )
    parser.add_argument(
        "--dest",
        type=Path,
        required=True,
        help="Directory to write the snapshot file into (must exist).",
    )
    parser.add_argument(
        "--label",
        default="manual",
        help="Short label included in the snapshot filename (e.g. nightly, pre-migration).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=14,
        help="Delete snapshots in --dest older than this many days. Set to 0 to disable.",
    )
    args = parser.parse_args(argv)
    try:
        backup_database(args.dest, label=args.label)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"[backup] FAILED: {exc}", file=sys.stderr)
        return 2
    prune_old_backups(args.dest, retention_days=args.retention_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
