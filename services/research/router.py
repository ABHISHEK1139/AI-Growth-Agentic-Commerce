"""Research Router for classifying questions into Database, Reviews, or Web Research."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class QuestionTarget(StrEnum):
    CATALOG_METADATA = "catalog_metadata"
    OFFER_PRICE = "offer_price"
    REVIEWS = "reviews"
    WEB_RESEARCH = "web_research"


class ResearchRouter:
    """Deterministically classifies user product questions into appropriate data sources."""

    _OFFER_PRICE_KEYWORDS = frozenset(
        {
            "price",
            "cost",
            "discount",
            "stock",
            "available",
            "buy",
            "checkout",
            "shipping",
            "delivery time",
            "delivery days",
            "when will it deliver",
            "estimated delivery",
            "return policy",
            "warranty",
        }
    )

    _REVIEW_KEYWORDS = frozenset(
        {
            "review",
            "reviews",
            "rating",
            "ratings",
            "customer",
            "customers",
            "complaint",
            "complaints",
            "experience",
            "opinion",
            "comfortable",
            "feel",
            "heating",
            "overheat",
            "fan noise",
            "worth it",
        }
    )

    _DIRECT_METADATA_KEYS = {
        "ram": ("ram", "memory", "ram_gb", "memory_gb"),
        "storage": ("storage", "ssd", "hdd", "storage_gb", "drive"),
        "processor": ("processor", "cpu", "chipset", "soc"),
        "display": ("display", "screen", "screen_size", "resolution"),
        "os": ("os", "operating_system", "windows", "macos"),
        "weight": ("weight", "dimensions", "thickness"),
        "color": ("color", "colour", "finish"),
    }

    _DEEP_SPEC_KEYWORDS = frozenset(
        {
            "usb 3",
            "usb3",
            "usb-a 3",
            "thunderbolt",
            "hdmi 2",
            "displayport",
            "expandable",
            "upgradable",
            "power delivery",
            "nit",
            "nits",
            "srgb",
            "dci-p3",
            "color gamut",
            "refresh rate",
            "external display",
            "dual monitor",
            "bios",
            "firmware",
            "driver",
            "compatibility",
            "linux",
            "ubuntu",
            "battery wh",
            "watt hour",
            "charging speed",
        }
    )

    @classmethod
    def classify(
        cls,
        question: str,
        catalog_specs: dict[str, Any] | None = None,
    ) -> tuple[QuestionTarget, str | None]:
        """Classify question and return (target_source, matching_spec_or_reason)."""
        q_clean = question.lower().strip()
        specs = catalog_specs or {}
        specs_lower = {str(k).lower(): str(v).lower() for k, v in specs.items()}

        # 1. Check for Deep / Specific Technical Hardware Inquiries FIRST
        if any(kw in q_clean for kw in cls._DEEP_SPEC_KEYWORDS):
            for spec_key, spec_val in specs_lower.items():
                if any(kw in spec_key or kw in spec_val for kw in cls._DEEP_SPEC_KEYWORDS):
                    return QuestionTarget.CATALOG_METADATA, f"{spec_key}: {specs.get(spec_key)}"
            return QuestionTarget.WEB_RESEARCH, "missing_deep_technical_spec"

        # 2. Check for Offer & Commerce Questions (Price, stock, checkout)
        if any(kw in q_clean for kw in cls._OFFER_PRICE_KEYWORDS):
            return QuestionTarget.OFFER_PRICE, "offer_pricing_and_inventory"

        # 3. Check for Review & Sentiment Questions
        if any(kw in q_clean for kw in cls._REVIEW_KEYWORDS):
            return QuestionTarget.REVIEWS, "customer_reviews_and_ratings"

        # 4. Check for Standard Catalog Specs (RAM, Storage, CPU, Display)
        for category, aliases in cls._DIRECT_METADATA_KEYS.items():
            if any(alias in q_clean for alias in aliases):
                # Check if catalog holds this spec
                for orig_k, orig_v in specs.items():
                    if any(alias in str(orig_k).lower() for alias in aliases):
                        return QuestionTarget.CATALOG_METADATA, f"{orig_k}: {orig_v}"
                # If stated in question but missing from catalog attributes, trigger web research
                return QuestionTarget.WEB_RESEARCH, f"missing_spec_{category}"

        # 5. Default to Web Research if inquiry is technical / factual but unresolved
        return QuestionTarget.WEB_RESEARCH, "unresolved_product_inquiry"
