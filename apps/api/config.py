"""Application configuration.

Every setting is read from the environment exactly once and exposed through a
cached ``get_settings()`` accessor. Two defaults matter more than the rest:

* ``payment_provider`` defaults to ``fake``
* ``model_provider`` defaults to ``mock``

Together they guarantee that a clean clone runs the entire golden path and the
entire test suite without a single credential.

This class is delivery configuration: it reads the environment, and it holds CORS
origins, rate-limit budgets, and session-cookie policy alongside the values a
domain seam needs. So the domain does not receive it. Each ``*_config()`` builder
below projects the subset one seam actually reads onto a frozen value object from
:mod:`packages.config.providers`, and the domain is typed against that. Four
modules under ``services/`` used to import this one, which ran the dependency from
the domain into the application.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.config.providers import (
    GuardConfig,
    GuardProviderName,
    ModelGatewayConfig,
    ModelProviderName,
    PaymentProviderConfig,
    PaymentProviderName,
    SearchProviderName,
)
from packages.schemas.v1 import CurrencyCode
from packages.urls import LOOPBACK_HOSTNAMES, is_loopback_url

AppEnv = Literal["local", "staging", "demo"]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Re-exported. The provider-name literals and the loopback predicate now live in
#: ``packages`` so a domain service can reach them without importing the API
#: layer; they stay importable from here because this is where a reader looks for
#: anything configuration-shaped.
__all__ = [
    "LOOPBACK_HOSTNAMES",
    "AppEnv",
    "GuardProviderName",
    "ModelProviderName",
    "PaymentProviderName",
    "SearchProviderName",
    "Settings",
    "get_settings",
    "is_loopback_url",
]


class Settings(BaseSettings):
    """Typed view of the process environment."""

    _live_env_opt_in = os.environ.get("ALLOW_LIVE_CREDENTIALS") == "1"
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env") if _live_env_opt_in else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Require explicit live opt-in before reading process or .env credentials.

        The default behavior is intentionally safe: a developer shell that happens to
        export real values must not silently influence a fresh app run unless the
        process has opted in with ``ALLOW_LIVE_CREDENTIALS=1``.
        """
        if os.environ.get("ALLOW_LIVE_CREDENTIALS") == "1":
            return init_settings, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)

    # --- Application ------------------------------------------------------
    app_env: AppEnv = "local"
    app_name: str = "agentpay"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"  # noqa: S104 - container binding, published selectively
    api_port: int = 8000

    # --- Datastores -------------------------------------------------------
    database_url: str = "postgresql+psycopg://agentpay:agentpay@localhost:5432/agentpay"
    redis_url: str = "redis://localhost:6379/0"

    # --- Rate limiting ----------------------------------------------------
    # The default applies only to routes without their own declared rule; see
    # `apps.api.middleware.ratelimit`. The timeout is short and the cooldown
    # exists because the limiter fails open: if Redis is unreachable, requests
    # are allowed, and neither the failure nor the recovery attempt may add
    # meaningful latency to the payment path.
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = Field(default=120, ge=1)
    rate_limit_redis_timeout_seconds: float = Field(default=0.25, gt=0, le=5)
    rate_limit_degraded_cooldown_seconds: float = Field(default=10.0, ge=0)

    # --- Security ---------------------------------------------------------
    # These placeholders exist so a clean clone boots. `validate_for_env()`
    # refuses to let them reach staging or a demo.
    jwt_secret: str = "dev-only-change-me-generate-a-real-secret-before-staging"  # noqa: S105
    session_secret: str = "dev-only-change-me-too"  # noqa: S105
    # Two secrets, two credential families, on purpose: `jwt_secret` signs the
    # scoped bearer tokens external agents carry, `session_secret` signs the web
    # session. Neither can be presented where the other belongs even if one leaks.
    #
    # Both TTLs are bounded here rather than trusted from the environment. A bearer
    # token is the credential an external process holds on disk; the upper bound is
    # what keeps "long-lived" from meaning "until someone notices".
    access_token_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    session_ttl_seconds: int = Field(default=86_400, ge=300, le=2_592_000)
    cors_allow_origins: str = "http://localhost:3000"

    # --- Payment provider -------------------------------------------------
    payment_provider: PaymentProviderName = "fake"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    # Provider-neutral, because the adapter interface is. Applies to every
    # outbound provider call, so it is short enough that a stalled provider
    # cannot hold a payment request open indefinitely.
    payment_provider_timeout_seconds: int = 10

    # --- Model provider ---------------------------------------------------
    model_provider: ModelProviderName = "mock"
    model_api_key: str = ""
    model_base_url: str = ""

    # Three model roles, three settings. None is a constant in code, so a
    # deployment can point each at a different model or leave it unset.
    model_name: str = ""
    model_guard_name: str = ""
    model_moderation_name: str = ""

    model_timeout_seconds: int = 30
    model_max_tokens: int = 2048
    model_max_retries: int = 2

    # Capability flags rather than vendor branches. Compatible endpoints
    # differ in detail; a new provider should be a settings entry, not an if.
    model_supports_json_schema: bool = True
    model_supports_tool_calling: bool = True
    # Strict structured-output mode requires every declared property to be
    # listed in `required`, so optional fields must be nullable-typed rather
    # than omitted. Verified against the live endpoint.
    model_strict_schema_requires_all_required: bool = True

    # --- Prompt guard -----------------------------------------------------
    # The guard has its own provider, endpoint, model, and credential. It used to
    # borrow the reasoning model's, so setting MODEL_API_KEY silently enabled a
    # second billed request on every query. The two are separate concerns and are
    # now configured separately.
    #
    # The selection is a cost decision as much as a security one:
    #   heuristic  in-process only, never leaves the process, costs nothing
    #   local      an OpenAI-compatible endpoint on this host, costs nothing
    #   remote     a metered provider, billed on every request
    #
    # `heuristic` is the default, so a clean clone makes no guard call at all.
    # Layer 1 (the length bound and the injection patterns) runs first in every
    # mode regardless, because it is free.
    guard_provider: GuardProviderName = "heuristic"
    # Ollama's OpenAI-compatible route. Configuration only, never request-derived.
    guard_base_url: str = "http://localhost:11434/v1"
    guard_model_name: str = "llama-guard3:1b"
    # Used by the `remote` provider only. When empty, no Authorization header is
    # sent at all, which is what the local path wants.
    guard_api_key: str = ""
    # Short and bounded: the guard sits in front of every prompt, so a stalled
    # classifier must not become the latency of the request.
    guard_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    # --- Web search (research agent, Task 36) -----------------------------
    # `null` keeps the golden path and the test suite entirely off the network.
    # `searxng_base_url` is configuration and is never derived from a request, a
    # model output, or product text. See docs/adr/0009-web-search-provider.md.
    search_provider: SearchProviderName = "null"
    searxng_base_url: str = "http://localhost:8080"
    research_max_searches: int = Field(default=3, ge=1, le=10)
    research_max_pages: int = Field(default=5, ge=1, le=20)
    research_max_steps: int = Field(default=6, ge=1, le=20)
    research_page_timeout_seconds: int = Field(default=10, ge=1, le=60)
    research_max_page_bytes: int = Field(default=2_000_000, ge=1024)
    research_cache_ttl_seconds: int = Field(default=86_400, ge=0)

    # --- Object storage ---------------------------------------------------
    object_storage_endpoint: str = "http://localhost:9000"
    object_storage_bucket: str = "agentpay-local"
    object_storage_access_key: str = ""
    object_storage_secret_key: str = ""

    # --- Dataset pipeline -------------------------------------------------
    agentpay_raw_dir: Path = REPO_ROOT.parent / "datasets"
    agentpay_out_dir: Path = REPO_ROOT / "data" / "out"
    max_lines_debug: int | None = None

    # --- Commerce defaults ------------------------------------------------
    # Typed as the schema's currency literal rather than `str`, so a deployment
    # that sets an unsupported currency fails at startup instead of producing a
    # capability document and a price snapshot that disagree about what the
    # amounts mean.
    default_currency: CurrencyCode = "INR"
    default_merchant_id: str = "merchant_demo"
    checkout_expiry_seconds: int = 900
    authorization_expiry_seconds: int = 900
    max_search_results: int = Field(default=20, ge=1, le=50)
    auto_approval_limit_minor: int = Field(default=500_000, ge=0)
    max_transaction_amount_minor: int = Field(default=7_000_000, ge=0)

    @field_validator("max_lines_debug", mode="before")
    @classmethod
    def _blank_means_no_cap(cls, value: object) -> object:
        """Treat an empty environment variable as "no cap" rather than an error."""
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        candidate = value.upper()
        if candidate not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return candidate

    @property
    def cors_origins(self) -> list[str]:
        """CORS origins as a list. Never a wildcard outside local development."""
        origins = [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]
        if "*" in origins and self.app_env != "local":
            raise ValueError("Wildcard CORS origin is not permitted outside local development")
        return origins

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def session_cookie_secure(self) -> bool:
        """Whether the session cookie is HTTPS-only.

        False only in local development, where the frontend runs on plain
        ``http://localhost`` and a ``Secure`` cookie would simply never be sent.
        Staging and demo serve over HTTPS, so there the flag is on.
        """
        return not self.is_local

    @property
    def payment_is_test_mode(self) -> bool:
        """Whether no real money can move through the configured provider.

        The fake provider is always test mode. Razorpay is test mode when its key
        id carries the provider's own ``rzp_test_`` prefix, which is the only
        signal available without calling out to the provider.

        Deliberately not derived from ``app_env``: this system declares no
        ``production`` environment, so a comparison against one would answer
        "test mode" for every deployment including a demo holding live keys.
        """
        if self.payment_provider != "razorpay":
            return True
        return self.razorpay_key_id.startswith("rzp_test_")

    def razorpay_is_configured(self) -> bool:
        """Whether real Razorpay credentials are present.

        Deliberately checks for presence only; the values are never logged or
        returned to a client.
        """
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    # --- Projections onto the domain's own configuration types -------------
    #
    # One builder per seam. Each is the whole of what that seam reads, so the
    # domain never receives a CORS list or a rate-limit budget, and a reviewer can
    # see from the return type which settings a service can possibly depend on.
    # The composition layer calls these; nothing under ``services/`` may.

    def model_gateway_config(self) -> ModelGatewayConfig:
        """Configuration for :func:`services.agent.model.get_model_provider`."""
        return ModelGatewayConfig(
            provider=self.model_provider,
            base_url=self.model_base_url,
            model_name=self.model_name,
            api_key=self.model_api_key,
            timeout_seconds=float(self.model_timeout_seconds),
            max_tokens=self.model_max_tokens,
            max_retries=self.model_max_retries,
        )

    def guard_config(self) -> GuardConfig:
        """Configuration for :class:`services.agent.guard.PromptSafetyClassifier`.

        Only ``guard_*`` settings appear here. The projection is what makes the
        separation from the reasoning model structural: there is no field on
        :class:`~packages.config.providers.GuardConfig` that ``model_api_key``
        could be assigned to.
        """
        return GuardConfig(
            provider=self.guard_provider,
            base_url=self.guard_base_url,
            model_name=self.guard_model_name,
            api_key=self.guard_api_key,
            timeout_seconds=self.guard_timeout_seconds,
        )

    def payment_provider_config(self) -> PaymentProviderConfig:
        """Configuration for :func:`services.payments.provider.get_payment_provider`."""
        return PaymentProviderConfig(
            provider=self.payment_provider,
            razorpay_key_id=self.razorpay_key_id,
            razorpay_key_secret=self.razorpay_key_secret,
            razorpay_webhook_secret=self.razorpay_webhook_secret,
            timeout_seconds=self.payment_provider_timeout_seconds,
        )

    def validate_for_env(self) -> None:
        """Refuse to start with development placeholders outside local.

        Called from the application factory. The failure mode this prevents is a
        demo deployment that silently signs sessions with a secret committed to a
        public template.
        """
        if self.is_local:
            return

        problems: list[str] = []
        if "change-me" in self.jwt_secret:
            problems.append("JWT_SECRET is still the template placeholder")
        if "change-me" in self.session_secret:
            problems.append("SESSION_SECRET is still the template placeholder")
        if self.payment_provider == "razorpay" and not self.razorpay_is_configured():
            problems.append("PAYMENT_PROVIDER=razorpay but no key id/secret is set")
        if self.payment_provider == "razorpay" and not self.razorpay_webhook_secret:
            problems.append("PAYMENT_PROVIDER=razorpay but RAZORPAY_WEBHOOK_SECRET is empty")
        if self.guard_provider == "remote" and not self.guard_api_key:
            problems.append("GUARD_PROVIDER=remote but GUARD_API_KEY is empty")
        if self.model_provider in ("openai_compatible", "groq", "grok"):
            if not self.model_base_url:
                problems.append(f"MODEL_PROVIDER={self.model_provider} but MODEL_BASE_URL is empty")
            if not self.model_name:
                problems.append(f"MODEL_PROVIDER={self.model_provider} but MODEL_NAME is empty")
            # A credential is required for an endpoint this deployment does not
            # own, and meaningless for one running on this host: an Ollama or
            # llama.cpp server has nothing to authenticate against. Demanding a
            # key regardless refused a valid local configuration at startup,
            # which is what made `MODEL_BASE_URL=http://localhost:11434/v1`
            # unusable outside APP_ENV=local.
            if not self.model_api_key and not is_loopback_url(self.model_base_url):
                problems.append(
                    f"MODEL_PROVIDER={self.model_provider} but MODEL_API_KEY is empty "
                    "and MODEL_BASE_URL does not point at this host"
                )

        if problems:
            raise ValueError(
                f"Unsafe configuration for APP_ENV={self.app_env}: " + "; ".join(problems)
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
