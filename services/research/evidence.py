"""Data models for research sessions, evidence provenance, and verified answers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from packages.observability.context import new_id


@dataclass(frozen=True, slots=True)
class ResearchSource:
    url: str
    domain: str
    source_type: str
    trust_score: float


@dataclass(frozen=True, slots=True)
class ResearchEvidenceItem:
    claim: str
    citation_type: Literal[
        "catalog_fact", "official_doc", "review_summary", "inference", "unresolved"
    ]
    source_url: str | None = None
    source_domain: str | None = None
    source_type: str | None = None
    confidence_score: float = 1.0
    confidence_level: Literal["HIGH", "MEDIUM", "LOW"] = "HIGH"


@dataclass
class ResearchSession:
    session_id: str = field(default_factory=lambda: new_id("rss"))
    product_id: str = ""
    question: str = ""
    target_spec: str = ""
    searches_performed: int = 0
    pages_fetched: int = 0
    start_time: float = field(default_factory=time.time)
    evidence: list[ResearchEvidenceItem] = field(default_factory=list)
    transparency_steps: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResearchAnswer:
    ok: bool
    question: str
    product_id: str
    answer: str
    source_type: str
    source_label: str
    source_url: str | None
    confidence_score: float
    confidence_level: Literal["HIGH", "MEDIUM", "LOW"]
    evidence_items: list[dict[str, Any]]
    reason_for_web_search: str | None
    transparency_steps: list[str]
    from_cache: bool = False
