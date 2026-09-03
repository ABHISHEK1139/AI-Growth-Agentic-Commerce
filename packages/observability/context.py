"""Correlation identifiers.

A single transaction is stitched together from an HTTP request, one or more
agent runs, several tool calls, a payment attempt, and an asynchronous webhook.
The only thing that makes that reconstructable afterwards is a consistent set of
identifiers carried on every log line and every audit event.

These live in :mod:`contextvars` so they propagate through async call stacks
without being threaded manually through every function signature, and so worker
tasks can adopt the identifiers of the request that scheduled them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace

_TRACE_ID: ContextVar[str | None] = ContextVar("trace_id", default=None)
_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_AGENT_RUN_ID: ContextVar[str | None] = ContextVar("agent_run_id", default=None)
_CHECKOUT_ID: ContextVar[str | None] = ContextVar("checkout_id", default=None)
_PAYMENT_ID: ContextVar[str | None] = ContextVar("payment_id", default=None)
_PROVIDER_EVENT_ID: ContextVar[str | None] = ContextVar("provider_event_id", default=None)
_ACTOR_ID: ContextVar[str | None] = ContextVar("actor_id", default=None)

_VARS: dict[str, ContextVar[str | None]] = {
    "trace_id": _TRACE_ID,
    "request_id": _REQUEST_ID,
    "agent_run_id": _AGENT_RUN_ID,
    "checkout_id": _CHECKOUT_ID,
    "payment_id": _PAYMENT_ID,
    "provider_event_id": _PROVIDER_EVENT_ID,
    "actor_id": _ACTOR_ID,
}


@dataclass(frozen=True, slots=True)
class CorrelationIds:
    """Immutable snapshot of the current correlation identifiers."""

    trace_id: str | None = None
    request_id: str | None = None
    agent_run_id: str | None = None
    checkout_id: str | None = None
    payment_id: str | None = None
    provider_event_id: str | None = None
    actor_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        """Only the identifiers that are actually set, for compact log lines."""
        return {
            key: value
            for key, value in {
                "trace_id": self.trace_id,
                "request_id": self.request_id,
                "agent_run_id": self.agent_run_id,
                "checkout_id": self.checkout_id,
                "payment_id": self.payment_id,
                "provider_event_id": self.provider_event_id,
                "actor_id": self.actor_id,
            }.items()
            if value is not None
        }


def new_id(prefix: str) -> str:
    """A short, readable, prefixed identifier.

    Prefixes make an identifier self-describing in a log line, which matters a
    great deal when reading a transaction timeline under demo pressure.
    """
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def current_ids() -> CorrelationIds:
    """Snapshot the identifiers currently in scope."""
    return CorrelationIds(
        trace_id=_TRACE_ID.get(),
        request_id=_REQUEST_ID.get(),
        agent_run_id=_AGENT_RUN_ID.get(),
        checkout_id=_CHECKOUT_ID.get(),
        payment_id=_PAYMENT_ID.get(),
        provider_event_id=_PROVIDER_EVENT_ID.get(),
        actor_id=_ACTOR_ID.get(),
    )


def set_ids(**kwargs: str | None) -> dict[str, Token[str | None]]:
    """Set one or more identifiers, returning reset tokens.

    Unknown keys are a programming error and raise rather than being silently
    dropped, because a typo'd identifier name is invisible in production.
    """
    tokens: dict[str, Token[str | None]] = {}
    for key, value in kwargs.items():
        try:
            var = _VARS[key]
        except KeyError:
            raise KeyError(f"Unknown correlation identifier {key!r}") from None
        tokens[key] = var.set(value)
    return tokens


def reset_ids(tokens: dict[str, Token[str | None]]) -> None:
    """Restore identifiers to their previous values."""
    for key, token in tokens.items():
        _VARS[key].reset(token)


@contextmanager
def correlation_scope(**kwargs: str | None) -> Iterator[CorrelationIds]:
    """Bind identifiers for the duration of a block.

    Used by the request middleware, by each agent run, and by worker tasks that
    inherit the identifiers of whatever scheduled them.
    """
    tokens = set_ids(**kwargs)
    try:
        yield current_ids()
    finally:
        reset_ids(tokens)


def adopt(ids: CorrelationIds) -> dict[str, Token[str | None]]:
    """Adopt a previously captured snapshot, e.g. inside a worker task."""
    return set_ids(**ids.as_dict())


def with_overrides(ids: CorrelationIds, **kwargs: str | None) -> CorrelationIds:
    """Copy a snapshot with some identifiers replaced."""
    return replace(ids, **kwargs)
