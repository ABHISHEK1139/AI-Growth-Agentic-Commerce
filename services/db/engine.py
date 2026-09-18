"""Single authoritative connection pool and engine manager."""

from __future__ import annotations

import contextlib
import os
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event, text

#: Fallback when DATABASE_URL is unset. Mirrors the default in
#: ``apps.api.config.Settings`` without importing the delivery layer, which
#: the architecture contract forbids (services must never import apps.*).
_DEFAULT_DATABASE_URL = "postgresql+psycopg://agentpay:agentpay@localhost:5432/agentpay"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


def _install_sqlite_compat(engine: Engine) -> Engine:
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_to_sqlite(type_: object, compiler: object, **kw: object) -> str:
        return "JSON"

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_connection: Any, _record: Any) -> None:
        import datetime

        with contextlib.suppress(AttributeError):
            dbapi_connection.create_function(
                "now", 0, lambda: datetime.datetime.now(datetime.UTC).isoformat()
            )

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = _database_url()

    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            echo=False,
        )
        return _install_sqlite_compat(engine)

    connect_args: dict[str, object] = {
        "connect_timeout": 5,
        "application_name": "agentpay-core",
    }

    return create_engine(
        url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_timeout=10,
        connect_args=connect_args,
        echo=False,
    )


def check_database() -> bool:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
