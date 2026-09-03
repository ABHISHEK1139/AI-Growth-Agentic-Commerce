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

        # Find ingestion run
        run_query = session.query(IngestionRun)
        if ingestion_run_id:
            run = run_query.filter(IngestionRun.run_id == ingestion_run_id).first()
        else:
            run = run_query.order_by(IngestionRun.started_at.desc()).first()

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
        seen_ext_ids: set[str] = set()

        for stg in staged_records:
            raw = stg.raw_payload
            ext_id = (
                stg.source_record_id
                or raw.get("external_product_id")
                or raw.get("asin")
                or raw.get("product_id")
                or new_id("ext")
            )

            if ext_id in seen_ext_ids:
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
                average_rating=float(raw.get("average_rating") or 4.5),
                rating_number=int(raw.get("rating_number") or 10),
                created_at=datetime.now(UTC),
            )
            session.merge(prod)

            # 2. Product Images (URLs only, no heavy blobs)
            images = raw.get("images") or []
            if isinstance(images, list):
                for idx, img in enumerate(images[:5]):
                    if isinstance(img, dict):
                        img_url = img.get("large") or img.get("hi_res") or img.get("thumb") or ""
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

            # Extract or derive deterministic price (in paise)
            price_val = raw.get("price") or raw.get("source_price") or 49999
            if isinstance(price_val, (int, float)) and price_val > 0:
                if price_val < 1000:  # If stored in USD or raw float, convert to ₹ demo price
                    unit_price_minor = int(price_val * 85 * 100)
                else:
                    unit_price_minor = int(price_val * 100)
            else:
                unit_price_minor = 3999000  # ₹39,990 default

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

        # Atomically publish the catalog version
        cat_version.status = "published"
        cat_version.product_count = promoted_count
        cat_version.valid_count = promoted_count
        cat_version.published_at = datetime.now(UTC)

        session.commit()

        print(
            f"\n[SUCCESS] Promoted {promoted_count} products into Catalog Version {catalog_v_id} (Published)!"
        )
        return {
            "catalog_version_id": catalog_v_id,
            "merchant_id": merchant_id,
            "promoted_count": promoted_count,
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
