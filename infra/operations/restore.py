#!/usr/bin/env python3
"""PostgreSQL restore script for the AgentPay commerce database.

Phase 9 acceptance: backup and restore procedure exists and is exercised in CI.

This is the companion to ``infra.operations.backup``. It accepts a
``*.sql.gz`` snapshot produced by that script and pipes it into
``psql``. The same connection-variable resolution applies.

Usage:
    python -m infra.operations.restore --snapshot /var/backups/agentpay/agentpay_nightly_20260101T000000Z.sql.gz
    python -m infra.operations.restore --snapshot /tmp/snap.sql.gz --yes

The script will refuse to run against a non-empty database without an
explicit ``--yes`` flag, so a typo in the snapshot path cannot silently
overwrite production.

Required environment variables: see ``infra.operations.backup``.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys

#: Reuse the connection-variable resolver from the backup module so the
#: two scripts agree on the environment.
from infra.operations.backup import (  # type: ignore[import-not-found]
    _parse_database_url,
    _redact_url,
)


def _resolve_env() -> dict[str, str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return _parse_database_url(url)
    return {
        "PGHOST": os.environ.get("PGHOST", "localhost"),
        "PGPORT": os.environ.get("PGPORT", "5432"),
        "PGUSER": os.environ.get("PGUSER", "agentpay"),
        "PGPASSWORD": os.environ.get("PGPASSWORD", ""),
        "PGDATABASE": os.environ.get("PGDATABASE", "agentpay"),
    }


def _existing_table_count(env: dict[str, str]) -> int:
    """Quick connectivity probe. Returns the number of user tables, or -1 if unreachable."""
    if shutil.which("psql") is None:
        return -1
    try:
        proc = subprocess.run(
            [
                "psql",
                "--no-password",
                "--no-psqlrc",
                "--tuples-only",
                "--command",
                "SELECT count(*) FROM information_schema.tables " "WHERE table_schema = 'public';",
            ],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return -1
    if proc.returncode != 0:
        return -1
    try:
        return int(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return -1


def restore_snapshot(snapshot: os.PathLike[str] | str) -> int:
    """Restore a gzipped ``pg_dump`` snapshot into the configured database."""
    snap_path = os.fspath(snapshot)
    if not os.path.isfile(snap_path):
        raise FileNotFoundError(f"snapshot does not exist: {snap_path}")
    if not snap_path.endswith(".sql.gz") and not snap_path.endswith(".sql"):
        # Cheap validation so an obviously-wrong file is refused up front
        raise ValueError("snapshot filename must end in .sql.gz (pg_dump output) or .sql")

    if shutil.which("psql") is None:
        raise RuntimeError(
            "psql is not installed. Install postgresql-client (apt: postgresql-client, brew: libpq)."
        )

    env = os.environ.copy()
    pg_vars = _resolve_env()
    env.update(pg_vars)

    existing = _existing_table_count(pg_vars)
    if existing < 0:
        print(
            "[restore] WARNING: could not probe the target database. Proceeding; "
            "ensure the database exists and the connection variables are correct.",
            file=sys.stderr,
        )
    elif existing > 0:
        return 3  # caller will surface the refusal
    print(
        f"[restore] starting against {_redact_url(os.environ.get('DATABASE_URL', ''))} from {snap_path}",
        file=sys.stderr,
    )

    cmd = ["psql", "--no-password", "--no-psqlrc", "--set", "ON_ERROR_STOP=1"]
    with open(snap_path, "rb") as raw_fp:
        if snap_path.endswith(".gz"):
            sql_fp = gzip.GzipFile(fileobj=raw_fp, mode="rb")
        else:
            sql_fp = raw_fp  # type: ignore[assignment]
        proc = subprocess.Popen(
            cmd, env=env, stdin=sql_fp, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = proc.communicate()
    if proc.returncode != 0:
        err_text = (err or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"psql exited with code {proc.returncode}: {err_text[:1000]}")
    print(f"[restore] OK: {snap_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore a compressed PostgreSQL snapshot into the AgentPay database."
    )
    parser.add_argument("--snapshot", required=True, help="Path to a .sql.gz snapshot file.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Allow restore into a non-empty database. Required to overwrite an existing schema.",
    )
    args = parser.parse_args(argv)

    env = _resolve_env()
    existing = _existing_table_count(env)
    if existing > 0 and not args.yes:
        print(
            f"[restore] REFUSED: target database has {existing} public tables. "
            "Re-run with --yes if you intend to overwrite them.",
            file=sys.stderr,
        )
        return 3

    try:
        return restore_snapshot(args.snapshot)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"[restore] FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
