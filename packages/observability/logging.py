"""Structured JSON logging with redaction (NFR-9).

Two responsibilities:

1. Emit one JSON object per log line, always carrying whichever correlation
   identifiers are in scope, so a transaction can be reconstructed later.
2. Refuse to emit secrets. Redaction is applied to keys by name and to values by
   shape, because the failure mode we are guarding against is a developer adding
   a helpful ``extra={"payload": ...}`` that happens to contain a live key.

Redaction is deliberately conservative: a false positive costs a masked debug
value, a false negative costs a leaked credential.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from packages.observability.context import current_ids

REDACTED = "***REDACTED***"

#: Substrings that mark a mapping key as sensitive. Matched case-insensitively
#: against the whole key, so ``razorpay_key_secret`` and ``X-Razorpay-Signature``
#: are both caught.
SENSITIVE_KEY_PARTS: frozenset[str] = frozenset(
    {
        "secret",
        "password",
        "passwd",
        "token",
        "api_key",
        "apikey",
        "key_id",
        "key_secret",
        "authorization",
        "auth",
        "cookie",
        "session",
        "signature",
        "private",
        "credential",
        "card",
        "cvv",
        "cvc",
        "pan",
        "otp",
        "pin",
        "jwt",
        "bearer",
        "webhook_secret",
    }
)

#: Keys that contain the word "auth" or "key" but are structural, not secret.
#: Without this allowlist the audit trail would redact its own vocabulary.
SAFE_KEY_EXACT: frozenset[str] = frozenset(
    {
        "authorization_id",
        "authorization_hash",
        "authorization_status",
        "authorization_expiry_seconds",
        "idempotency_key",
        "price_hash",
        "input_hash",
        "inputs_hash",
        "raw_body_hash",
        "extracted_text_hash",
        "storage_key",
        "signature_valid",
        "auth_required",
        "authenticated",
    }
)

#: Value shapes that are secrets regardless of the key they arrived under.
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brzp_(?:test|live)_[A-Za-z0-9]+", re.IGNORECASE),  # Razorpay key id
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # OpenAI-style secret key
    re.compile(r"\bgsk_[A-Za-z0-9_\-]{16,}"),  # Groq secret key
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),  # JWT
)

#: Attributes the stdlib puts on every record; anything else is caller-supplied
#: and belongs in the log line.
_STDLIB_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)

_MAX_DEPTH = 6


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_KEY_EXACT:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _scrub_value_shape(value: str) -> str:
    for pattern in _VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively mask secrets in an arbitrary structure.

    Applies both checks: sensitive key names mask their whole value, and string
    values are scanned for credential shapes wherever they appear.
    """
    if _depth > _MAX_DEPTH:
        return "***TRUNCATED***"

    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                result[key] = REDACTED
            else:
                result[key] = redact(item, _depth=_depth + 1)
        return result

    if isinstance(value, list | tuple | set):
        return [redact(item, _depth=_depth + 1) for item in value]

    if isinstance(value, str):
        return _scrub_value_shape(value)

    if isinstance(value, int | float | bool) or value is None:
        return value

    # Unknown object: stringify, then scrub. Never trust __repr__ to be clean.
    return _scrub_value_shape(repr(value))


class JsonFormatter(logging.Formatter):
    """Renders a log record as a single redacted JSON object."""

    def __init__(self, *, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }

        # Correlation identifiers, flattened so log queries stay simple.
        payload.update(current_ids().as_dict())

        # Caller-supplied structured fields.
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STDLIB_RECORD_ATTRS and not key.startswith("_")
        }
        extras.pop("event", None)
        if extras:
            payload.update(redact(extras))

        message = record.getMessage()
        if message and message != payload["event"]:
            payload["message"] = _scrub_value_shape(message)

        if record.exc_info:
            # The traceback text can echo arguments, so it is scrubbed too.
            payload["exception"] = _scrub_value_shape(self.formatException(record.exc_info))

        if record.stack_info:
            payload["stack"] = _scrub_value_shape(self.formatStack(record.stack_info))

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, level: str = "INFO", service: str = "agentpay") -> None:
    """Install the JSON formatter as the only root handler.

    Idempotent: safe to call from the API app factory, the worker entrypoint, and
    a pytest fixture without stacking duplicate handlers.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own colourised handlers; route them through ours so
    # every line in the container log is machine-readable.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    # SQLAlchemy is chatty at INFO and can echo bound parameters.
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    """Module-level logger accessor, so callers never touch ``logging`` directly."""
    return logging.getLogger(name)
