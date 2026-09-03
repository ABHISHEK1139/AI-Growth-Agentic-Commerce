"""Deterministic price hashing for checkout and authorization verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    """Immutable representation of all price-determining factors for a checkout."""

    offer_id: str
    offer_version: int
    unit_price_minor: int
    quantity: int
    shipping_minor: int
    tax_minor: int
    discount_minor: int
    currency: str
    expires_at: str | datetime

    def to_canonical_dict(self) -> dict[str, Any]:
        expires_str = (
            self.expires_at.isoformat()
            if isinstance(self.expires_at, datetime)
            else str(self.expires_at)
        )
        return {
            "offer_id": self.offer_id,
            "offer_version": self.offer_version,
            "unit_price_minor": self.unit_price_minor,
            "quantity": self.quantity,
            "shipping_minor": self.shipping_minor,
            "tax_minor": self.tax_minor,
            "discount_minor": self.discount_minor,
            "currency": self.currency,
            "expires_at": expires_str,
        }


PRICE_FACTORS = (
    "offer_id",
    "offer_version",
    "unit_price_minor",
    "quantity",
    "shipping_minor",
    "tax_minor",
    "discount_minor",
    "currency",
    "expires_at",
)


def compute_price_hash(snapshot: PriceSnapshot | dict[str, Any]) -> str:
    """Compute SHA-256 hash over canonical sorted JSON serialization of price factors."""
    if isinstance(snapshot, PriceSnapshot):
        data = snapshot.to_canonical_dict()
    else:
        data = {k: snapshot[k] for k in PRICE_FACTORS if k in snapshot}

    if isinstance(data.get("expires_at"), datetime):
        data["expires_at"] = data["expires_at"].isoformat()

    canonical_json = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
