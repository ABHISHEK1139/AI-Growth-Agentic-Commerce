"""Prompt injection detector for external web content."""

from __future__ import annotations

import re

_PROMPT_INJECTION_RULES = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*:\s*override", re.IGNORECASE),
    re.compile(r"reveal\s+(your\s+)?(api\s+key|secret|password|system\s+prompt)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?safety\s+guidelines", re.IGNORECASE),
]


def contains_prompt_injection(text: str) -> bool:
    """Check if external text contains direct prompt injection attempts."""
    if not text:
        return False
    return any(rule.search(text) for rule in _PROMPT_INJECTION_RULES)
