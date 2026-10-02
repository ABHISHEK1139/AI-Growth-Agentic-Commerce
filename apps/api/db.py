"""Database engine and session management.

The engine is created lazily so that importing the application never requires a
reachable database. That matters for two reasons: unit tests must run without
Docker, and ``/health`` must stay answerable while ``/health/db`` reports the
datastore as down.

The declarative :class:`~packages.db.base.Base` used to be declared here, which
made every ``services/*/models.py`` import the delivery layer in order to declare
a table. It now lives in :mod:`packages.db.base` and is re-exported below, so this
module still reads as the one place the delivery layer goes for anything
database-shaped while the domain imports the shared location directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from apps.api.config import get_settings
from packages.db.base import Base

__all__ = [
    "Base",
    "check_database",
    "get_db",
    "get_engine",
    "get_session_factory",
]


def _install_sqlite_compat(engine: Engine) -> Engine:
    """Make the raw PostgreSQL-flavoured SQL in the services layer run on SQLite.

    The demo must run end to end on a laptop with no Docker: judges and the
    buildathon demo script start the API against a local SQLite file when no
    Postgres is reachable. Three incompatibilities exist in the hand-written
    repository SQL and the ORM column types:

    1. ``JSONB`` columns (ORM models) — compiled as plain ``JSON`` on SQLite.
    2. ``now()`` in raw ``UPDATE`` statements — registered as a connection
       function returning the current UTC timestamp.
    3. ``TIMESTAMPTZ`` DDL from ``create_all`` — SQLite accepts it as TEXT.

    PostgreSQL behaviour is untouched: the shim only attaches when the URL
    scheme is ``sqlite``.
    """
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_to_sqlite(type_: object, compiler: object, **kw: object) -> str:  # noqa: ANN001
        return "JSON"

    @event.listens_for(engine, "connect")
    def _register_now(dbapi_connection: object, _record: object) -> None:
        import datetime

        def _now() -> str:
            return datetime.datetime.now(datetime.UTC).isoformat()

        dbapi_connection.create_function("now", 0, _now)  # type: ignore[attr-defined]

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide SQLAlchemy engine with seamless local SQLite fallback."""
    settings = get_settings()
    db_url = settings.resolved_database_url
    is_sqlite = settings.is_sqlite_url

    if not is_sqlite:
        try:
            test_engine = create_engine(
                db_url, connect_args={"connect_timeout": settings.db_connect_timeout_seconds}
            )
            with test_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            test_engine.dispose()
        except Exception as exc:
            settings.validate_datastore_for_env()
            import logging
            from pathlib import Path

            from packages.observability.logging import redact

            # Never the URL itself: it carries the database password, and this
            # line lands in whatever ships the container logs. Host and database
            # are enough to diagnose "wrong host" or "wrong database".
            #
            # This is a WARNING, not INFO, and it names the file that now holds
            # the data. Every row in the configured database is invisible to this
            # process from here on, so an operator has to be able to find the line
            # in a log without knowing that the fallback exists.
            logging.getLogger("apps.api.db").warning(
                "PostgreSQL unreachable at %s (%s); FALLING BACK to local SQLite at "
                "data/local_dev.db. This process will serve an empty catalog and any "
                "writes are not durable.",
                redact(db_url),
                type(exc).__name__,
            )
            db_path = Path(__file__).resolve().parents[2] / "data" / "local_dev.db"
            db_url = f"sqlite:///{db_path.as_posix()}"
            is_sqlite = True

    connect_args = {} if is_sqlite else {"connect_timeout": 1}
    engine = create_engine(
        db_url,
        pool_pre_ping=True,  # reconnect transparently after an idle drop
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_size=5 if is_sqlite else settings.db_pool_size,
        max_overflow=0 if is_sqlite else settings.db_max_overflow,
        future=True,
        connect_args=connect_args,
        # Never echo SQL: bound parameters can contain buyer data.
        echo=False,
    )
    if is_sqlite:
        engine = _install_sqlite_compat(engine)
        with engine.connect() as conn:
            conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS audit_event (
                    event_id TEXT PRIMARY KEY,
                    merchant_id TEXT,
                    request_id TEXT,
                    trace_id TEXT,
                    agent_run_id TEXT,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    input_hash TEXT,
                    decision TEXT,
                    reason_code TEXT,
                    policy_version TEXT,
                    model_version TEXT,
                    amount_minor INTEGER,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            )
            conn.commit()
    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session bound to one request.

    The session is rolled back on any exception so a failed request can never
    leave a partial write behind, then always closed.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database() -> tuple[bool, str | None]:
    """Cheap liveness probe. Returns ``(ok, error_kind)``.

    The error is reported as a coarse exception class name rather than the driver
    message, because connection errors routinely embed the DSN, and the DSN
    embeds a password.
    """
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not decide
        return False, type(exc).__name__
    return True, None
