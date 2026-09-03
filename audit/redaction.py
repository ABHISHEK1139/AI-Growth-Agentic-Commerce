"""Secret index, credential shape detectors, and scrubbing engine."""

from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"rzp_(?:test|live)_[A-Za-z0-9]{14,20}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+eyJ[A-Za-z0-9_\-\.]+", re.I),
    re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
]

_HASH_FIELD_NAMES = {
    "price_hash",
    "request_hash",
    "content_sha256",
    "stdout_sha256",
    "stderr_sha256",
}


def is_secret_like(value: str) -> bool:
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        # 64-hex SHA-256 digests are hashes, not secrets
        return False
    return any(pat.search(value) for pat in _SECRET_PATTERNS)


def scrub(data: Any, max_depth: int = 10) -> Any:
    """Recursively redact secrets from dicts, lists, and strings."""
    if max_depth <= 0:
        return data

    if isinstance(data, str):
        if is_secret_like(data):
            return "[REDACTED]"
        res = data
        for pat in _SECRET_PATTERNS:
            res = pat.sub("[REDACTED]", res)
        return res

    if isinstance(data, dict):
        return {
            k: (
                "[REDACTED]"
                if any(s in str(k).lower() for s in ("secret", "token", "key", "password"))
                and not any(h in str(k).lower() for h in _HASH_FIELD_NAMES)
                else scrub(v, max_depth - 1)
            )
            for k, v in data.items()
        }

    if isinstance(data, list):
        return [scrub(item, max_depth - 1) for item in data]

    if isinstance(data, tuple):
        return tuple(scrub(item, max_depth - 1) for item in data)

    return data
