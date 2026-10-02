"""Configuration invariants that protect the demo and the credentials."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from apps.api.config import LIVE_OPT_IN_VAR, Settings, get_settings, is_loopback_url


def test_defaults_require_no_credentials() -> None:
    settings = Settings()

    assert settings.payment_provider == "fake"
    assert settings.model_provider == "mock"
    assert settings.razorpay_is_configured() is False


def test_money_defaults_are_integer_minor_units() -> None:
    """NFR-1: amounts are paise, never floats."""
    settings = Settings()

    assert isinstance(settings.auto_approval_limit_minor, int)
    assert isinstance(settings.max_transaction_amount_minor, int)
    # INR 5,000 auto-approval under an INR 70,000 ceiling is the hero scenario:
    # it forces REQUIRE_APPROVAL on a 64,999 laptop.
    assert settings.auto_approval_limit_minor == 500_000
    assert settings.max_transaction_amount_minor == 7_000_000


def test_blank_debug_cap_means_no_cap() -> None:
    """An unset env var must mean "process everything", not crash."""
    assert Settings(max_lines_debug="").max_lines_debug is None
    assert Settings(max_lines_debug=None).max_lines_debug is None
    assert Settings(max_lines_debug=200_000).max_lines_debug == 200_000


def test_cors_origins_parse_to_a_list() -> None:
    settings = Settings(cors_allow_origins="http://localhost:3000, http://localhost:3001")

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:3001"]


def test_wildcard_cors_is_rejected_outside_local() -> None:
    with pytest.raises(ValueError, match="Wildcard CORS origin"):
        _ = Settings(app_env="demo", cors_allow_origins="*").cors_origins


def test_wildcard_cors_is_tolerated_locally() -> None:
    assert Settings(app_env="local", cors_allow_origins="*").cors_origins == ["*"]


def test_invalid_log_level_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="CHATTY")


def test_search_results_are_bounded() -> None:
    """The agent must never be handed an unbounded candidate set."""
    with pytest.raises(ValidationError):
        Settings(max_search_results=500)


class TestStartupGuard:
    """`validate_for_env` is the guard against shipping template secrets."""

    def test_placeholders_are_fine_locally(self) -> None:
        Settings(app_env="local").validate_for_env()  # must not raise

    def test_placeholder_jwt_secret_is_rejected_outside_local(self) -> None:
        settings = Settings(app_env="demo", session_secret="a-real-one")

        with pytest.raises(ValueError, match="JWT_SECRET is still the template placeholder"):
            settings.validate_for_env()

    def test_razorpay_without_credentials_is_rejected(self) -> None:
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            payment_provider="razorpay",
        )

        with pytest.raises(ValueError, match="no key id/secret"):
            settings.validate_for_env()

    def test_razorpay_without_webhook_secret_is_rejected(self) -> None:
        """Unsigned webhooks are indistinguishable from spoofed ones, so a
        missing webhook secret is a hard failure, not a warning."""
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            payment_provider="razorpay",
            razorpay_key_id="rzp_test_x",
            razorpay_key_secret="s",
            razorpay_webhook_secret="",
        )

        with pytest.raises(ValueError, match="RAZORPAY_WEBHOOK_SECRET is empty"):
            settings.validate_for_env()

    def test_remote_guard_without_a_key_is_rejected(self) -> None:
        """`remote` is the billed guard path and it cannot authenticate without a
        key of its own. Booting anyway would leave every prompt cleared by the
        no-credential skip while the operator believed a classifier was running."""
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            guard_provider="remote",
            guard_api_key="",
        )

        with pytest.raises(ValueError, match="GUARD_API_KEY is empty"):
            settings.validate_for_env()

    def test_remote_guard_is_not_satisfied_by_the_reasoning_models_key(self) -> None:
        """The two credentials are separate on purpose; one does not stand in for
        the other."""
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            guard_provider="remote",
            guard_api_key="",
            model_api_key="reasoning-key-not-a-real-credential",
        )

        with pytest.raises(ValueError, match="GUARD_API_KEY is empty"):
            settings.validate_for_env()

    def test_openai_compatible_without_api_key_is_rejected(self) -> None:
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            model_provider="openai_compatible",
        )

        with pytest.raises(ValueError, match="MODEL_API_KEY is empty"):
            settings.validate_for_env()

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://localhost:11434/v1",
            "http://127.0.0.1:11434/v1",
            "http://[::1]:11434/v1",
            "http://127.0.0.5:8000/v1",
        ],
    )
    def test_a_model_on_this_host_needs_no_api_key(self, base_url: str) -> None:
        """A local Ollama or llama.cpp server has no credential to present, so
        demanding one refused a valid configuration at startup. This is what made
        MODEL_BASE_URL undeployable outside APP_ENV=local."""
        Settings(
            app_env="demo",
            jwt_secret="a-real-secret",
            session_secret="another-real-secret",
            channel_encryption_key="a-real-channel-key",
            model_provider="openai_compatible",
            model_base_url=base_url,
            model_name="qwen3.5:4b",
            model_api_key="",
            cors_allow_origins="https://agentpay.example.com",
        ).validate_for_env()  # must not raise

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://models.example.invalid/v1",
            # Contains "localhost", is a different machine. A substring test would
            # wave this through and call a remote endpoint unauthenticated.
            "https://localhost.example.invalid/v1",
            "https://models.example.invalid/v1?host=127.0.0.1",
        ],
    )
    def test_a_model_off_this_host_still_needs_an_api_key(self, base_url: str) -> None:
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            model_provider="openai_compatible",
            model_base_url=base_url,
            model_api_key="",
        )

        with pytest.raises(ValueError, match="MODEL_API_KEY is empty"):
            settings.validate_for_env()

    def test_an_empty_base_url_is_still_rejected(self) -> None:
        settings = Settings(
            app_env="staging",
            jwt_secret="real",
            session_secret="real",
            model_provider="openai_compatible",
            model_base_url="",
            model_api_key="a-key",
        )

        with pytest.raises(ValueError, match="MODEL_BASE_URL is empty"):
            settings.validate_for_env()

    def test_a_fully_configured_demo_env_passes(self) -> None:
        Settings(
            app_env="demo",
            jwt_secret="a-real-secret",
            session_secret="another-real-secret",
            channel_encryption_key="a-real-channel-key",
            payment_provider="razorpay",
            razorpay_key_id="rzp_test_abc",
            razorpay_key_secret="secret",
            razorpay_webhook_secret="whsec",
            model_provider="openai_compatible",
            model_base_url="https://models.example.com/v1",
            model_name="test-model",
            model_api_key="test-key-not-a-real-credential",
            cors_allow_origins="https://agentpay.example.com",
        ).validate_for_env()  # must not raise

    def test_the_placeholder_channel_key_is_refused_outside_local(self) -> None:
        """Channel access tokens would be encrypted with a key in this repository.

        The other placeholder secrets are refused for the same reason, and the
        failure is at startup rather than at the first store sync, because by then
        a merchant has already entered real Shopify credentials.
        """
        with pytest.raises(ValueError, match="CHANNEL_ENCRYPTION_KEY"):
            Settings(
                app_env="staging",
                jwt_secret="real",
                session_secret="real",
                payment_provider="fake",
                model_provider="mock",
            ).validate_for_env()


class TestDatastoreResolution:
    """The DSN assembled from the discrete `DB_*` parts.

    `DATABASE_URL` was the only datastore setting before this, so an operator
    following their provider's documentation -- host, user, password, database
    name -- had to hand-assemble a URL, and a password containing `@` or `/`
    silently truncated it.
    """

    def test_discrete_parts_compose_a_dsn(self) -> None:
        settings = Settings(
            database_url=None,
            db_driver="postgresql+psycopg",
            db_host="db.internal",
            db_port=6543,
            db_user="agentpay",
            db_password="s3cret",
            db_name="commerce",
        )

        assert (
            settings.resolved_database_url
            == "postgresql+psycopg://agentpay:s3cret@db.internal:6543/commerce"
        )
        assert settings.is_sqlite_url is False

    def test_an_explicit_database_url_wins(self) -> None:
        """A deployment that already sets a DSN must be unaffected by the new parts."""
        settings = Settings(
            database_url="sqlite:///./data/local_dev.db",
            db_host="ignored.example.com",
            db_password="ignored",
        )

        assert settings.resolved_database_url == "sqlite:///./data/local_dev.db"
        assert settings.is_sqlite_url is True

    def test_a_password_with_url_metacharacters_is_encoded(self) -> None:
        """A managed provider password containing `@` or `/` truncates a raw DSN.

        Interpolated unencoded, `p@ss/word` makes the parser read part of it as
        the host and the rest as credentials -- a connection failure that looks
        like a wrong-password error.
        """
        settings = Settings(
            database_url=None,
            db_host="db.example.com",
            db_user="u",
            db_password="p@ss/word?#x",
            db_name="d",
        )

        url = settings.resolved_database_url
        assert "p%40ss%2Fword%3F%23x" in url
        assert url.endswith("@db.example.com:5432/d")


class TestDatastoreSafety:
    """The SQLite fallback, which is the most dangerous line of configuration here.

    `apps.api.db` falls back to a local file when Postgres is unreachable so a
    laptop still boots. A deployment that hits that path appears to start
    normally and then serves an empty catalog while losing every write.
    """

    def test_a_non_local_deployment_refuses_to_fall_back(self) -> None:
        settings = Settings(
            app_env="staging",
            database_url="postgresql+psycopg://agentpay:agentpay@db:5432/agentpay",
            db_password="",
        )

        with pytest.raises(ValueError, match="DB_PASSWORD"):
            settings.validate_datastore_for_env()

    def test_a_configured_password_does_not_buy_the_fallback(self) -> None:
        """The regression that shipped: a set ``DB_PASSWORD`` used to permit the fallback.

        The original check raised only when the password was *empty*, reasoning that a
        deployment reaching for the fallback probably had not finished configuring
        itself. Docker Compose sets ``DB_PASSWORD``, so the guard never fired there.

        It then fired for real during load testing: Docker Desktop restarted, the API
        process started before PostgreSQL was accepting connections, and the engine
        fell back to SQLite. The service reported **healthy** while serving an empty
        catalogue with every write going to a local file. Nothing caught it except a
        seeded fixture suddenly returning 404s.

        Whether the password is set says nothing about whether an empty local database
        is acceptable. This asserts that.
        """
        settings = Settings(
            app_env="staging",
            database_url="postgresql+psycopg://agentpay:agentpay@db:5432/agentpay",
            db_password="agentpay",
            allow_sqlite_fallback=False,
        )

        with pytest.raises(ValueError) as excinfo:
            settings.validate_datastore_for_env()

        message = str(excinfo.value)
        assert "staging" in message, "the error must name the environment"
        assert "DB_HOST" in message, "the error must name the variables to set"
        assert (
            "ALLOW_SQLITE_FALLBACK" in message
        ), "the error must name the one variable that does permit this"

    def test_every_non_local_environment_refuses(self) -> None:
        """The guard is "not local", not "staging".

        ``AppEnv`` is ``local | staging | demo``, so ``demo`` is a second non-local
        value that has to be covered. Asserting only staging would leave a gap for
        whatever environment is added next.
        """
        for app_env in ("staging", "demo"):
            settings = Settings(
                app_env=app_env,  # type: ignore[arg-type]
                database_url="postgresql+psycopg://agentpay:secret@db:5432/agentpay",
                db_password="secret",
            )
            with pytest.raises(ValueError, match=app_env):
                settings.validate_datastore_for_env()

    def test_the_error_says_what_would_have_been_lost(self) -> None:
        """The message has to say what the operator is about to lose.

        "Connection failed" invites a retry. Naming the consequence -- an apparently
        healthy service serving an empty catalogue -- is what makes this a decision
        rather than something to work around.
        """
        settings = Settings(
            app_env="staging",
            database_url="postgresql+psycopg://agentpay:pw@db:5432/agentpay",
        )

        with pytest.raises(ValueError) as excinfo:
            settings.validate_datastore_for_env()

        message = str(excinfo.value).lower()
        assert "empty catalog" in message
        assert "losing every write" in message

    def test_an_explicit_sqlite_url_is_allowed_anywhere(self) -> None:
        """Choosing SQLite on purpose is a valid deployment, unlike falling back to it."""
        settings = Settings(app_env="staging", database_url="sqlite:///./data/local_dev.db")

        settings.validate_datastore_for_env()  # must not raise

    def test_the_fallback_can_be_opted_into_explicitly(self) -> None:
        settings = Settings(
            app_env="staging",
            database_url="postgresql+psycopg://agentpay:agentpay@db:5432/agentpay",
            db_password="agentpay",
            allow_sqlite_fallback=True,
        )

        settings.validate_datastore_for_env()  # must not raise

    def test_local_may_fall_back(self) -> None:
        settings = Settings(
            app_env="local",
            database_url="postgresql+psycopg://agentpay:agentpay@localhost:5432/agentpay",
        )

        settings.validate_datastore_for_env()  # must not raise

    def test_an_empty_password_is_reported_by_name(self) -> None:
        """The message has to name the variable, not just fail.

        An operator reading a startup traceback should not have to know that the
        password is the third field of a DSN to act on it.
        """
        settings = Settings(
            app_env="staging",
            database_url="postgresql+psycopg://agentpay:@db:5432/agentpay",
            db_password="",
        )

        with pytest.raises(ValueError, match="DB_PASSWORD"):
            settings.validate_datastore_for_env()


@pytest.mark.parametrize("declared", ["staging", "demo"])
def test_a_declared_non_local_environment_cannot_start_without_the_opt_in(
    declared: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment that says it is staging must not silently boot as local.

    Without the credential opt-in the whole environment is discarded, so a real
    deployment's ``APP_ENV=staging`` and ``DATABASE_URL`` used to vanish: the
    process started as ``local`` (which also switched off ``Secure`` on the
    session cookie and made ``validate_for_env`` return before checking
    anything) against the built-in localhost database. Refusing to start is the
    only safe answer.
    """
    monkeypatch.delenv(LIVE_OPT_IN_VAR, raising=False)
    monkeypatch.setenv("APP_ENV", declared)

    with pytest.raises(ValueError, match=LIVE_OPT_IN_VAR):
        Settings()


def test_a_local_declaration_still_boots_without_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opt-in exists to stop ambient credentials leaking in, not to stop a
    clean clone from running offline."""
    monkeypatch.delenv(LIVE_OPT_IN_VAR, raising=False)
    monkeypatch.setenv("APP_ENV", "local")

    settings = Settings()

    assert settings.app_env == "local"
    assert settings.payment_provider == "fake"
    assert settings.session_cookie_secure is False


def test_the_opt_in_reinstates_the_full_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the opt-in set, ambient configuration is read again — including
    ``APP_ENV`` itself, which is what makes the previous test's guard necessary."""
    monkeypatch.setenv(LIVE_OPT_IN_VAR, "1")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.internal:5432/shop")

    settings = Settings()

    assert settings.app_env == "staging"
    assert settings.database_url == "postgresql+psycopg://u:p@db.internal:5432/shop"
    assert settings.session_cookie_secure is True


def test_init_arguments_beat_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit construction is how the test suite injects settings, and it has
    to keep working regardless of what the ambient environment happens to say."""
    monkeypatch.delenv(LIVE_OPT_IN_VAR, raising=False)
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ValueError):
        # Even an explicit app_env cannot bypass the guard: the environment said
        # staging and no credential opt-in was given.
        Settings(app_env="local")

    monkeypatch.setenv(LIVE_OPT_IN_VAR, "1")
    assert Settings(app_env="local").app_env == "local"


def test_the_singleton_does_not_leak_between_opt_in_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get_settings` is `lru_cache`d, and these tests move the very variable
    that decides which fields it reads.

    A test that sets the opt-in and reads the singleton leaves it cached with
    environment-derived values, and every later test in the session then sees
    those instead of its own defaults. It surfaced as unrelated failures
    elsewhere in the suite -- placeholder-secret checks and loopback exemptions
    that passed in isolation and failed in a full run. Each test that moves the
    opt-in therefore clears the cache on the way in *and* on the way out.
    """
    monkeypatch.setenv(LIVE_OPT_IN_VAR, "1")
    get_settings.cache_clear()
    try:
        assert get_settings().app_env == os.environ.get("APP_ENV", "local")
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv(LIVE_OPT_IN_VAR, "0")
    get_settings.cache_clear()
    assert get_settings().app_env == "local", "a leaked cache entry outlived the opt-in"


def test_the_opt_in_variable_is_the_documented_one() -> None:
    """Guards against the name drifting away from what .env.example documents."""
    assert LIVE_OPT_IN_VAR == "ALLOW_LIVE_CREDENTIALS"


class TestLoopbackDetection:
    """This answer decides whether a credential is required, so a false positive
    means an unauthenticated call to a metered endpoint."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434/v1",
            "http://LOCALHOST:11434/v1",
            "http://127.0.0.1:8000/v1",
            "http://127.0.0.5/v1",
            "http://[::1]:11434/v1",
            "https://localhost/v1",
        ],
    )
    def test_this_host_is_recognised(self, url: str) -> None:
        assert is_loopback_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://models.example.invalid/v1",
            # The two cases a substring test gets wrong, in both directions.
            "https://localhost.example.invalid/v1",
            "https://models.example.invalid/v1?upstream=127.0.0.1",
            "https://127.0.0.1.example.invalid/v1",
            "",
            "not-a-url",
        ],
    )
    def test_everything_else_is_not(self, url: str) -> None:
        assert is_loopback_url(url) is False


class TestGuardProviderConfig:
    """The guard is configured on its own, not off the reasoning model's settings.

    Borrowing them is what made `/api/explore` cost two provider calls instead of
    one, with no way to disable the second short of disabling intent extraction.
    """

    def test_the_guard_is_free_by_default(self) -> None:
        settings = Settings()

        assert settings.guard_provider == "heuristic"
        assert settings.guard_api_key == ""

    def test_local_defaults_point_at_this_host(self) -> None:
        """The zero-cost model-backed path needs no credential and no egress."""
        settings = Settings()

        assert settings.guard_base_url == "http://localhost:11434/v1"
        assert settings.guard_model_name == "llama-guard3:1b"

    def test_guard_timeout_is_bounded(self) -> None:
        """The guard sits in front of every prompt, so an unbounded timeout is the
        latency of the whole request."""
        assert Settings().guard_timeout_seconds == 5.0

        with pytest.raises(ValidationError):
            Settings(guard_timeout_seconds=0)
        with pytest.raises(ValidationError):
            Settings(guard_timeout_seconds=120)

    def test_unknown_guard_provider_is_refused_at_startup(self) -> None:
        with pytest.raises(ValidationError):
            Settings(guard_provider="groq")

    def test_guard_settings_are_independent_of_the_model_settings(self) -> None:
        """Configuring the reasoning model must move nothing on the guard."""
        settings = Settings(
            model_provider="openai_compatible",
            model_api_key="reasoning-key-not-a-real-credential",
            model_base_url="https://api.groq.com/openai/v1",
            model_guard_name="meta-llama/llama-prompt-guard-2-86m",
        )

        assert settings.guard_provider == "heuristic"
        assert settings.guard_api_key == ""
        assert settings.guard_base_url == "http://localhost:11434/v1"
        assert settings.guard_model_name == "llama-guard3:1b"


class TestSearchProviderConfig:
    """ADR-0009. The search layer must be off by default and bounded when on."""

    def test_search_is_off_by_default(self) -> None:
        """The golden path and the test suite must never touch the network."""
        assert Settings().search_provider == "null"

    def test_research_limits_have_safe_defaults(self) -> None:
        """Upstream engines throttle a self-hosted SearXNG instance, so these are
        correctness limits, not politeness."""
        settings = Settings()

        assert settings.research_max_searches == 3
        assert settings.research_max_pages == 5
        assert settings.research_max_steps == 6

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("research_max_searches", 100),
            ("research_max_pages", 500),
            ("research_max_steps", 1000),
            ("research_page_timeout_seconds", 600),
        ],
    )
    def test_research_limits_cannot_be_raised_without_bound(self, field: str, value: int) -> None:
        """A misconfigured limit is how a research loop becomes a scraper and gets
        the instance blocked mid-demo."""
        with pytest.raises(ValidationError):
            Settings(**{field: value})

    def test_searxng_base_url_is_configuration_not_a_parameter(self) -> None:
        """Documents the security boundary from ADR-0009: the search host comes
        from config only. If this ever becomes request-derived, the SSRF control
        on `open_url` is void, because SearXNG lives on a private address."""
        settings = Settings(searxng_base_url="http://localhost:8080")

        assert settings.searxng_base_url == "http://localhost:8080"
        # There is deliberately no API accepting a caller-supplied search host.
        assert not hasattr(settings, "search_host_override")
