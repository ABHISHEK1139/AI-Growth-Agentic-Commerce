"""Where the reasoning model's requests actually go, and what they carry.

The defect these tests pin down: the vendor endpoint was a string literal in three
places in :mod:`services.agent.model` -- a constructor default and two fallbacks --
so a request could be built against one vendor's host regardless of
``MODEL_BASE_URL``, and the `grok` selector rewrote the base URL to a second
hardcoded host. A credential was invented too (``"local-key"``), so an endpoint on
this host received ``Authorization: Bearer local-key`` and the call was recorded as
authenticated when nothing had authenticated.

Assertions are made against the real :class:`httpx.Request` the provider would have
put on the wire, captured at ``httpx.Client.send``, because that is the only place
the URL and the headers exist together.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from apps.api.config import Settings
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.agent.model import (
    GroqModelProvider,
    MockModelProvider,
    chat_completions_url,
    get_model_provider,
    parse_json_object,
)

LOCAL_BASE_URL = "http://localhost:11434/v1"
LOCAL_MODEL = "qwen3.5:4b"
HOSTED_BASE_URL = "https://models.example.invalid/v1"
FAKE_KEY = "not-a-real-credential-just-a-shape"

#: Vendor hosts that must never reappear as literals in the gateway module. The
#: point is not that these particular companies are special -- it is that a host
#: belongs in configuration, and a default here silently pins every deployment.
VENDOR_HOSTS = (
    "api.groq.com",
    "api.openai.com",
    "api.x.ai",
    "api.together.xyz",
    "api.fireworks.ai",
    "openrouter.ai",
    "api.deepinfra.com",
    "api.anthropic.com",
)

JSON_INTENT = '{"category": "laptop", "min_memory_gb": 16}'


class RecordedTransport:
    """Captures outbound model requests instead of making them.

    Installed over ``httpx.Client.send`` rather than as an ``httpx.MockTransport``
    because the provider constructs its own client and there is nowhere to inject
    a transport -- and because what is captured is then the real request, headers
    included.
    """

    def __init__(self, content: str = JSON_INTENT, status_code: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.content = content
        self.status_code = status_code

    @contextmanager
    def patched(self) -> Iterator[RecordedTransport]:
        recorder = self

        def fake_send(client: httpx.Client, request: httpx.Request, **kwargs: object):
            recorder.requests.append(request)
            return httpx.Response(
                recorder.status_code,
                json={
                    "choices": [{"message": {"content": recorder.content}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                },
                request=request,
            )

        with patch.object(httpx.Client, "send", fake_send):
            yield self

    @property
    def count(self) -> int:
        return len(self.requests)

    @property
    def only(self) -> httpx.Request:
        assert self.count == 1, f"expected exactly one model request, saw {self.count}"
        return self.requests[0]


def _provider(**overrides: object) -> GroqModelProvider:
    kwargs: dict[str, object] = {
        "base_url": LOCAL_BASE_URL,
        "model_name": LOCAL_MODEL,
    }
    kwargs.update(overrides)
    return GroqModelProvider(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The request URL comes from configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (HOSTED_BASE_URL, "https://models.example.invalid/v1/chat/completions"),
        (LOCAL_BASE_URL, "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1/chat/completions"),
        ("http://127.0.0.1:8000/v1///", "http://127.0.0.1:8000/v1/chat/completions"),
    ],
)
def test_the_endpoint_is_built_from_the_base_url(base_url: str, expected: str) -> None:
    assert chat_completions_url(base_url) == expected


@pytest.mark.parametrize("base_url", ["", "   ", "/"])
def test_an_unconfigured_base_url_is_refused_rather_than_defaulted(base_url: str) -> None:
    """There is no host to fall back to, which is the entire fix."""
    with pytest.raises(DomainError) as exc_info:
        chat_completions_url(base_url)

    assert exc_info.value.details.get("setting") == "MODEL_BASE_URL"


@pytest.mark.parametrize(
    ("base_url", "api_key", "expected_url"),
    [
        (HOSTED_BASE_URL, FAKE_KEY, "https://models.example.invalid/v1/chat/completions"),
        (LOCAL_BASE_URL, "", "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/v1/", "", "http://localhost:11434/v1/chat/completions"),
    ],
)
def test_the_request_really_goes_to_the_configured_base_url(
    base_url: str, api_key: str, expected_url: str
) -> None:
    recorder = RecordedTransport()
    with recorder.patched():
        _provider(base_url=base_url, api_key=api_key).generate("hi", schema={"type": "object"})

    assert str(recorder.only.url) == expected_url


@pytest.mark.parametrize("vendor_host", VENDOR_HOSTS)
def test_the_gateway_module_holds_no_vendor_host_literal(vendor_host: str) -> None:
    """A static scan across gateway, guard, and config modules, because a default
    endpoint is easy to reintroduce by habit and impossible to notice: every request
    keeps working, just against the wrong host."""
    import apps.api.config as config_module
    import services.agent.guard as guard_module
    import services.agent.model as model_module

    for mod in (model_module, guard_module, config_module):
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert (
            vendor_host not in source
        ), f"Found forbidden vendor host {vendor_host} in {mod.__name__}"


def test_the_gateway_module_holds_no_absolute_url_at_all() -> None:
    """Stronger and simpler than a vendor blocklist: the request path is built from
    a configured base URL and a relative route, so no scheme belongs in the file."""
    import services.agent.model as model_module

    source = Path(model_module.__file__).read_text(encoding="utf-8")
    assert "http://" not in source
    assert "https://" not in source


# ---------------------------------------------------------------------------
# Authorization only when there is something to authorize with
# ---------------------------------------------------------------------------


def test_no_authorization_header_when_no_key_is_configured() -> None:
    """A local Ollama or llama.cpp server has no credential. An empty `Bearer`
    would be accepted and would record the call as authenticated."""
    recorder = RecordedTransport()
    with recorder.patched():
        _provider(api_key="").generate("hi", schema={"type": "object"})

    assert "authorization" not in recorder.only.headers


def test_the_configured_key_is_sent_when_one_exists() -> None:
    recorder = RecordedTransport()
    with recorder.patched():
        _provider(base_url=HOSTED_BASE_URL, api_key=FAKE_KEY).generate(
            "hi", schema={"type": "object"}
        )

    assert recorder.only.headers["authorization"] == f"Bearer {FAKE_KEY}"


def test_an_endpoint_off_this_host_with_no_key_is_refused_without_a_request() -> None:
    """The request cannot succeed, so it is not made."""
    recorder = RecordedTransport()
    with recorder.patched(), pytest.raises(DomainError) as exc_info:
        _provider(base_url=HOSTED_BASE_URL, api_key="").generate("hi")

    assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
    assert recorder.count == 0


# ---------------------------------------------------------------------------
# A body that is not JSON is a failure, not an empty intent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "I'd be happy to help you find a laptop!",
        "",
        "null",
        "[]",
        "42",
        '"just a string"',
        "{not json at all}",
    ],
)
def test_an_unparseable_body_raises_instead_of_returning_an_empty_intent(body: str) -> None:
    """An empty intent reaches the catalog as a search with no filters and is
    presented as an answer. A local model returning prose is common enough that
    this is the difference between a wrong result and a reported failure."""
    recorder = RecordedTransport(content=body)
    with recorder.patched(), pytest.raises(DomainError) as exc_info:
        _provider().generate("find me a laptop", schema={"type": "object"})

    assert exc_info.value.code == ErrorCode.SERVICE_UNAVAILABLE
    assert exc_info.value.details.get("reason") == "no_json_object"


@pytest.mark.parametrize(
    "body",
    [
        JSON_INTENT,
        f"```json\n{JSON_INTENT}\n```",
        f"Here is what I extracted:\n{JSON_INTENT}\nHope that helps.",
    ],
)
def test_the_object_a_model_did_emit_is_recovered(body: str) -> None:
    """Fenced and prose-wrapped objects are read out of what the model actually
    said. Nothing is invented: the recovered value is exactly the emitted one."""
    recorder = RecordedTransport(content=body)
    with recorder.patched():
        response = _provider().generate("find me a laptop", schema={"type": "object"})

    assert response.parsed_json == {"category": "laptop", "min_memory_gb": 16}


def test_no_constraint_value_is_ever_invented() -> None:
    """The recovered mapping holds the emitted keys and no others."""
    assert parse_json_object('{"category": "laptop"}') == {"category": "laptop"}
    assert parse_json_object("{}") == {}


def test_an_unstructured_request_is_not_parsed_at_all() -> None:
    """Prose is a valid answer when no schema was asked for."""
    recorder = RecordedTransport(content="a paragraph of prose")
    with recorder.patched():
        response = _provider().generate("summarise this")

    assert response.parsed_json is None
    assert response.content == "a paragraph of prose"


# ---------------------------------------------------------------------------
# Timeout, retries, and the token ceiling apply the same way everywhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", [LOCAL_BASE_URL, HOSTED_BASE_URL])
def test_the_token_ceiling_is_sent_to_every_endpoint(base_url: str) -> None:
    import json

    recorder = RecordedTransport()
    with recorder.patched():
        _provider(base_url=base_url, api_key=FAKE_KEY, max_tokens=2048).generate(
            "hi", schema={"type": "object"}
        )

    assert json.loads(recorder.only.content)["max_tokens"] == 2048


@pytest.mark.parametrize("base_url", [LOCAL_BASE_URL, HOSTED_BASE_URL])
def test_the_retry_budget_applies_to_every_endpoint(base_url: str) -> None:
    """`MODEL_MAX_RETRIES=2` means three attempts, wherever the endpoint lives."""
    attempts = 0

    def always_timeout(client: httpx.Client, request: httpx.Request, **kwargs: object):
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("timed out")

    with patch.object(httpx.Client, "send", always_timeout), pytest.raises(DomainError) as exc_info:
        _provider(base_url=base_url, api_key=FAKE_KEY, max_retries=2).generate("hi")

    assert attempts == 3
    assert exc_info.value.code == ErrorCode.GATEWAY_TIMEOUT


def test_a_non_200_is_not_retried() -> None:
    """Its body carries the endpoint's own explanation, which this module does not
    read, so a retry would as often repeat a rejected model name as recover."""
    recorder = RecordedTransport(status_code=503)
    with recorder.patched(), pytest.raises(DomainError) as exc_info:
        _provider(max_retries=2).generate("hi")

    assert recorder.count == 1
    assert exc_info.value.code == ErrorCode.SERVICE_UNAVAILABLE
    assert exc_info.value.details.get("status") == 503


def test_the_configured_timeout_reaches_the_client() -> None:
    seen: list[object] = []
    original = httpx.Client.__init__

    def spy(self: httpx.Client, *args: object, **kwargs: object) -> None:
        seen.append(kwargs.get("timeout"))
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    recorder = RecordedTransport()
    with patch.object(httpx.Client, "__init__", spy), recorder.patched():
        _provider(timeout_seconds=7.5).generate("hi", schema={"type": "object"})

    assert seen == [7.5]


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_the_default_provider_is_the_mock() -> None:
    """A clean clone needs no model at all."""
    assert isinstance(get_model_provider(Settings()), MockModelProvider)


def test_a_loopback_endpoint_with_no_key_selects_the_http_provider() -> None:
    """The configuration this whole bug made unreachable."""
    provider = get_model_provider(
        Settings(
            model_provider="openai_compatible",
            model_base_url=LOCAL_BASE_URL,
            model_name=LOCAL_MODEL,
            model_api_key="",
        )
    )

    assert isinstance(provider, GroqModelProvider)
    assert provider.base_url == LOCAL_BASE_URL
    assert provider.model_name == LOCAL_MODEL
    assert provider.api_key == ""


def test_the_selected_provider_carries_the_configured_limits() -> None:
    provider = get_model_provider(
        Settings(
            model_provider="openai_compatible",
            model_base_url=LOCAL_BASE_URL,
            model_name=LOCAL_MODEL,
            model_timeout_seconds=17,
            model_max_tokens=999,
            model_max_retries=4,
        )
    )

    assert isinstance(provider, GroqModelProvider)
    assert provider.timeout_seconds == 17.0
    assert provider.max_tokens == 999
    assert provider.max_retries == 4


def test_a_remote_endpoint_with_no_key_falls_back_to_the_mock() -> None:
    """Nothing silently makes an unauthenticated call to a metered host."""
    provider = get_model_provider(
        Settings(
            model_provider="openai_compatible",
            model_base_url=HOSTED_BASE_URL,
            model_api_key="",
        )
    )

    assert isinstance(provider, MockModelProvider)


def test_the_grok_selector_no_longer_rewrites_the_base_url() -> None:
    """It used to replace a configured endpoint with a hardcoded vendor host."""
    provider = get_model_provider(
        Settings(
            model_provider="grok",
            model_base_url=LOCAL_BASE_URL,
            model_name=LOCAL_MODEL,
        )
    )

    assert isinstance(provider, GroqModelProvider)
    assert provider.base_url == LOCAL_BASE_URL
