"""Multi-tier research cache with query normalization and TTL freshness rules."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

# Default TTLs (seconds)
TTL_STATIC_SPECS = 30 * 86_400  # 30 days for hardware ports, dimensions, specs
TTL_DYNAMIC_INFO = 1 * 86_400  # 1 day for firmware, drivers, software


@dataclass
class CachedResearchEntry:
    query_hash: str
    product_id: str
    question: str
    answer: str
    evidence: list[dict[str, Any]]
    source_url: str | None
    source_domain: str | None
    source_type: str | None
    confidence_score: float
    confidence_level: str
    created_at: float
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class ResearchCache:
    """In-memory cache with query normalization, TTL rules, and Redis fallback readiness."""

    def __init__(self) -> None:
        self._store: dict[str, CachedResearchEntry] = {}

    @staticmethod
    def compute_key(product_id: str, question: str) -> str:
        norm_q = " ".join(question.lower().strip().split())
        raw_key = f"{product_id.strip()}:{norm_q}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get(self, product_id: str, question: str) -> CachedResearchEntry | None:
        key = self.compute_key(product_id, question)
        entry = self._store.get(key)
        if entry is None:
            return None

        if entry.is_expired():
            del self._store[key]
            return None

        return entry

    def set(
        self,
        product_id: str,
        question: str,
        answer: str,
        evidence: list[dict[str, Any]],
        source_url: str | None = None,
        source_domain: str | None = None,
        source_type: str | None = None,
        confidence_score: float = 1.0,
        confidence_level: str = "HIGH",
        ttl_seconds: int = TTL_STATIC_SPECS,
    ) -> CachedResearchEntry:
        key = self.compute_key(product_id, question)
        now = time.time()
        entry = CachedResearchEntry(
            query_hash=key,
            product_id=product_id,
            question=question,
            answer=answer,
            evidence=evidence,
            source_url=source_url,
            source_domain=source_domain,
            source_type=source_type,
            confidence_score=confidence_score,
            confidence_level=confidence_level,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        self._store[key] = entry
        return entry

    def clear(self) -> None:
        self._store.clear()


# Process-wide singleton cache instance
RESEARCH_CACHE = ResearchCache()
