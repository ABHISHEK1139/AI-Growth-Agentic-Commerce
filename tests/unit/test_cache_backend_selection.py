"""Cache backend selection must default to the *correct* option, not the fast one.

The failure this prevents
-------------------------
``InMemoryCacheBackend`` is a per-process dictionary. It is faster than nothing and it
is wrong the moment there is more than one API worker or replica: each process holds its
own copy, so an invalidation on one does not reach the others, and a reader can be served
a stale offer price until its TTL expires. Nothing errors. The cache simply stops
meaning what it says.

The tempting default is therefore the dangerous one -- "cache locally if Redis is
missing" reads like graceful degradation and is in fact silent data staleness. So the
default is the opposite: with no Redis and no explicit acknowledgement, caching is
*disabled*, which is correct and slower.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from apps.api.config import Settings
from apps.api.main import create_app
from packages.cache import (
    InMemoryCacheBackend,
    NullCacheBackend,
    RedisCacheBackend,
    get_cache,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clean() -> Any:
    yield
    reset_cache(None)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "local",
        "database_url": "sqlite+pysqlite:///:memory:",
        "jwt_secret": "real",
        "session_secret": "real",
        "channel_encryption_key": "real-channel-key",
        "payment_provider": "fake",
        "model_provider": "mock",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _build(**overrides: Any) -> FastAPI:
    return create_app(_settings(**overrides))


def test_redis_is_selected_when_configured() -> None:
    """The normal deployment: a shared cache, so invalidations propagate."""
    _build(redis_url="redis://cache:6379/0")
    assert isinstance(get_cache(), RedisCacheBackend)


def test_no_redis_and_no_opt_in_disables_caching() -> None:
    """The important default.

    Correct and slower beats fast and silently wrong. Disabling the cache restores
    today's behaviour exactly, so nothing breaks -- it simply stops pretending to cache.
    """
    _build(redis_url="", cache_allow_process_local=False)
    assert isinstance(get_cache(), NullCacheBackend), (
        "a process-local cache was selected without acknowledgement; with more than one "
        "worker that serves stale entries after an invalidation"
    )


def test_process_local_requires_an_explicit_opt_in() -> None:
    """Single-process deployments can still have a cache, but must say so."""
    _build(redis_url="", cache_allow_process_local=True)
    assert isinstance(get_cache(), InMemoryCacheBackend)


def test_cache_disabled_outranks_everything() -> None:
    """`cache_enabled=false` wins even with a Redis URL, so the kill switch is total."""
    _build(redis_url="redis://cache:6379/0", cache_enabled=False)
    assert isinstance(get_cache(), NullCacheBackend)


def test_the_process_local_fallback_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing the risky option must say so, loudly.

    Without this the only way to discover the deployment is in the unsafe configuration
    is to go looking in the settings for a flag nobody documented.

    Asserted against ``apps.api.main.logger`` directly rather than through the root
    handler: ``get_logger`` returns a logger that does not propagate, so a root-level
    capture sees nothing. Intercepting the logger that actually emits is both simpler
    and a more precise statement of what is being promised.
    """
    import apps.api.main as main_module

    warnings: list[dict[str, Any]] = []

    class _RecordingLogger:
        def warning(self, message: str, extra: dict[str, Any] | None = None) -> None:
            warnings.append({"message": message, **(extra or {})})

    monkeypatch.setattr(main_module, "logger", _RecordingLogger())

    _build(redis_url="", cache_allow_process_local=True)

    assert warnings, "the process-local fallback was chosen silently"
    event = warnings[0].get("event")
    assert event == "CACHE_PROCESS_LOCAL_FALLBACK"

    message = str(warnings[0]["message"])
    assert "stale" in message.lower(), "the warning must state the actual risk"
    assert "REDIS_URL" in message, "the warning must say how to fix it"


def test_disabling_the_cache_without_redis_also_says_why() -> None:
    """The silent-but-safe path is logged too.

    Being slower for a reason nobody can find is its own kind of confusing, and this is
    the configuration a first-time deployer hits.
    """
    import apps.api.main as main_module

    warnings: list[dict[str, Any]] = []

    class _RecordingLogger:
        def warning(self, message: str, extra: dict[str, Any] | None = None) -> None:
            warnings.append({"message": message, **(extra or {})})

    original = main_module.logger
    main_module.logger = _RecordingLogger()  # type: ignore[assignment]
    try:
        _build(redis_url="", cache_allow_process_local=False)
    finally:
        main_module.logger = original  # type: ignore[assignment]

    assert warnings, "caching was disabled without explanation"
    assert warnings[0].get("event") == "CACHE_DISABLED_NO_REDIS"
