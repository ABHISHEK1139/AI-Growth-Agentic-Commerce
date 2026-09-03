"""Research Query Planner for constructing targeted search queries."""

from __future__ import annotations

import re


class ResearchPlanner:
    """Constructs precise, domain-targeted search queries from product title and question."""

    _BRAND_DOMAINS = {
        "lenovo": "lenovo.com",
        "thinkpad": "lenovo.com",
        "ideapad": "lenovo.com",
        "apple": "apple.com",
        "macbook": "apple.com",
        "dell": "dell.com",
        "xps": "dell.com",
        "hp": "hp.com",
        "asus": "asus.com",
        "zenbook": "asus.com",
        "rog": "asus.com",
        "sony": "sony.com",
        "samsung": "samsung.com",
        "bose": "bose.com",
        "logitech": "logitech.com",
    }

    @classmethod
    def detect_manufacturer_domain(cls, product_title: str) -> str | None:
        title_lower = product_title.lower()
        for brand, domain in cls._BRAND_DOMAINS.items():
            if brand in title_lower:
                return domain
        return None

    @classmethod
    def extract_spec_focus(cls, question: str) -> str:
        """Extract the core technical concept being inquired about."""
        q_clean = question.strip()
        # Remove common question prefixes
        q_clean = re.sub(
            r"^(does\s+this|is\s+there|what\s+is\s+the|how\s+many|can\s+this|does\s+it\s+have)\s+",
            "",
            q_clean,
            flags=re.IGNORECASE,
        )
        q_clean = q_clean.rstrip("?").strip()
        return q_clean or "specifications"

    @classmethod
    def build_search_queries(cls, product_title: str, question: str) -> list[str]:
        """Generate targeted search queries ordered by specificity."""
        manufacturer_domain = cls.detect_manufacturer_domain(product_title)
        spec_focus = cls.extract_spec_focus(question)

        queries: list[str] = []
        if manufacturer_domain:
            # 1. Site-targeted query
            queries.append(f'site:{manufacturer_domain} "{product_title}" {spec_focus}')
            # 2. PSREF / Docs targeted query for Lenovo
            if "lenovo" in manufacturer_domain:
                queries.append(f'site:psref.lenovo.com "{product_title}" ports')

        # 3. Precise quotes query
        queries.append(f'"{product_title}" "{spec_focus}" official specifications')
        # 4. Fallback technical query
        queries.append(f"{product_title} {spec_focus} tech specs")

        return queries
