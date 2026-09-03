"""Shared pytest fixtures.

The unit suite must run with no Docker, no database, no Redis, and no
credentials. Anything that needs a datastore is marked ``integration``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

# Add buyer-agent to sys.path for contract testing
sys.path.insert(0, str(Path(__file__).parent.parent / "buyer-agent"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Set before any application import so cached settings never see a developer's
# real environment. `fake`/`mock` providers are the defaults we want under test,
# unless an explicit opt-in is set for live integration testing.
# NOTE: We must also clear the settings cache here because `get_settings()` is
# cached at module-import time. If the config module was imported in a prior
# pytest worker (or the first import happened before this file set env vars),
# the cached value persists. Clearing it now ensures Settings() re-evaluates.
# We use a local import inside the if-block to avoid triggering the full
# apps.api.config import until we've set up the env vars we want it to see.
# Set safe defaults BEFORE any application import. This runs unconditionally
# (not inside an `if ALLOW_LIVE_CREDENTIALS != "1"` block) because the unit-test
# suite must *always* be isolated from the developer's ambient environment.
# `ALLOW_LIVE_CREDENTIALS=1` only controls whether .env credentials are visible
# to Settings() — it does NOT disable the safe defaults set here.
#
# NOTE: we use ``os.environ["KEY"] = value`` (not setdefault) to FORCE-override
# any ambient values the developer's shell has set. setdefault would leave an
# existing PAYMENT_PROVIDER=razorpay in place and the unit tests would fail.
os.environ["APP_ENV"] = "local"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ALLOW_LIVE_CREDENTIALS"] = "0"
os.environ["PAYMENT_PROVIDER"] = "fake"
os.environ["MODEL_PROVIDER"] = "mock"
os.environ["SEARCH_PROVIDER"] = "null"
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["JWT_SECRET"] = "dev-only-change-me-generate-a-real-secret-before-staging"
os.environ["SESSION_SECRET"] = "dev-only-change-me-too"

# Clear any cached settings that may have been populated from the shell's
# inherited environment before conftest.py ran.
try:
    from apps.api.config import get_settings

    get_settings.cache_clear()
except ImportError:
    pass  # config not yet imported — no cache to clear


@pytest.fixture(autouse=True, scope="session")
def _isolate_settings_from_dotenv() -> Iterator[None]:
    """Unit tests must never read the developer's `.env` file.

    The `.env` file may contain real-looking credentials for local development.
    Unit tests must be deterministic and not depend on what is in `.env`.
    Therefore, `env_file` is always set to ``None`` in the unit test environment,
    regardless of ``ALLOW_LIVE_CREDENTIALS``.

    ``ALLOW_LIVE_CREDENTIALS=1`` only controls whether the ambient environment
    variables (set at the top of this file) are overridden with safe defaults.
    When it is set, tests that specifically want to exercise live credential
    scenarios can pass explicit values to ``Settings(...)`` as needed.

    NOTE: ``scope="session"`` means this runs once per test process. We clear the
    settings cache both on entry and on exit to ensure that the first test in the
    session gets a clean slate and that no cached values bleed into a subsequent
    test session in the same process (e.g. pytest-repeat, pytest-xdist workers).
    """
    from apps.api.config import Settings, get_settings

    original = Settings.model_config.get("env_file")
    # NEVER read .env in unit tests — it may contain real credentials
    Settings.model_config["env_file"] = None
    get_settings.cache_clear()
    try:
        yield
        get_settings.cache_clear()
    finally:
        Settings.model_config["env_file"] = original
        get_settings.cache_clear()


@pytest.fixture
def settings():  # noqa: ANN201 - inferred from the application factory
    from apps.api.config import Settings

    return Settings(
        app_env="local",
        payment_provider="fake",
        model_provider="mock",
        log_level="WARNING",
    )


@pytest.fixture
def app(settings) -> FastAPI:
    from apps.api.main import create_app
    from apps.api.middleware.ratelimit import InMemoryRateLimitBackend

    application = create_app(settings)
    # Rate limiting stays *on* under test, but counts in process memory. Pointing
    # at a real Redis would make the suite need a datastore, and would carry
    # counters between tests inside one fixed window, so an unrelated test could
    # be the thing that trips a limit. The fixture is function scoped, so every
    # test starts from an empty counter.
    application.state.rate_limit_backend = InMemoryRateLimitBackend()
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


class LogCapture:
    """Log lines as the process would actually write them.

    Assertions run against the output of the real :class:`JsonFormatter`, not
    against the record objects, because the formatter is where redaction happens.
    Checking records would prove the wrong thing.
    """

    def __init__(self) -> None:
        self.raw: list[str] = []

    @property
    def records(self) -> list[dict]:
        import json

        return [json.loads(line) for line in self.raw]

    def with_event(self, event: str) -> list[dict]:
        return [record for record in self.records if record.get("event") == event]

    @property
    def text(self) -> str:
        return "\n".join(self.raw)


@pytest.fixture
def logs(app: FastAPI) -> Iterator[LogCapture]:
    """Capture emitted log lines.

    Depends on ``app`` so it installs itself *after* ``create_app`` has called
    ``configure_logging``, which replaces the root handlers — including the one
    pytest's own ``caplog`` installs.
    """
    import logging

    from packages.observability.logging import JsonFormatter

    capture = LogCapture()

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            capture.raw.append(self.format(record))

    handler = _Handler()
    handler.setFormatter(JsonFormatter(service="test"))
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    # The suite runs at WARNING; the access log line is INFO.
    root.setLevel(logging.INFO)
    try:
        yield capture
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
