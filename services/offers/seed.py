"""The offline catalog: the seed import artifacts, read without a database.

Why this exists at all: the unit suite must run with no Docker and no PostgreSQL,
and a demo has to be answerable on a laptop with nothing running. The tempting
shortcut is a list of product dictionaries in a Python module. That is what was
here before, and it produced three defects at once — the searchable set had no
laptop under ₹70,000, its filter semantics diverged from the SQL path, and a
reviewer had no way to tell which catalog had answered.

So this module reads *the import artifacts*, the same two JSONL files
``apps.worker.seed_catalog`` feeds to
:meth:`services.catalog.service.CatalogService.import_catalog_artifacts`. One
dataset, two readers. A price corrected here is corrected in PostgreSQL on the
next import, because there is only one place the price is written.

Filtering is not reimplemented either: :func:`search_seed_candidates` delegates to
:func:`services.offers.constraints.apply_constraints`, which is the Python half of
the pair that ``sql_predicates`` completes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from packages.schemas.v1 import OfferV1, ProductSpecificationsV1
from services.offers.constraints import (
    OfferCandidate,
    OfferConstraints,
    apply_constraints,
)

#: Repository root, derived from this file rather than from application settings.
#: A domain service reading `apps.api.config` would put the API layer inside the
#: service layer, and this module is imported on the offline path precisely
#: because the application's datastore is not available.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_OUT_CATALOG_DIR = _REPO_ROOT / "data" / "out" / "catalog"
_OUT_PRODUCTS_PATH = _OUT_CATALOG_DIR / "products.jsonl"
_OUT_OFFERS_PATH = _OUT_CATALOG_DIR / "offers.jsonl"

SEED_CATALOG_DIR = _REPO_ROOT / "data" / "seed" / "catalog"
SEED_PRODUCTS_PATH = _OUT_PRODUCTS_PATH if _OUT_PRODUCTS_PATH.exists() else SEED_CATALOG_DIR / "products.jsonl"
SEED_OFFERS_PATH = _OUT_OFFERS_PATH if _OUT_OFFERS_PATH.exists() else SEED_CATALOG_DIR / "offers.jsonl"

#: Default when an offer record omits stock, matching the importer's own default
#: so the two readers agree on a record that leaves it out.
DEFAULT_AVAILABLE_QUANTITY = 10


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                records.append(json.loads(stripped))
    return records


def _specifications_of(raw: dict[str, Any]) -> ProductSpecificationsV1:
    """Project a product's raw specifications onto the public schema.

    An absent key stays ``None`` rather than becoming a zero. ``0`` would satisfy
    no ``min_*`` constraint above zero but *would* satisfy one at zero, and more
    importantly it would claim a fact the catalog does not hold.
    """
    dim = raw.get("dimensions_mm") if isinstance(raw.get("dimensions_mm"), dict) else {}
    return ProductSpecificationsV1(
        memory_gb=raw.get("memory_gb"),
        storage_gb=raw.get("storage_gb"),
        weight_grams=raw.get("weight_grams"),
        length_mm=raw.get("length_mm") or (int(dim.get("length_mm")) if dim.get("length_mm") is not None else None),
        width_mm=raw.get("width_mm") or (int(dim.get("width_mm")) if dim.get("width_mm") is not None else None),
        height_mm=raw.get("height_mm") or (int(dim.get("height_mm")) if dim.get("height_mm") is not None else None),
    )


def _primary_image_url(product: dict[str, Any]) -> str | None:
    images = product.get("images") or []
    for image in images:
        url = image.get("url") or image.get("source_url") or image.get("large") or image.get("thumb")
        if url:
            return str(url)
    return None


def _build_candidates(merchant_id: str) -> tuple[OfferCandidate, ...]:
    products = {record["product_id"]: record for record in _read_jsonl(SEED_PRODUCTS_PATH)}
    candidates: list[OfferCandidate] = []

    for record in _read_jsonl(SEED_OFFERS_PATH):
        product = products.get(record.get("product_id", ""))
        if product is None:
            # An offer without its product is a broken artifact, not a result.
            continue

        raw_specs: dict[str, Any] = product.get("specifications") or {}
        available = int(record.get("available_quantity", DEFAULT_AVAILABLE_QUANTITY))

        offer = OfferV1(
            schema_version="1.0",
            offer_id=str(record["offer_id"]),
            product_id=str(record["product_id"]),
            merchant_id=merchant_id,
            status=record.get("status", "active"),
            unit_price_minor=int(record["unit_price_minor"]),
            currency=record.get("currency", "INR"),
            available_quantity=max(0, available),
            delivery_days=int(record.get("delivery_days", 3)),
            return_period_days=int(record.get("return_period_days", 14)),
            expires_at=str(record["expires_at"]),
            offer_version=int(record.get("offer_version", 1)),
            pricing_source=record.get("pricing_source", "synthetic_band_random"),
            specifications=_specifications_of(raw_specs),
        )

        candidates.append(
            OfferCandidate(
                offer=offer,
                category_id=str(product.get("subcategory") or product.get("category_id") or ""),
                title=str(product.get("title", "")),
                average_rating=float(product.get("average_rating", 0.0)),
                rating_number=int(product.get("rating_number", 0)),
                image_url=_primary_image_url(product),
                specifications=dict(raw_specs),
            )
        )

    return tuple(candidates)


@lru_cache(maxsize=4)
def load_seed_candidates(merchant_id: str) -> tuple[OfferCandidate, ...]:
    """Every seed offer, unfiltered. Cached because the artifacts are immutable.

    Cached per merchant because the merchant identifier is stamped onto each
    :class:`~packages.schemas.v1.OfferV1`, exactly as the importer stamps it onto
    the row. Nothing here reads across a tenant: the seed dataset belongs to
    whichever merchant asks for it, which is the same posture the importer takes.
    """
    return _build_candidates(merchant_id)


def search_seed_candidates(
    *,
    merchant_id: str,
    constraints: OfferConstraints,
    now: datetime | None = None,
) -> list[OfferCandidate]:
    """Constrained search over the seed artifacts.

    Every filter, the baseline stock and expiry checks, the ranking, and the limit
    all come from :mod:`services.offers.constraints`. This function contributes no
    filter logic of its own, which is what keeps it from drifting away from SQL.
    """
    return apply_constraints(
        load_seed_candidates(merchant_id),
        constraints,
        now=now or datetime.now(UTC),
    )
