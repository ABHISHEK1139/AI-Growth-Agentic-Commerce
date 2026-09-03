"""Configuration invariants that protect the demo and the credentials."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.api.config import Settings, is_loopback_url


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
