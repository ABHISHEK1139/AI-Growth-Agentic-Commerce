"""Database access for operator scripts, resolved from the one authoritative engine.

Why this module exists
----------------------
``apps/api/db.py`` and ``services/db/engine.py`` used to define two separate engines.
They disagreed on pool sizing, and more importantly on whether an unreachable
PostgreSQL silently falls back to a local SQLite file. That duplication meant an
operator script and the API serving traffic could be pointed at different databases
with nothing to say so. ``services/db/`` has been deleted; this module is the single
entry point that replaces it for scripts.

The guard is not optional
-------------------------
``apps.api.db.get_engine()`` falls back to SQLite when it cannot reach PostgreSQL,
which is what lets a laptop run the demo with no Docker. That is a reasonable default
for a web process and an unacceptable one for a script that imports a catalog or
promotes staged records into production: the run would succeed, report success, and
write into ``data/local_dev.db`` where nobody looks.

``require_server_database()`` therefore refuses a SQLite engine unless the caller has
explicitly opted in with ``allow_sqlite_fallback=True``. The opt-in is spelled out at
every call site so the intent is visible rather than implied.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Scripts run as standalone entry points, so ensure the project root is importable
# before reaching for anything inside the repository.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from apps.api.db import get_engine

__all__ = [
    "get_engine",
    "require_server_database",
    "server_session_factory",
]


def require_server_database(
    *,
    operation: str,
    allow_sqlite_fallback: bool = False,
) -> Engine:
    """Return the shared engine, refusing a silent SQLite fallback.

    Args:
        operation: Named in the error message so an operator knows which run failed,
            e.g. ``"catalog import"``. A bare "connection failed" gives no clue
            whether the import or the promotion broke.
        allow_sqlite_fallback: Set only by tests and deliberate local demos. Anything
            that writes production-shaped data must leave this ``False``.

    Raises:
        RuntimeError: If the resolved engine is a SQLite file while the opt-in is off.
    """
    engine = get_engine()

    if not engine.url.get_backend_name().startswith("sqlite"):
        return engine

    if allow_sqlite_fallback:
        return engine

    raise RuntimeError(
        f"Refusing to run {operation}: PostgreSQL was unreachable and the engine "
        f"resolved to the local SQLite file {engine.url.database!r}. Continuing would "
        "write to a throwaway database and report success. Set DB_HOST/DB_USER/"
        "DB_PASSWORD/DB_NAME or DATABASE_URL so this script reaches the real store."
    )


def server_session_factory(engine: Engine) -> sessionmaker[Session]:
    """A session factory bound to the given engine.

    ``expire_on_commit=False`` matches ``apps.api.db.get_session_factory`` so a script
    and the API behave identically after a commit: attributes stay readable instead of
    triggering a refresh.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
