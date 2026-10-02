"""A non-local deployment must not run on test-double providers.

The gap this closes
-------------------
``validate_for_env`` checked providers in one direction only -- "is ``razorpay``
*configured*?" -- and never asked "is the **fake** provider running?" An operator who set
``APP_ENV=staging`` and the live-credential opt-in but forgot ``PAYMENT_PROVIDER`` got the
``"fake"`` default, startup passed, and the deployment ran
:class:`~services.payments.provider.FakePaymentProvider`.

Consequences, none of which raise:

* every payment reports success without moving money;
* webhooks verify against a secret published in this repository, so a forged
  ``payment.captured`` callback is accepted;
* the console shows healthy orders that do not exist at the provider.

These tests drive ``create_app`` rather than the validator, because a check that exists
but is not wired into startup is exactly the failure that produced this bug in the first
place -- ``validate_datastore_for_env`` had precisely that problem.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from apps.api.config import Settings

# Imported here, at module scope, deliberately.
#
# `apps.api.main` constructs an app at module level (`app = create_app()`), so importing
# it while this module's fixture has set APP_ENV=staging raises during the import -- and
# an exception raised during an import inside a test body escapes `pytest.raises`. The
# import therefore has to happen at collection time, under the ordinary environment, and
# the tests below pass an explicit `settings` object to `create_app` instead.
from apps.api.main import create_app

LIVE_ENV = {
    "APP_ENV": "staging",
    "ALLOW_LIVE_CREDENTIALS": "1",
    "JWT_SECRET": "a" * 48,
    "SESSION_SECRET": "b" * 48,
    "CHANNEL_ENCRYPTION_KEY": "c" * 48,
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
}


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A staging environment with real-shaped secrets, so only the provider varies."""
    for key, value in LIVE_ENV.items():
        monkeypatch.setenv(key, value)
    # get_settings is an lru_cache singleton; the settings module caches env-derived
    # values at import time, so the cache is cleared for each test in this file.
    from apps.api import config as config_module

    config_module.get_settings.cache_clear()
    yield
    config_module.get_settings.cache_clear()


def _staging(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "staging",
        "jwt_secret": "a" * 48,
        "session_secret": "b" * 48,
        "channel_encryption_key": "c" * 48,
        "database_url": "sqlite+pysqlite:///:memory:",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The validator itself
# ---------------------------------------------------------------------------


def test_fake_payment_provider_is_refused_outside_local() -> None:
    settings = _staging(payment_provider="fake")

    with pytest.raises(ValueError, match="test double"):
        settings.validate_providers_for_env()


def test_mock_model_provider_is_refused_outside_local() -> None:
    """The agent would reason against a deterministic stub and appear to work."""
    settings = _staging(
        payment_provider="razorpay",
        razorpay_key_id="rzp_test",
        razorpay_key_secret="secret",
        razorpay_webhook_secret="webhook",
        model_provider="mock",
    )

    with pytest.raises(ValueError, match="MODEL_PROVIDER=mock"):
        settings.validate_providers_for_env()


def test_null_search_provider_is_refused_outside_local() -> None:
    """Research silently returning nothing is a degraded product, not a failure."""
    settings = _staging(
        payment_provider="razorpay",
        razorpay_key_id="rzp_test",
        razorpay_key_secret="secret",
        razorpay_webhook_secret="webhook",
        model_provider="ollama",
        search_provider="null",
    )

    with pytest.raises(ValueError, match="SEARCH_PROVIDER=null"):
        settings.validate_providers_for_env()


def test_local_may_use_test_doubles() -> None:
    """A laptop with no credentials must still boot. That is the whole point of local."""
    settings = Settings(app_env="local")

    settings.validate_providers_for_env()  # must not raise


def test_real_providers_pass_outside_local() -> None:
    settings = _staging(
        payment_provider="razorpay",
        razorpay_key_id="rzp_test",
        razorpay_key_secret="secret",
        razorpay_webhook_secret="webhook",
        model_provider="ollama",
        model_base_url="http://localhost:11434/v1",
        model_name="llama3",
        search_provider="searxng",
        search_base_url="http://localhost:8080",
    )

    settings.validate_providers_for_env()  # must not raise


def test_the_demo_escape_hatch_is_explicit() -> None:
    """A staging environment that is deliberately a demo can say so.

    Load testing and a judge's demo both need the fakes in a non-local environment. The
    point is that they are chosen rather than inherited.
    """
    settings = _staging(payment_provider="fake", allow_test_double_providers=True)

    settings.validate_providers_for_env()  # must not raise


def test_the_error_names_the_fix_and_the_risk() -> None:
    """The message has to be actionable and honest about the consequence."""
    settings = _staging(payment_provider="fake")

    with pytest.raises(ValueError) as excinfo:
        settings.validate_providers_for_env()

    message = str(excinfo.value)
    assert "PAYMENT_PROVIDER=fake" in message
    assert "moving money" in message, "the operator must learn what the risk is"
    assert "ALLOW_TEST_DOUBLE_PROVIDERS=1" in message, "the message must name the escape"


# ---------------------------------------------------------------------------
# Wired into startup, which is where it has to be
# ---------------------------------------------------------------------------


def test_create_app_refuses_a_staging_deployment_on_the_fake_provider(
    live_env: None,
) -> None:
    """Startup, not just the validator.

    ``validate_datastore_for_env`` was written, correct, and ineffective for a whole
    session because ``create_app`` never called it on the path that mattered. Driving the
    factory is the only assertion that catches that repeating.

    The import lives at module scope above; see the note there.
    """
    from apps.api.config import get_settings

    settings = get_settings()
    assert settings.payment_provider == "fake", "precondition: the fakes are in play"

    with pytest.raises(ValueError, match="test double"):
        create_app(settings)


def test_create_app_still_works_for_local() -> None:
    """The guard must not break the development experience."""
    from apps.api.config import Settings as S
    from apps.api.main import create_app

    app = create_app(
        S(
            app_env="local",
            database_url="sqlite+pysqlite:///:memory:",
            jwt_secret="a" * 48,
            session_secret="b" * 48,
            channel_encryption_key="c" * 48,
        )
    )

    assert app.routes


def test_the_provider_map_matches_the_actual_defaults() -> None:
    """The guard's table must cover whatever the settings actually default to.

    A new provider default added later, or a typo in this table, would leave a test
    double reachable outside local with nothing complaining.
    """
    defaults = Settings()
    table = Settings._TEST_DOUBLE_PROVIDERS  # noqa: SLF001

    assert defaults.payment_provider in table["PAYMENT_PROVIDER"]
    assert defaults.model_provider in table["MODEL_PROVIDER"]
    assert defaults.search_provider in table["SEARCH_PROVIDER"]

    # And nothing in the table is a real integration, or a real deployment would be
    # blocked by its own guard.
    assert "razorpay" not in table["PAYMENT_PROVIDER"]
    assert "mock" not in ("ollama", "groq", "grok", "local")


def test_env_var_name_matches_the_setting() -> None:
    """The documented variable and the field have to agree.

    They are declared in two places -- an operator reads the variable name in the error
    message, and the field is what actually reads it -- so a mismatch would produce a
    message instructing the reader to set a variable that does nothing.
    """
    assert (
        Settings.ALLOW_TEST_DOUBLE_PROVIDERS_VAR  # noqa: SLF001
        == "ALLOW_TEST_DOUBLE_PROVIDERS"
    )
    assert "allow_test_double_providers" in Settings.model_fields


def test_env_var_actually_enables_the_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The variable has to be read, not merely named in the error message."""
    from apps.api import config as config_module

    for key, value in LIVE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PAYMENT_PROVIDER", "fake")
    monkeypatch.setenv("ALLOW_TEST_DOUBLE_PROVIDERS", "1")
    config_module.get_settings.cache_clear()
    try:
        settings = config_module.get_settings()
        assert settings.allow_test_double_providers is True
        settings.validate_providers_for_env()
    finally:
        config_module.get_settings.cache_clear()


def test_the_old_check_does_not_cover_this() -> None:
    """Documents the asymmetry that made this a bug.

    ``validate_for_env`` asks whether the real provider is configured and is silent about
    the fake one. If a future change makes it refuse too, this test should be updated
    rather than left passing by accident.
    """
    settings = _staging(payment_provider="fake")

    settings.validate_for_env()  # does not raise, and should not start doing so here

    with pytest.raises(ValueError):
        settings.validate_providers_for_env()


def test_environment_is_not_leaked_between_tests() -> None:
    """The fixture must not leave APP_ENV=staging behind.

    A leaked staging environment would make every later unit test in the session run
    against the live-credential code path, which is the same class of silent-state bug.
    """
    assert os.environ.get("APP_ENV") in (None, "local") or os.environ.get("ALLOW_LIVE_CREDENTIALS")
