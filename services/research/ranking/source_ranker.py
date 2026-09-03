"""Domain-level source trustworthiness ranker for technical product facts."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Literal

SourceType = Literal[
    "manufacturer",
    "official_documentation",
    "official_support",
    "major_retailer",
    "established_tech_review",
    "forum",
    "unknown_blog",
]

_MANUFACTURER_DOMAINS = frozenset(
    {
        "lenovo.com",
        "apple.com",
        "dell.com",
        "hp.com",
        "asus.com",
        "acer.com",
        "sony.com",
        "samsung.com",
        "bose.com",
        "logitech.com",
        "intel.com",
        "amd.com",
        "nvidia.com",
    }
)

_DOCUMENTATION_SUBDOMAINS = frozenset(
    {
        "psref.lenovo.com",
        "support.lenovo.com",
        "support.apple.com",
        "developer.apple.com",
        "support.dell.com",
        "manuals.playstation.net",
        "docs.sony.com",
    }
)

_ESTABLISHED_REVIEWS = frozenset(
    {
        "notebookcheck.net",
        "rtings.com",
        "gsmarena.com",
        "anandtech.com",
        "tomshardware.com",
        "theverge.com",
        "cnet.com",
        "pcmag.com",
    }
)

_RETAILER_DOMAINS = frozenset(
    {
        "amazon.in",
        "amazon.com",
        "flipkart.com",
        "bestbuy.com",
        "croma.com",
        "reliancedigital.in",
    }
)

_FORUM_DOMAINS = frozenset(
    {
        "reddit.com",
        "quora.com",
        "forums.lenovo.com",
        "discussions.apple.com",
        "linustechtips.com",
        "xda-developers.com",
    }
)

# Authority Weights specified in design
SOURCE_WEIGHTS: dict[SourceType, float] = {
    "manufacturer": 1.00,
    "official_documentation": 1.00,
    "official_support": 0.95,
    "major_retailer": 0.75,
    "established_tech_review": 0.70,
    "forum": 0.45,
    "unknown_blog": 0.30,
}


@dataclass(frozen=True, slots=True)
class ScoredSource:
    url: str
    domain: str
    source_type: SourceType
    trust_score: float


class SourceRanker:
    """Classifies and ranks search result URLs by institutional authority."""

    @classmethod
    def evaluate_source(cls, url: str) -> ScoredSource:
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = (parsed.hostname or "").lower()
            if hostname.startswith("www."):
                hostname = hostname[4:]
        except Exception:
            hostname = ""

        # 1. Support subdomains (support.apple.com, support.lenovo.com)
        if "support." in hostname or "help." in hostname:
            return ScoredSource(
                url=url,
                domain=hostname,
                source_type="official_support",
                trust_score=SOURCE_WEIGHTS["official_support"],
            )

        # 2. Official Documentation & PSREF
        if (
            hostname in _DOCUMENTATION_SUBDOMAINS
            or "psref." in hostname
            or "docs." in hostname
            or "developer." in hostname
            or "manual" in hostname
        ):
            return ScoredSource(
                url=url,
                domain=hostname,
                source_type="official_documentation",
                trust_score=SOURCE_WEIGHTS["official_documentation"],
            )

        # 3. Direct manufacturer root domains
        if hostname in _MANUFACTURER_DOMAINS or any(
            hostname.endswith("." + m) for m in _MANUFACTURER_DOMAINS
        ):
            return ScoredSource(
                url=url,
                domain=hostname,
                source_type="manufacturer",
                trust_score=SOURCE_WEIGHTS["manufacturer"],
            )

        # 4. Major Retailers
        if hostname in _RETAILER_DOMAINS or any(
            hostname.endswith("." + r) for r in _RETAILER_DOMAINS
        ):
            return ScoredSource(
                url=url,
                domain=hostname,
                source_type="major_retailer",
                trust_score=SOURCE_WEIGHTS["major_retailer"],
            )

        # 5. Established Tech Review Sites
        if hostname in _ESTABLISHED_REVIEWS or any(
            hostname.endswith("." + e) for e in _ESTABLISHED_REVIEWS
        ):
            return ScoredSource(
                url=url,
                domain=hostname,
                source_type="established_tech_review",
                trust_score=SOURCE_WEIGHTS["established_tech_review"],
            )

        # 6. Forums / Community Discussions
        if hostname in _FORUM_DOMAINS or any(hostname.endswith("." + f) for f in _FORUM_DOMAINS):
            return ScoredSource(
                url=url,
                domain=hostname,
                source_type="forum",
                trust_score=SOURCE_WEIGHTS["forum"],
            )

        # 7. Default Unknown Blog
        return ScoredSource(
            url=url,
            domain=hostname or "external",
            source_type="unknown_blog",
            trust_score=SOURCE_WEIGHTS["unknown_blog"],
        )
