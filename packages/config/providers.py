"""What each provider seam needs in order to be configured.

Four things used to reach ``apps.api.config`` from inside ``services/``: the model
gateway, the prompt guard, the payment provider resolver, and the research
orchestrator. All four wanted a handful of scalars and got the whole delivery
configuration object, which is how the domain ended up importing the application.

Each class below is the *complete* set of values one seam reads — nothing wider,
so a reviewer can see the blast radius of a setting from the type alone. They are
frozen, so a value cannot be edited after the composition layer supplied it, and
every default matches the corresponding ``Settings`` default, so constructing one
with no arguments describes a clean clone: the deterministic mock model, the free
in-process guard, and the fake payment provider.

The research orchestrator is not here. It reads two values, and two explicit
keyword arguments say more at the call site than a class would.

The provider-name literals live here too. Which model gateways exist and which
payment providers exist are facts about the domain, not about how the process is
configured; ``Settings`` annotates its fields with these, so an unsupported value
still fails at startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "DEFAULT_GUARD_BASE_URL",
    "DEFAULT_GUARD_MODEL",
    "DEFAULT_GUARD_TIMEOUT_SECONDS",
    "GuardConfig",
    "GuardProviderName",
    "ModelGatewayConfig",
    "ModelProviderName",
    "PaymentProviderConfig",
    "PaymentProviderName",
    "SearchProviderName",
]

PaymentProviderName = Literal["fake", "razorpay"]
ModelProviderName = Literal["mock", "openai_compatible", "groq", "grok", "ollama", "local"]
# "auto" resolves to the DuckDuckGo fallback (see services.research.tools.search);
# "duckduckgo" selects it explicitly. The .env shipped with SEARCH_PROVIDER=duckduckgo
# while this literal omitted it, which crashed Settings() at startup.
SearchProviderName = Literal["null", "searxng", "duckduckgo", "auto"]
GuardProviderName = Literal["heuristic", "local", "remote"]

#: Layer 2 defaults for the prompt guard. Declared once here and reused both as
#: :class:`GuardConfig` field defaults and as the direct-call defaults in
#: :mod:`services.agent.guard`, so the free local endpoint is spelled in one place.
DEFAULT_GUARD_BASE_URL = "http://localhost:11434/v1"
DEFAULT_GUARD_MODEL = "llama-guard3:1b"
DEFAULT_GUARD_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ModelGatewayConfig:
    """Everything the model gateway reads in order to select and drive a provider.

    ``provider`` chooses between the deterministic mock and the OpenAI-compatible
    HTTP adapter; ``base_url`` alone distinguishes a hosted endpoint from one on
    this host, which is the commitment ADR-0010 made. The three bounds apply
    identically to both, because an unbounded completion from a local model is the
    same runaway cost in latency that a hosted one is in tokens.
    """

    provider: ModelProviderName = "mock"
    base_url: str = ""
    model_name: str = ""
    api_key: str = ""
    timeout_seconds: float = 30.0
    max_tokens: int = 2048
    max_retries: int = 2


@dataclass(frozen=True, slots=True)
class GuardConfig:
    """Everything the prompt guard reads.

    Deliberately holds no model-gateway value. The guard used to borrow
    ``model_api_key``, ``model_base_url``, and ``model_guard_name``, so configuring
    the *reasoning* model silently bought a second billed request in front of every
    query. A type that cannot express those three is a stronger guarantee than a
    comment asking future readers not to reach for them.
    """

    provider: GuardProviderName = "heuristic"
    base_url: str = DEFAULT_GUARD_BASE_URL
    model_name: str = DEFAULT_GUARD_MODEL
    api_key: str = ""
    timeout_seconds: float = DEFAULT_GUARD_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class PaymentProviderConfig:
    """Everything the payment provider resolver reads.

    ``timeout_seconds`` is provider-neutral because the adapter interface is: a
    second provider reuses it rather than adding a vendor-named twin.
    """

    provider: PaymentProviderName = "fake"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    timeout_seconds: int = 10

    @property
    def razorpay_is_configured(self) -> bool:
        """Whether real Razorpay credentials are present.

        Presence only; the values are never logged or returned to a client.
        """
        return bool(self.razorpay_key_id and self.razorpay_key_secret)
