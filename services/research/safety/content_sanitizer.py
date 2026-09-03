"""External web content sanitizer and untrusted evidence delimiting."""

from __future__ import annotations

import html
import re

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
    re.compile(r"system\s*:\s*override", re.IGNORECASE),
    re.compile(
        r"reveal\s+(your\s+)?(api\s+key|secret|password|token|system\s+prompt)", re.IGNORECASE
    ),
    re.compile(r"do\s+not\s+follow\s+(the\s+)?original\s+prompt", re.IGNORECASE),
]


def sanitize_evidence_text(raw_text: str, max_chars: int = 4000) -> str:
    """Sanitize untrusted external web text, stripping tags, dangerous sequences, and control chars."""
    if not raw_text:
        return ""

    # Strip HTML tags
    cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", raw_text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)

    # Neutralize common injection phrases in external text
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub("[REDACTED_UNTRUSTED_INSTRUCTION]", cleaned)

    # Normalize whitespace
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    normalized = "\n".join(lines)

    return normalized[:max_chars].strip()


def wrap_untrusted_evidence(content: str, source_url: str | None = None) -> str:
    """Wrap untrusted web research content in unambiguous security boundaries."""
    sanitized = sanitize_evidence_text(content)
    source_label = source_url or "external_source"
    return (
        f"--- BEGIN UNTRUSTED EVIDENCE (Source: {source_label}) ---\n"
        f"{sanitized}\n"
        f"--- END UNTRUSTED EVIDENCE ---"
    )
