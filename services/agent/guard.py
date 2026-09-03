"""Prompt safety classification and Meta Llama Guard integration (Task 26, Requirement 22.3, 22.4, 22.7).

Three providers, selected by ``guard_provider``. The choice is a cost decision as
much as a security one:

``heuristic`` (default)
    Layer 1 only. Deterministic, in-process, sub-millisecond. Never leaves the
    process, so it costs nothing and there is nothing to bill.
``local``
    Layer 1, then an OpenAI-compatible ``/chat/completions`` POST at
    ``guard_base_url`` --- Ollama on this host by default. Never leaves the host,
    so it also costs nothing. Plain HTTP over the already-pinned httpx; no new
    dependency, no model runtime in this process.
``remote``
    Layer 1, then the same POST against a metered provider, authenticated with
    ``guard_api_key``. Bills on every single request.

Layer 1 runs first in all three modes, because it is free and it catches the
obvious cases before any model is consulted.

**An attempted evaluation that does not conclude fails closed.** All three of
them: a dropped connection, a non-200, and a 200 whose body carries no
recognisable verdict. The last one used to clear the prompt, which meant an
upstream changing its response contract silently disabled Layer 2 while every log
line still said the guard had run.

**A guard that was never configured is not an inconclusive evaluation**, and the
asymmetry is deliberate. ``guard_provider="heuristic"`` makes no request, and
``remote`` with no credential cannot make one, so in both cases the Layer 1
verdict stands and a credential-free clone still completes a purchase. The
distinction is whether a request was attempted, not whether one succeeded.

**The guard reads only ``guard_*`` settings.** It used to read ``model_api_key``,
``model_base_url``, and ``model_guard_name``, which meant that configuring the
*reasoning* model silently enabled a second, billed request in front of every
query. Those three settings belong to the reasoning model and are not referenced
anywhere in this module.

That is now enforced by the type rather than by discipline. The guard receives a
:class:`~packages.config.providers.GuardConfig`, not the application's
``Settings``: there is no field on it that a reasoning-model value could be
assigned to, and the domain no longer imports the delivery layer to be configured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from packages.config.providers import (
    DEFAULT_GUARD_BASE_URL,
    DEFAULT_GUARD_MODEL,
    DEFAULT_GUARD_TIMEOUT_SECONDS,
    GuardConfig,
)
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode

MAX_INPUT_LENGTH = 4000

#: The two providers that consult a model. ``heuristic`` never reaches Layer 2, so
#: it cannot label a Layer 2 verdict.
ModelBackedGuardProvider = Literal["local", "remote"]

#: Layer 2 defaults, re-exported from :mod:`packages.config.providers` where they
#: are also the :class:`GuardConfig` field defaults. ``evaluate()`` always passes
#: the configured values explicitly; these exist so a direct call is possible
#: without constructing a configuration object.
__all__ = [
    "DEFAULT_GUARD_BASE_URL",
    "DEFAULT_GUARD_MODEL",
    "DEFAULT_GUARD_TIMEOUT_SECONDS",
    "EVALUATORS",
    "LLAMA_GUARD_CATEGORIES",
    "MAX_INPUT_LENGTH",
    "PROMPT_GUARD_UNSAFE_THRESHOLD",
    "ModelBackedGuardProvider",
    "PromptSafetyClassifier",
    "SafetyAssessment",
]

#: Meta Prompt Guard answers with a probability rather than a label. At or above
#: this, the input is treated as an injection attempt.
PROMPT_GUARD_UNSAFE_THRESHOLD = 0.5

# High-risk prompt injection and jailbreak patterns (Layer 1 Instant Filter)
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|above|system)\s+instructions?", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|system)\s+prompts?", re.I),
    re.compile(r"(?:bypass|override|disable)\s+(?:policy|budget|checks|guardrails)", re.I),
    re.compile(r"(?:set|make)\s+price\s+(?:to\s+)?(?:0|zero|free|1\s+rupee)", re.I),
    re.compile(
        r"(?:exfiltrate|leak|print|display|reveal)\s+(?:api_key|token|secret|credential)", re.I
    ),
    re.compile(r"(?:report|mark)\s+payment\s+(?:as\s+)?(?:successful|verified|confirmed)", re.I),
    re.compile(r"<system>|\[INST\]|```system", re.I),
]

# Meta Llama Guard 3 Safety Taxonomies (S1 - S14)
LLAMA_GUARD_CATEGORIES: dict[str, str] = {
    "S1": "Violent Crimes",
    "S2": "Non-Violent Crimes",
    "S3": "Sex-Related Crimes",
    "S4": "Child Sexual Exploitation",
    "S5": "Defamation",
    "S6": "Specialized Advice",
    "S7": "Privacy / PII Leakage",
    "S8": "Intellectual Property",
    "S9": "Indiscriminate Weapons",
    "S10": "Hate Speech",
    "S11": "Suicide & Self-Harm",
    "S12": "Sexual Content",
    "S13": "Elections & Political Interference",
    "S14": "Code Interpreter Abuse / Prompt Injection",
}

#: Every evaluator identifier this module can emit. Closed on purpose: the value
#: reaches a log line and the ``/api/explore`` response body, and its job is to
#: make the cost of a verdict readable. A ``local_``/``remote_`` prefix means a
#: network request was made; anything else means the verdict was free.
#:
#: Layer 1 identifiers carry no provider token, because Layer 1 is the same
#: in-process check in all three modes.
EVALUATORS: frozenset[str] = frozenset(
    {
        # Layer 1, no network, every mode.
        "heuristic_bounds",  # over MAX_INPUT_LENGTH
        "heuristic_regex",  # matched an injection pattern
        "heuristic_passed",  # Layer 1 clear; the final verdict under `heuristic`
        # Layer 2 on this host, free.
        "local_llama_guard_verdict",  # parsed a `safe`/`unsafe` label
        "local_prompt_guard_score",  # parsed a bare probability
        "local_guard_unparsed",  # 200 with no recognisable verdict; fails closed
        "local_guard_failclosed_unavailable",  # non-200
        "local_guard_failclosed_transport",  # request never completed
        # Layer 2 on a metered provider, billed.
        "remote_llama_guard_verdict",
        "remote_prompt_guard_score",
        "remote_guard_unparsed",
        "remote_guard_failclosed_unavailable",
        "remote_guard_failclosed_transport",
        "remote_guard_skipped_no_key",  # `remote` selected with no credential
    }
)


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    is_safe: bool
    threat_category: str | None = None
    reason: str | None = None
    #: One of :data:`EVALUATORS`. Names which provider produced this verdict.
    evaluator: str = "heuristic_passed"


class PromptSafetyClassifier:
    """Classifies incoming natural language prompts before model execution using Layered GuardLLM defense."""

    @staticmethod
    def evaluate_heuristic(prompt: str) -> SafetyAssessment:
        """Layer 1: Instant deterministic heuristic safety and prompt injection checks (<1ms).

        Runs first under every provider, including ``heuristic``, because it costs
        nothing and no model needs to be consulted to reject an oversized input or
        a known injection string.
        """
        # 1. Length validation (Requirement 22.4)
        if len(prompt) > MAX_INPUT_LENGTH:
            return SafetyAssessment(
                is_safe=False,
                threat_category="OVERSIZED_INPUT",
                reason=f"Input length {len(prompt)} exceeds maximum of {MAX_INPUT_LENGTH} characters.",
                evaluator="heuristic_bounds",
            )

        # 2. Heuristic prompt injection scans (Requirement 22.7)
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(prompt):
                return SafetyAssessment(
                    is_safe=False,
                    threat_category="PROMPT_INJECTION",
                    reason="Prompt injection pattern detected.",
                    evaluator="heuristic_regex",
                )

        return SafetyAssessment(is_safe=True, evaluator="heuristic_passed")

    @staticmethod
    def evaluate_meta_llama_guard(
        prompt: str,
        *,
        api_key: str = "",
        base_url: str = DEFAULT_GUARD_BASE_URL,
        guard_model: str = DEFAULT_GUARD_MODEL,
        timeout_seconds: float = DEFAULT_GUARD_TIMEOUT_SECONDS,
        provider: ModelBackedGuardProvider = "remote",
    ) -> SafetyAssessment:
        """Layer 2: Meta Llama Guard deep semantic safety classifier.

        One request shape serves both providers, because both speak the
        OpenAI-compatible ``/chat/completions`` route. ``provider`` changes only
        two things: whether an absent credential is a skip or a normal
        unauthenticated call, and which half of the evaluator set is used to
        label the verdict.
        """
        if provider == "remote" and not api_key:
            # A metered provider cannot be called without a credential. Reported
            # rather than attempted, and left permissive, because "not configured"
            # is the posture the credential-free golden path runs in.
            return SafetyAssessment(is_safe=True, evaluator="remote_guard_skipped_no_key")

        try:
            import httpx

            headers = {"Content-Type": "application/json"}
            if api_key:
                # Omitted entirely when there is no key. A local Ollama accepts
                # either, but sending an empty `Bearer` would misreport the call
                # as authenticated.
                headers["Authorization"] = f"Bearer {api_key}"

            payload: dict[str, Any] = {
                "model": guard_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
            }

            with httpx.Client(timeout=timeout_seconds) as client:
                res = client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )

            if res.status_code == 200:
                data = res.json()
                raw_decision = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                )

                # Shape 1: a bare numerical probability (Meta Prompt Guard).
                try:
                    score = float(raw_decision)
                except ValueError:
                    pass
                else:
                    if score >= PROMPT_GUARD_UNSAFE_THRESHOLD:
                        return SafetyAssessment(
                            is_safe=False,
                            threat_category="META_PROMPT_GUARD_INJECTION",
                            reason=f"Meta Prompt Guard detected injection attempt (confidence: {score * 100:.2f}%)",
                            evaluator=f"{provider}_prompt_guard_score",
                        )
                    return SafetyAssessment(
                        is_safe=True, evaluator=f"{provider}_prompt_guard_score"
                    )

                # Shape 2: `safe`, or `unsafe` followed by a taxonomy code line.
                decision = raw_decision.lower()
                if decision.startswith("unsafe"):
                    lines = raw_decision.split("\n")
                    category_code = lines[1].strip() if len(lines) > 1 else "S14"
                    category_name = LLAMA_GUARD_CATEGORIES.get(
                        category_code, "Unsafe Policy Violation"
                    )
                    return SafetyAssessment(
                        is_safe=False,
                        threat_category=f"META_LLAMA_GUARD_{category_code}",
                        reason=f"Meta Llama Guard classified input as unsafe ({category_name})",
                        evaluator=f"{provider}_llama_guard_verdict",
                    )
                if decision.startswith("safe"):
                    return SafetyAssessment(
                        is_safe=True, evaluator=f"{provider}_llama_guard_verdict"
                    )

                # A 200 whose body carries no recognisable verdict is an
                # evaluation that was attempted and did not conclude, which is the
                # same class of outcome as a non-200 and a dropped connection. It
                # used to be the one inconclusive path that cleared the prompt, so
                # an upstream changing its response contract silently disabled
                # Layer 2 while every log still said the guard had run.
                return SafetyAssessment(
                    is_safe=False,
                    threat_category="GUARD_VERDICT_UNPARSED",
                    reason=(
                        "Meta Llama Guard returned 200 with no recognisable verdict; "
                        "failing closed for safety."
                    ),
                    evaluator=f"{provider}_guard_unparsed",
                )

            return SafetyAssessment(
                is_safe=False,
                threat_category="GUARD_SERVICE_UNAVAILABLE",
                reason=f"Meta Llama Guard endpoint returned non-200 status ({res.status_code}); failing closed for safety.",
                evaluator=f"{provider}_guard_failclosed_unavailable",
            )
        except Exception as exc:
            # Layer 2 failure fails closed in strict security mode with logged reason
            return SafetyAssessment(
                is_safe=False,
                threat_category="GUARD_TRANSPORT_ERROR",
                reason=f"Meta Llama Guard safety check failed ({type(exc).__name__}); failing closed for safety.",
                evaluator=f"{provider}_guard_failclosed_transport",
            )

    @classmethod
    def evaluate(
        cls,
        prompt: str,
        config: GuardConfig | None = None,
        *,
        settings: Any = None,
    ) -> SafetyAssessment:
        """Run layered GuardLLM evaluation: Layer 1, then Layer 2 if one is selected.

        Layer 2 is reached only when ``config.provider`` asks for it. Accepts either
        a dedicated :class:`GuardConfig` or the application :class:`Settings` for
        backwards-compatible caller ergonomics.
        """
        l1 = cls.evaluate_heuristic(prompt)
        if not l1.is_safe:
            return l1

        if config is None and settings is not None:
            if hasattr(settings, "guard_config") and callable(settings.guard_config):
                config = settings.guard_config()
            elif isinstance(settings, GuardConfig):
                config = settings
            elif hasattr(settings, "guard_provider"):
                config = GuardConfig(
                    provider=getattr(settings, "guard_provider", "heuristic"),
                    api_key=getattr(settings, "guard_api_key", ""),
                    base_url=getattr(settings, "guard_base_url", DEFAULT_GUARD_BASE_URL),
                    model_name=getattr(
                        settings,
                        "guard_model_name",
                        getattr(settings, "guard_model", DEFAULT_GUARD_MODEL),
                    ),
                    timeout_seconds=float(
                        getattr(settings, "guard_timeout_seconds", DEFAULT_GUARD_TIMEOUT_SECONDS)
                    ),
                )
        elif (
            config is not None
            and not isinstance(config, GuardConfig)
            and hasattr(config, "guard_config")
        ):
            config = config.guard_config()

        if config is None:
            return l1

        provider = config.provider
        if provider == "heuristic":
            return l1

        # Returned even when safe, so the evaluator on the record names the
        # provider that answered and a log line shows what the verdict cost.
        return cls.evaluate_meta_llama_guard(
            prompt,
            api_key=config.api_key,
            base_url=config.base_url,
            guard_model=config.model_name,
            timeout_seconds=config.timeout_seconds,
            provider=provider,
        )

    @classmethod
    def assert_safe(
        cls,
        prompt: str,
        config: GuardConfig | None = None,
        *,
        settings: Any = None,
    ) -> None:
        """Raise DomainError if prompt fails GuardLLM safety classification."""
        assessment = cls.evaluate(prompt, config, settings=settings)
        if not assessment.is_safe:
            raise DomainError(
                f"Prompt safety violation: {assessment.reason}",
                code=ErrorCode.PROMPT_INJECTION_SUSPECTED,
                details={
                    "threat_category": assessment.threat_category,
                    "evaluator": assessment.evaluator,
                },
            )
