"""Promote staged quarantine records into validated Products, Images, Offers, and Inventory.

Enforces:
1. Stable deterministic Product ID: merchant_id + ":" + external_product_id
2. Separation of Product (static metadata) and Offer (live commerce state)
3. Separate image URL reference table (no heavy binary blobs in PostgreSQL)
4. Atomic Catalog Version draft -> published lifecycle
5. Initial deterministic inventory hold counters
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.observability.context import new_id
from services.catalog.models import CatalogVersion, Merchant, Product, ProductImage
from services.db.session import get_session_factory
from services.inventory.models import Inventory
from services.offers.models import Offer
from services.staging.models import IngestionRun, StagingCatalogRaw


def make_stable_id(prefix: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


#: USD→INR conversion used for bare dataset prices, stated once so the
#: promotion math is auditable instead of a magic 85 buried in a branch.
USD_TO_INR_RATE = Decimal("85")


def parse_price_minor(raw: dict[str, Any]) -> int | None:
    """Extract an INR minor-unit price from a staged payload, or None.

    Explicit ``*_minor`` integer fields win. Otherwise a bare ``price`` is
    interpreted as USD major units (the dataset's convention) and converted.
    Strings like ``"$29.99"`` parse; anything unparseable or non-positive
    yields None so the caller can quarantine the row instead of inventing a
    price for it.
    """
    for key in ("price_minor", "source_price_minor", "unit_price_minor"):
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
    for key in ("price", "source_price"):
        value = raw.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            decimal = Decimal(str(value).strip().lstrip("$").replace(",", ""))
        except InvalidOperation:
            continue
        if decimal <= 0:
            continue
        return int((decimal * USD_TO_INR_RATE * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return None


def safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def promote_staging_run(
    merchant_id: str = "merchant_demo",
    ingestion_run_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    SessionLocal = get_session_factory()

    with SessionLocal() as session:
        # Ensure merchant exists
        merch = session.query(Merchant).filter(Merchant.merchant_id == merchant_id).first()
        if not merch:
            merch = Merchant(
                merchant_id=merchant_id, name="AgentPay Flagship Store", status="active"
            )
            session.add(merch)
            session.flush()

        # Find ingestion run: an explicit id is honored as given, but the
        # default is the latest *completed* run — a stranded "running" or
        # "failed" row must never be promoted as an empty success.
        run_query = session.query(IngestionRun)
        if ingestion_run_id:
            run = run_query.filter(IngestionRun.run_id == ingestion_run_id).first()
        else:
            run = (
                run_query.filter(IngestionRun.status == "completed")
                .order_by(IngestionRun.started_at.desc())
                .first()
            )

        if not run:
            raise RuntimeError("No ingestion run found to promote.")

        actual_run_id = run.run_id
        print(f"\n--- Promoting Ingestion Run: {actual_run_id} for Merchant: {merchant_id} ---")

        # Create new CatalogVersion
        catalog_v_id = new_id("cat")
        cat_version = CatalogVersion(
            catalog_version_id=catalog_v_id,
            merchant_id=merchant_id,
            import_run_id=actual_run_id,
            status="draft",
            product_count=0,
            valid_count=0,
            needs_review_count=0,
            created_at=datetime.now(UTC),
        )
        session.add(cat_version)
        session.flush()

        # Query valid staged records
        staged_query = session.query(StagingCatalogRaw).filter(
            StagingCatalogRaw.ingestion_run_id == actual_run_id,
            StagingCatalogRaw.validation_status == "valid",
        )
        if limit:
            staged_query = staged_query.limit(limit)

        staged_records = staged_query.all()
        print(f"Found {len(staged_records)} valid staged records to promote...")

        promoted_count = 0
        skipped_no_id = 0
        skipped_no_price = 0
        skipped_error = 0
        seen_ext_ids: set[str] = set()

        for stg in staged_records:
            nested = session.begin_nested()
            try:
                raw = stg.raw_payload if isinstance(stg.raw_payload, dict) else {}
                ext_id = (
                    stg.source_record_id
                    or raw.get("external_product_id")
                    or raw.get("asin")
                    or raw.get("product_id")
                )
                # No identifier anywhere: inventing a random one would create
                # a *new* product on every run instead of merging the same
                # one, so the row is quarantined, not promoted.
                if not ext_id:
                    skipped_no_id += 1
                    nested.rollback()
                    continue

                if ext_id in seen_ext_ids:
                    nested.rollback()
                    continue
                seen_ext_ids.add(ext_id)

                # Deterministic stable Product ID
                prod_id = make_stable_id("prd", f"{merchant_id}:{ext_id}")
                title = raw.get("title") or raw.get("name") or "Imported Hardware"
                category = (
                    raw.get("category_id")
                    or raw.get("main_category")
                    or stg.source_category
                    or "electronics"
                )

                # Format specifications
                specs = raw.get("specifications") or {}
                if not isinstance(specs, dict):
                    specs = {"raw_specs": str(specs)}

                # Format description
                desc_val = raw.get("description") or raw.get("feature") or []
                if isinstance(desc_val, str):
                    desc_list = [desc_val]
                elif isinstance(desc_val, list):
                    desc_list = [str(x) for x in desc_val if x]
                else:
                    desc_list = [str(desc_val)]

                # 1. Product Record
                prod = Product(
                    product_id=prod_id,
                    catalog_version_id=catalog_v_id,
                    merchant_id=merchant_id,
                    external_product_id=ext_id,
                    category_id=category,
                    title=title,
                    status="valid",
                    description=desc_list,
                    specifications=specs,
                    average_rating=safe_float(raw.get("average_rating"), 4.5),
                    rating_number=safe_int(raw.get("rating_number"), 10),
                    created_at=datetime.now(UTC),
                )
                session.merge(prod)

                # 2. Product Images (URLs only, no heavy blobs)
                images = raw.get("images") or []
                if isinstance(images, list):
                    for idx, img in enumerate(images[:5]):
                        if isinstance(img, dict):
                            img_url = (
                                img.get("large") or img.get("hi_res") or img.get("thumb") or ""
                            )
                        else:
                            img_url = str(img)
                        if img_url:
                            img_id = make_stable_id("img", f"{prod_id}:{idx}:{img_url}")
                            prod_img = ProductImage(
                                product_image_id=img_id,
                                product_id=prod_id,
                                source_url=img_url,
                                storage_key=f"products/{prod_id}/{idx}.jpg",
                                resolution="standard",
                                position=idx,
                            )
                            session.merge(prod_img)

                # 3. Deterministic Live Offer & Inventory
                offer_id = make_stable_id("off", f"{merchant_id}:{prod_id}")

                # A row without a parseable price is quarantined, not defaulted:
                # promoting it at an invented price would sell at a lie.
                unit_price_minor = parse_price_minor(raw)
                if unit_price_minor is None:
                    skipped_no_price += 1
                    nested.rollback()
                    continue

                offer = Offer(
                    offer_id=offer_id,
                    catalog_version_id=catalog_v_id,
                    product_id=prod_id,
                    variant_id=None,
                    merchant_id=merchant_id,
                    status="active",
                    unit_price_minor=unit_price_minor,
                    currency="INR",
                    delivery_days=3,
                    return_period_days=7,
                    pricing_source="synthetic_band_random",
                    offer_version=1,
                    expires_at=datetime(2028, 1, 1, tzinfo=UTC),
                    created_at=datetime.now(UTC),
                )
                session.merge(offer)

                # 4. Inventory Hold Counter
                inv = Inventory(
                    offer_id=offer_id,
                    available_quantity=20,
                    reserved_quantity=0,
                    version=1,
                )
                session.merge(inv)

                stg.validation_status = "promoted"
                promoted_count += 1
            except Exception as row_exc:
                nested.rollback()
                skipped_error += 1
                print(f"   ! row skipped ({type(row_exc).__name__}): {row_exc}")
                continue

        # Atomically publish the catalog version
        cat_version.status = "published"
        cat_version.product_count = promoted_count
        cat_version.valid_count = promoted_count
        cat_version.published_at = datetime.now(UTC)

        session.commit()

        print(
            f"\n[SUCCESS] Promoted {promoted_count} products into Catalog Version {catalog_v_id} (Published)!"
        )
        if skipped_no_id or skipped_no_price or skipped_error:
            print(
                f"   quarantined: {skipped_no_id} without identifier, "
                f"{skipped_no_price} without parseable price, "
                f"{skipped_error} errored rows"
            )
        return {
            "catalog_version_id": catalog_v_id,
            "merchant_id": merchant_id,
            "promoted_count": promoted_count,
            "skipped_no_id": skipped_no_id,
            "skipped_no_price": skipped_no_price,
            "skipped_error": skipped_error,
            "status": "published",
        }


def main():
    parser = argparse.ArgumentParser(description="Promote Staging Records to Catalog")
    parser.add_argument(
        "--merchant-id", type=str, default="merchant_demo", help="Target merchant ID"
    )
    parser.add_argument("--run-id", type=str, default=None, help="Ingestion run ID")
    parser.add_argument(
        "--limit", type=int, default=None, help="Limit number of records to promote"
    )
    args = parser.parse_args()

    promote_staging_run(
        merchant_id=args.merchant_id,
        ingestion_run_id=args.run_id,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
