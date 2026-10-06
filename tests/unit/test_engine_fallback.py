"""The engine must never silently substitute a local file for a configured server.

``get_engine`` probes PostgreSQL once and caches the result for the life of the
process. When the probe failed it used to fall back to ``data/local_dev.db``
whatever had been configured, so a worker that started during a ten-second
PostgreSQL restart served an empty SQLite catalogue forever, while a sibling
worker served Postgres. Identical requests got different databases depending on
which worker the balancer picked, and the health probe - which tests the URL,
not the cached engine - stayed green throughout.

The rule these pin: a server that was never asked for may be replaced by the
local demo file, because that is the documented reason the fallback exists. A
server that was explicitly configured with ``DATABASE_URL`` reports
SERVICE_UNAVAILABLE instead, and because a failed call is never cached, the next
request re-probes rather than inheriting a dead database.

The probe itself is stubbed: these assert the *decision*, and must not touch the
network or the repo's own ``data/local_dev.db`` whatever databases happen to be
running on the machine that runs the suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine as real_create_engine
from sqlalchemy.exc import OperationalError

from apps.api import db as db_module
from apps.api.config import Settings


@pytest.fixture(autouse=True)
def _fresh_engine_cache():
    db_module.get_engine.cache_clear()
    yield
    db_module.get_engine.cache_clear()


def _probe_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any connection to a server fail, while SQLite keeps working in memory."""

    def fake_create_engine(url: object, **kwargs: object):
        if str(url).startswith("sqlite"):
            return real_create_engine("sqlite:///:memory:")
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(db_module, "create_engine", fake_create_engine)


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> Settings:
    settings = Settings(
        app_env="local",
        payment_provider="fake",
        model_provider="mock",
        log_level="WARNING",
        **overrides,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(db_module, "get_settings", lambda: settings)
    return settings


def test_an_explicit_postgres_url_is_never_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(
        monkeypatch,
        database_url="postgresql+psycopg://agentpay:agentpay@db:5432/agentpay",
    )
    _probe_down(monkeypatch)

    # It must fail loudly, with the driver's own connection error, which the
    # middleware already maps to SERVICE_UNAVAILABLE. Anything else - a SQLite
    # engine, a swallowed exception - is the bug this file is about.
    with pytest.raises(OperationalError):
        db_module.get_engine()


def test_a_failed_probe_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(
        monkeypatch,
        database_url="postgresql+psycopg://agentpay:agentpay@db:5432/agentpay",
    )
    _probe_down(monkeypatch)

    with pytest.raises(OperationalError):
        db_module.get_engine()
    # lru_cache never caches an exception, so this probes again rather than
    # replaying the first failure. A cached failure mode would do something
    # cheaper than a second full probe.
    with pytest.raises(OperationalError):
        db_module.get_engine()
    assert db_module.get_engine.cache_info().misses == 2


def test_an_unconfigured_database_still_gets_the_demo_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing configured: the fallback exists for exactly this laptop, and it
    # must keep working.
    _settings(monkeypatch, database_url=None)
    _probe_down(monkeypatch)

    engine = db_module.get_engine()

    assert engine.url.get_backend_name() == "sqlite"


def test_an_explicit_sqlite_url_is_used_as_given(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Choosing SQLite on purpose is a valid deployment, unlike falling back to
    # it. No probe runs at all: there is no server to test.
    requested = f"sqlite:///{(tmp_path / 'chosen.db').as_posix()}"
    _settings(monkeypatch, database_url=requested)

    engine = db_module.get_engine()

    assert str(engine.url) == requested
    assert db_module.get_engine.cache_info().misses == 1
