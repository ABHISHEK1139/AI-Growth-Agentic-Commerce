"""Response envelopes (design: "API Design").

Every response, from any surface, has one of two shapes:

```json
{ "ok": true,  "request_id": "req_...", "data": {}, "warnings": [], "evidence": [],
  "next_actions": [] }
{ "ok": false, "request_id": "req_...",
  "error": { "code": "OFFER_EXPIRED", "message": "...", "retryable": false, "details": {} } }
```

The frontend and the external buyer then share one error-handling path instead of
each learning the failure modes of each endpoint.

``next_actions`` is the part that earns its keep. It is how a client offers
recovery without hardcoding knowledge of what went wrong (Requirement 31.10): a
price change comes back with "request a fresh comparison" and "get a new
approval" attached, and the interface renders buttons for whatever it was handed.
Because services are the only layer that knows which recoveries exist, they
populate it — see :class:`ServiceResult` — and transport only stamps the
``request_id``.

These models are pure: no logging, no context, no framework. That keeps them
importable from services, from the worker, and from the JSON Schema export.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from packages.errors.registry import ErrorCode, spec_for


class NextAction(BaseModel):
    """A recovery or follow-up the client may offer.

    ``action`` is the machine-readable discriminator a client switches on;
    ``label`` is the human sentence. Both are required, because an action without
    a label ends up rendered as a raw enum, and a label without an action ends up
    hardcoded in the frontend.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    #: HTTP method and path the client would call, when the action is a call.
    method: Literal["GET", "POST", "PUT", "DELETE"] | None = None
    href: str | None = Field(default=None, max_length=500)
    #: Non-sensitive parameters the client should send back with the action.
    params: dict[str, Any] = Field(default_factory=dict)


class EnvelopeWarning(BaseModel):
    """Something the caller should know that did not stop the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    """A pointer to the record a claim came from.

    The system does not present a number it cannot source (NFR-13), so anything
    the agent asserts arrives with the offer, inventory row, or policy version it
    was read from.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1, max_length=64)
    reference: str = Field(min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=500)


class ErrorBody(BaseModel):
    """The ``error`` object of an error envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    message: str
    retryable: bool
    #: Structured, client-safe context: limits, amounts, field names. Never a
    #: driver message, an exception string, or a stack trace.
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_code(
        cls,
        code: ErrorCode,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> Self:
        """Build from the registry, so status and retryable never drift."""
        spec = spec_for(code)
        return cls(
            code=code,
            message=message or spec.message,
            retryable=spec.is_retryable,
            details=details or {},
        )


class SuccessEnvelope(BaseModel):
    """``ok: true``. ``data`` carries the endpoint's own payload."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    request_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[EnvelopeWarning] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ErrorEnvelope(BaseModel):
    """``ok: false``. One ``error`` object, optionally with recoveries.

    ``next_actions`` is omitted from the serialized payload when empty so the
    common case matches the documented shape exactly, and present when a service
    knows how the caller can recover.
    """

    model_config = ConfigDict(extra="forbid")

    ok: Literal[False] = False
    request_id: str | None = None
    error: ErrorBody
    next_actions: list[NextAction] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if not payload.get("next_actions"):
            payload.pop("next_actions", None)
        return payload


class ServiceResult(BaseModel):
    """What a service hands back to transport.

    Services own the domain knowledge, including which recoveries exist, so they
    build this. Transport adds the ``request_id`` and serializes. The split is
    what keeps ``next_actions`` out of the routers and out of the frontend.
    """

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[EnvelopeWarning] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)

    def to_envelope(self, *, request_id: str | None = None) -> SuccessEnvelope:
        return SuccessEnvelope(
            request_id=request_id,
            data=self.data,
            warnings=self.warnings,
            evidence=self.evidence,
            next_actions=self.next_actions,
        )
