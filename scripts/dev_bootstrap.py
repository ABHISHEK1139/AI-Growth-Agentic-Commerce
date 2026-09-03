"""One-command local bootstrap: schema + demo seed on SQLite.

Usage:
    python scripts/dev_bootstrap.py

Creates ./data/local_dev.db with the full commerce schema and the committed
demo seed catalog, so the API can run with:

    DATABASE_URL=sqlite:///./data/local_dev.db uvicorn apps.api.main:app
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make first-party imports work when run as a plain script.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "local_dev.db"
DB_URL = f"sqlite:///{DB_PATH.as_posix()}"


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    import sqlalchemy
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_to_sqlite(type_: object, compiler: object, **kw: object) -> str:  # noqa: ANN001
        return "JSON"

    # Import every model module so its tables register on Base.metadata.
    import services.catalog.models  # noqa: F401
    import services.checkout.models  # noqa: F401
    import services.inventory.models  # noqa: F401
    import services.orders.models  # noqa: F401
    import services.payments.models  # noqa: F401
    import services.policy.models  # noqa: F401
    from apps.api.db import Base

    engine = sqlalchemy.create_engine(DB_URL)
    Base.metadata.create_all(engine)
    print(f"schema ready: {DB_PATH} ({len(Base.metadata.tables)} tables)")

    # Seed through the same code path the worker uses, pointed at SQLite.
    import os

    os.environ["DATABASE_URL"] = DB_URL
    # The engine cache must not hold the Postgres URL from any earlier import.
    from apps.api.db import get_engine, get_session_factory

    get_engine.cache_clear()
    get_session_factory.cache_clear()

    from apps.worker.seed_catalog import main as seed_main

    rc = seed_main([])
    print("seed complete" if rc == 0 else f"seed exited with {rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
