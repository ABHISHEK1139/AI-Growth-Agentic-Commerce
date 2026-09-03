"""Single authoritative connection pool and engine manager."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text

from apps.api.config import get_settings


def _install_sqlite_compat(engine: Engine) -> Engine:
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_to_sqlite(type_: object, compiler: object, **kw: object) -> str:
        return "JSON"

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_connection: object, _record: object) -> None:
        import datetime

        try:
            dbapi_connection.create_function(
                "now", 0, lambda: datetime.datetime.now(datetime.UTC).isoformat()
            )
        except AttributeError:
            pass

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url

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
