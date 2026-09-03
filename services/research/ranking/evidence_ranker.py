"""Evidence confidence estimation and ranking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True, slots=True)
class RankedEvidence:
    claim: str
    source_url: str
    source_type: str
    confidence_score: float
    confidence_level: ConfidenceLevel
    relevance_score: float


_STOPWORDS = frozenset(
    {
        "does",
        "this",
        "that",
        "have",
        "with",
        "what",
        "where",
        "which",
        "there",
        "from",
        "laptop",
        "product",
        "item",
        "about",
        "many",
        "much",
        "they",
        "them",
        "these",
    }
)


class EvidenceRanker:
    """Combines source trust and snippet relevance to score overall confidence."""

    @classmethod
    def rank_evidence(
        cls,
        claim: str,
        query: str,
        source_url: str,
        source_trust_score: float,
        source_type: str,
    ) -> RankedEvidence:
        all_terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1]
        tech_terms = [t for t in all_terms if t not in _STOPWORDS]
        query_terms = tech_terms if tech_terms else all_terms
        claim_lower = claim.lower()

        matches = sum(1 for t in query_terms if t in claim_lower)
        relevance = min(1.0, matches / max(1, len(query_terms))) if query_terms else 0.5

        # Weighted confidence: 70% source authority + 30% text relevance
        overall_confidence = round((0.7 * source_trust_score) + (0.3 * relevance), 3)

        if overall_confidence >= 0.80 or (source_trust_score >= 0.95 and relevance >= 0.3):
            level: ConfidenceLevel = "HIGH"
        elif overall_confidence >= 0.55:
            level = "MEDIUM"
        else:
            level = "LOW"

        return RankedEvidence(
            claim=claim,
            source_url=source_url,
            source_type=source_type,
            confidence_score=overall_confidence,
            confidence_level=level,
            relevance_score=relevance,
        )
