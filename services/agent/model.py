"""Model gateway: the provider protocol, a deterministic mock, and one
OpenAI-compatible HTTP adapter (Task 28 / Task 32, Requirement 22.15, 22.16).

ADR-0010 committed this system to a single provider spoken over the OpenAI chat
completions wire format and selected entirely by configuration. The adapter did
not hold up its end. The vendor endpoint appeared as a string literal in three
places -- a constructor default and two fallbacks -- so a request could still be
built against one vendor's host no matter what ``MODEL_BASE_URL`` said, and the
`grok` selector rewrote the base URL to a second hardcoded host. A credential was
also invented (``"local-key"``) when none was configured, so a local server
received ``Authorization: Bearer local-key`` and the call was recorded as
authenticated when nothing had authenticated.

All of it is gone. :func:`chat_completions_url` is the only place a request URL is
built and it has no default to fall back to; ``Authorization`` is sent only when a
credential actually exists; and the timeout, retry budget, and token ceiling apply
identically to an endpoint on this host and to a hosted one.

Structured output is the other half. A local model is far likelier than a hosted
one to answer with prose or a fenced code block, so :func:`parse_json_object`
recovers the object the model emitted and *raises* when there is none. Returning
an empty mapping instead would reach the catalog as an intent carrying no
constraints, and the buyer would be shown an unfiltered result set that looks like
an answer to the question they asked.

``tests/unit/test_model_provider.py`` scans this module and fails if a host
literal reappears.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from packages.config.providers import ModelGatewayConfig
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.urls import is_loopback_url

#: The chat-completions route, relative to whatever base URL is configured. Every
#: OpenAI-compatible endpoint serves it at this path; the host never belongs here.
CHAT_COMPLETIONS_PATH = "chat/completions"

#: The ``MODEL_PROVIDER`` values that mean "reach an OpenAI-compatible endpoint".
#: All three behave identically -- the base URL is what distinguishes them, which
#: is the whole point of ADR-0010.
HTTP_PROVIDERS: frozenset[str] = frozenset({"openai_compatible", "groq", "grok"})

#: First ``{`` to last ``}``. Used only after the whole body fails to decode, to
#: lift an object out of a fenced code block or out of surrounding prose.
_JSON_OBJECT_SPAN = re.compile(r"\{.*\}", re.DOTALL)


def chat_completions_url(base_url: str) -> str:
    """Build the chat-completions URL for a configured base URL.

    The single place a request URL is constructed. There is deliberately no
    default: a default here is exactly what pinned every deployment to one vendor
    while the setting that was supposed to control it looked honoured.
    """
    trimmed = base_url.strip().rstrip("/")
    if not trimmed:
        raise DomainError(
            "The model endpoint is not configured.",
            code=ErrorCode.INTERNAL_ERROR,
            details={"setting": "MODEL_BASE_URL"},
        )
    return f"{trimmed}/{CHAT_COMPLETIONS_PATH}"


def parse_json_object(content: str) -> dict[str, Any]:
    """Recover the JSON object a completion carries, or refuse.

    Two attempts, both reading only what the model actually said: the whole body,
    then the first ``{...}`` span in it, which covers a fenced code block and a
    sentence of preamble. Nothing is guessed and no field is supplied.

    Refusing is the point. An unreadable body used to become ``None``, then an
    empty mapping one caller up, then an intent with every constraint absent --
    a search with no filters, presented as a result. A typed error is the honest
    answer, and it is retryable because resampling often does produce JSON.
    """
    stripped = content.strip()
    candidates = [stripped]
    span = _JSON_OBJECT_SPAN.search(stripped)
    if span is not None and span.group(0) != stripped:
        candidates.append(span.group(0))

    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(decoded, dict):
            return decoded

    raise DomainError(
        "The model did not return a JSON object for a structured request.",
        code=ErrorCode.SERVICE_UNAVAILABLE,
        # The body itself is not echoed: it is model prose derived from buyer
        # text, and it belongs in a log line rather than in a response.
        details={"reason": "no_json_object", "content_length": len(stripped)},
    )


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str
    parsed_json: dict[str, Any] | None = None
    model_version: str = "mock-model-v1"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0


class ModelProvider(Protocol):
    """Protocol for language model interactions."""

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> ModelResponse: ...

    def embed(self, text: str) -> list[float]: ...


class MockModelProvider:
    """Deterministic mock provider returning valid structured schemas."""

    def __init__(self, model_version: str = "mock-model-v1") -> None:
        self.model_version = model_version
        self.behavior: str = "success"

    def set_behavior(self, behavior: str) -> None:
        self.behavior = behavior

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        if self.behavior == "timeout":
            raise DomainError("Model provider timed out.", code=ErrorCode.GATEWAY_TIMEOUT)
        if self.behavior == "error":
            raise DomainError("Model provider error.", code=ErrorCode.SERVICE_UNAVAILABLE)

        # Default mock intent extraction JSON
        p_lower = prompt.lower()
        mock_intent = {
            "schema_version": "1.0",
            "query": prompt[:100],
            "category": "laptop"
            if ("laptop" in p_lower or "ultrabook" in p_lower or "computer" in p_lower)
            else "smartphone",
            "financial": {"budget_minor": 7000000, "currency": "INR"},
            "min_memory_gb": 16 if "16" in prompt else (32 if "32" in prompt else None),
            "min_storage_gb": 512 if "512" in prompt else None,
            "max_delivery_days": 3
            if (
                "day" in p_lower
                or "delivery" in p_lower
                or "ssd" in p_lower
                or "programming" in p_lower
            )
            else None,
            "quantity": 2 if ("two" in p_lower or " 2 " in p_lower) else 1,
        }

        return ModelResponse(
            content=json.dumps(mock_intent),
            parsed_json=mock_intent,
            model_version=self.model_version,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=50,
            latency_ms=12.5,
        )

    def embed(self, text: str) -> list[float]:
        # Return 384-dim dummy unit vector
        vec = [0.0] * 384
        vec[0] = 1.0
        return vec


class GroqModelProvider:
    """The OpenAI-compatible chat-completions adapter.

    One adapter for every compatible endpoint, hosted or on this host, because
    they all speak the same wire format (ADR-0010). Nothing in this class knows a
    vendor: ``base_url`` and ``model_name`` are required arguments with no
    defaults, and the timeout, retry budget, and token ceiling arrive the same way.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        api_key: str = "",
        timeout_seconds: float = 30.0,
        max_tokens: int | None = None,
        max_retries: int = 0,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.model_name = model_name
        self.api_key = api_key or ""
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.max_retries = max(max_retries, 0)

    @property
    def endpoint(self) -> str:
        """The URL this provider posts to. Derived, never stored as a literal."""
        return chat_completions_url(self.base_url)

    @property
    def is_local(self) -> bool:
        """Whether the configured endpoint runs on this host."""
        return is_loopback_url(self.base_url)

    def _headers(self) -> dict[str, str]:
        """Request headers. ``Authorization`` appears only with a real credential.

        An empty ``Bearer`` is worse than no header: a local server ignores it, so
        the request succeeds while every log and trace records an authenticated
        call that never authenticated anything.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(
        self,
        prompt: str,
        *,
        system_prompt: str | None,
        schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        sys_msg = system_prompt
        if (
            schema
            and (not sys_msg or "json" not in sys_msg.lower())
            and "json" not in prompt.lower()
        ):
            sys_msg = (
                sys_msg + "\n" if sys_msg else ""
            ) + "You are a structured data extractor. Respond in valid JSON format only."

        if sys_msg:
            messages.append({"role": "system", "content": sys_msg})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.1,
        }
        # Applied to every endpoint, not only a hosted one: an unbounded
        # completion from a local model is the same runaway cost in latency that a
        # hosted one is in tokens.
        if self.max_tokens is not None and self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        if schema:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """POST once, then up to ``max_retries`` more times on a transport failure.

        Only the conditions that are transient by definition are retried: a
        timeout and a dropped connection. A non-200 is not, because its body
        carries the endpoint's own explanation and this module does not read it --
        retrying would as often repeat a rejected model name as recover from an
        outage. The budget is ``MODEL_MAX_RETRIES`` and it applies identically
        wherever the endpoint lives.
        """
        endpoint = self.endpoint
        last_error: Exception | None = None

        for _attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(endpoint, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
                continue
            except httpx.TransportError as exc:
                last_error = exc
                continue
            return self._decode(response)

        if isinstance(last_error, httpx.TimeoutException):
            raise DomainError(
                "Model provider timed out.", code=ErrorCode.GATEWAY_TIMEOUT
            ) from last_error
        raise DomainError(
            "The model endpoint could not be reached.",
            code=ErrorCode.SERVICE_UNAVAILABLE,
            # The host is deliberately absent: a registry message is written for
            # the caller and never names an endpoint.
            details={"error_kind": type(last_error).__name__},
        ) from last_error

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        """Turn a response into the completion body, or raise a typed error."""
        if response.status_code != 200:
            raise DomainError(
                "The model endpoint rejected the request.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
                details={"status": response.status_code},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise DomainError(
                "The model endpoint returned a body that is not JSON.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
            ) from exc
        if not isinstance(body, dict):
            raise DomainError(
                "The model endpoint returned an unexpected response shape.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
            )
        return body

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        if not self.api_key and not self.is_local:
            # Refused rather than attempted. An unauthenticated call to a metered
            # endpoint is a request that cannot succeed and should not be made;
            # `validate_for_env` catches the same misconfiguration at startup.
            raise DomainError(
                "The model endpoint is not on this host and no API key is configured.",
                code=ErrorCode.INTERNAL_ERROR,
                details={"setting": "MODEL_API_KEY"},
            )

        payload = self._payload(prompt, system_prompt=system_prompt, schema=schema)
        headers = self._headers()

        start = datetime.now(UTC)
        body = self._post(payload, headers)
        latency = (datetime.now(UTC) - start).total_seconds() * 1000

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DomainError(
                "The model endpoint returned no completion.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
            ) from exc
        if not isinstance(content, str):
            raise DomainError(
                "The model endpoint returned a non-text completion.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
            )

        # A structured request that cannot be parsed is a failure, not an empty
        # result. See `parse_json_object`.
        parsed = parse_json_object(content) if schema else None

        usage = body.get("usage") or {}
        return ModelResponse(
            content=content,
            parsed_json=parsed,
            model_version=self.model_name,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency,
        )

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * 384
        vec[0] = 1.0
        return vec


_MOCK_MODEL_INSTANCE = MockModelProvider()


def get_model_provider(config: ModelGatewayConfig | Any | None = None) -> ModelProvider:
    """Resolve the configured model provider (defaults to the deterministic mock)."""
    if (
        config is not None
        and hasattr(config, "model_gateway_config")
        and callable(config.model_gateway_config)
    ):
        cfg = config.model_gateway_config()
    elif isinstance(config, ModelGatewayConfig):
        cfg = config
    elif config is not None and hasattr(config, "model_provider"):
        cfg = ModelGatewayConfig(
            provider=getattr(config, "model_provider", "mock"),
            base_url=getattr(config, "model_base_url", ""),
            model_name=getattr(config, "model_name", ""),
            api_key=getattr(config, "model_api_key", ""),
            timeout_seconds=float(getattr(config, "model_timeout_seconds", 30.0)),
            max_tokens=int(getattr(config, "model_max_tokens", 2048)),
            max_retries=int(getattr(config, "model_max_retries", 2)),
        )
    else:
        cfg = config or ModelGatewayConfig()

    if cfg.provider not in HTTP_PROVIDERS:
        return _MOCK_MODEL_INSTANCE

    base_url = cfg.base_url.strip()
    model_name = cfg.model_name.strip()
    if not base_url or not model_name:
        return _MOCK_MODEL_INSTANCE
    if not cfg.api_key and not is_loopback_url(base_url):
        # `validate_for_env` rejects this outside local; locally, falling back
        # keeps the golden path running instead of failing every request.
        return _MOCK_MODEL_INSTANCE

    return GroqModelProvider(
        base_url=base_url,
        model_name=model_name,
        api_key=cfg.api_key,
        timeout_seconds=cfg.timeout_seconds,
        max_tokens=cfg.max_tokens,
        max_retries=cfg.max_retries,
    )
