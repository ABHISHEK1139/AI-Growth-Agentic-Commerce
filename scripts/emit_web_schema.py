"""Emit apps/web/src/lib/catalogSchema.ts from the real models.

The web tier opens a SQLite file through `src/lib/serverDb.ts` and probes it at
`/health/db`. Nothing in a container ever created that file, so every
server-rendered route that touched the catalog threw
`ERR_SQLITE_ERROR: unable to open database file` and answered 500. The product
page then reported "Offer Currently Unavailable" for a well-stocked item, because
the offer lookup died before the bundled seed catalog could answer - a data
problem wearing the costume of a stock problem.

The web app cannot run the Python models, so the schema ships with it. It is
generated from the models rather than written by hand, because a hand-written
copy silently rots: `audit_event` is not in `Base.metadata` at all (it is created
by raw SQL in `apps/api/db.py`), which is exactly the kind of table a
hand-copied schema misses.

Regenerate after changing a model:

    python scripts/dev_bootstrap.py          # writes data/local_dev.db
    python scripts/emit_web_schema.py        # rewrites catalogSchema.ts

Usage:
    python scripts/emit_web_schema.py [path/to/local_dev.db]
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "local_dev.db"
TARGET = ROOT / "apps" / "web" / "src" / "lib" / "catalogSchema.ts"

HEADER = """/**
 * SQLite schema for the web tier, generated - do not edit by hand.
 *
 * See `scripts/emit_web_schema.py` for how to regenerate and why this is
 * generated rather than written out. The file is emitted as a string so it
 * bundles with no loader configuration and no runtime file read.
 *
 * An empty database is the expected starting state: the web tier's read paths
 * fall back to the committed seed catalog baked into the bundle, which is the
 * same data `scripts/dev_bootstrap.py` loads locally.
 */
export const CATALOG_SCHEMA_SQL = `
"""


def main() -> int:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        print(f"no database at {db_path}; run scripts/dev_bootstrap.py first", file=sys.stderr)
        return 1

    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        "select sql from sqlite_master "
        "where sql is not null and type in ('table', 'index') "
        "and name not like 'sqlite_%' "
        "order by type desc, name"
    ).fetchall()
    connection.close()

    statements: list[str] = []
    for (sql,) in rows:
        statement = sql.strip()
        # Re-running against an existing file must be a no-op rather than a
        # "table already exists" error, so every index is created conditionally.
        if statement.upper().startswith("CREATE INDEX"):
            statement = "CREATE INDEX IF NOT EXISTS " + statement[len("CREATE INDEX") :]
        statements.append(statement)

    body = ";\n".join(statements)
    # A backtick or `${` inside the DDL would end the template literal or start an
    # interpolation. Neither appears in SQLite DDL today, but a silent truncation
    # here would produce a schema missing its last table, so the check is cheap.
    for token in ("`", "${"):
        if token in body:
            print(f"refusing to emit: DDL contains {token!r}", file=sys.stderr)
            return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(HEADER + body + "\n`;\n", encoding="utf-8")

    tables = sum(1 for s in statements if s.upper().startswith("CREATE TABLE"))
    print(f"wrote {TARGET.relative_to(ROOT)}: {tables} tables, {len(statements)} statements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
