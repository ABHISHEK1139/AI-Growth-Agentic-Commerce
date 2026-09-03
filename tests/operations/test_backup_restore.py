"""Operations-scripts tests: backup / restore argument and redaction logic.

The tests do not invoke ``pg_dump`` or ``psql``; they cover the parse and
redact helpers and the script's refusal paths so the CI integration job
can rely on the same code path the operator does.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from infra.operations.backup import _parse_database_url, _redact_url, prune_old_backups


def test_parse_database_url_with_psycopg_dialect() -> None:
    """The SQLAlchemy ``+psycopg`` driver suffix is stripped before parsing."""
    env = _parse_database_url("postgresql+psycopg://agentpay:secret@db:5432/agentpay")
    assert env["PGHOST"] == "db"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "agentpay"
    assert env["PGPASSWORD"] == "secret"
    assert env["PGDATABASE"] == "agentpay"


def test_parse_database_url_without_dialect() -> None:
    env = _parse_database_url("postgresql://alice:hunter2@localhost:5433/shop")
    assert env["PGHOST"] == "localhost"
    assert env["PGPORT"] == "5433"
    assert env["PGUSER"] == "alice"
    assert env["PGPASSWORD"] == "hunter2"
    assert env["PGDATABASE"] == "shop"


def test_parse_database_url_default_port() -> None:
    env = _parse_database_url("postgresql+psycopg://u:p@h/db")
    assert env["PGPORT"] == "5432"


def test_redact_url_replaces_password() -> None:
    redacted = _redact_url("postgresql://user:supersecret@host:5432/db")
    assert "supersecret" not in redacted
    assert "***" in redacted
    assert "user" in redacted
    assert "host" in redacted


def test_redact_url_unset() -> None:
    assert _redact_url("") == "<unset>"


def test_prune_old_backups_removes_expired(tmp_path: Path) -> None:
    """``prune_old_backups`` removes files older than the retention window."""
    import time

    old = tmp_path / "agentpay_nightly_20200101T000000Z.sql.gz"
    old.write_text("old")
    fresh = tmp_path / "agentpay_nightly_20990101T000000Z.sql.gz"
    fresh.write_text("fresh")
    # Force mtime so the test is not time-of-day dependent.
    old_mtime = time.time() - (30 * 24 * 3600)
    fresh_mtime = time.time() - (60)
    os.utime(old, (old_mtime, old_mtime))
    os.utime(fresh, (fresh_mtime, fresh_mtime))

    removed = prune_old_backups(tmp_path, retention_days=14)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_prune_old_backups_skips_unrelated_files(tmp_path: Path) -> None:
    """Files that do not match the snapshot pattern are not pruned."""
    keep = tmp_path / "manual_export.txt"
    keep.write_text("important")
    import time

    mtime = time.time() - (30 * 24 * 3600)
    os.utime(keep, (mtime, mtime))
    removed = prune_old_backups(tmp_path, retention_days=14)
    assert removed == 0
    assert keep.exists()


def test_prune_old_backups_zero_retention_is_noop(tmp_path: Path) -> None:
    """``retention_days=0`` is documented as a no-op."""
    f = tmp_path / "agentpay_nightly_20200101T000000Z.sql.gz"
    f.write_text("x")
    assert prune_old_backups(tmp_path, retention_days=0) == 0
    assert f.exists()


def test_restore_refuses_non_sql_file(tmp_path: Path) -> None:
    """Restore rejects files that do not look like a pg_dump output."""
    from infra.operations.restore import restore_snapshot

    bogus = tmp_path / "not_a_snapshot.bin"
    bogus.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match=r"\.sql\.gz"):
        restore_snapshot(bogus)
