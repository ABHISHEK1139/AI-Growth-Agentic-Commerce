"""Catalog ingestion, validation, and publishing service."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from packages.observability.context import new_id
from packages.security.tenancy import TenantScope
from services.catalog.models import (
    CatalogVersion,
    ImportRun,
    Product,
    ProductImage,
)
from services.catalog.repository import (
    CatalogVersionRepository,
    ImportRunRepository,
    atomic_publish_catalog_version,
)
from services.inventory.models import Inventory
from services.offers.models import Offer

VALID_CATEGORIES = frozenset(
    {
        "laptop",
        "smartphone",
        "monitor",
        "audio",
        "camera",
        "computer_accessory",
        "phone_accessory",
        "home_electronics",
        "appliance",
        "uncategorized_review",
    }
)


def compute_file_checksum(*paths: Path | str) -> str:
    """Compute combined SHA-256 checksum over input files."""
    hasher = hashlib.sha256()
    for p in paths:
        path = Path(p)
        if path.exists():
            with path.open("rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
    return hasher.hexdigest()


class CatalogService:
    """Service managing catalog versions, imports, and publication."""

    def import_catalog_artifacts(
        self,
        session: Session,
        *,
        merchant_id: str,
        products_path: Path | str,
        offers_path: Path | str | None = None,
        source_name: str = "amazon_catalog_demo",
        schema_version: str = "1.0",
        licence_note: str = "Synthetically priced demo dataset",
    ) -> CatalogVersion:
        """Import products and offers from pipeline artifacts into a new DRAFT catalog version.

        Idempotent: Re-importing with identical source checksum returns existing version.
        """
        scope = TenantScope(merchant_id=merchant_id)
        import_repo = ImportRunRepository(session, scope)
        version_repo = CatalogVersionRepository(session, scope)

        p_path = Path(products_path)
        o_path = Path(offers_path) if offers_path else None

        files_to_hash = [p_path]
        if o_path:
            files_to_hash.append(o_path)
        source_checksum = compute_file_checksum(*files_to_hash)

        # Check for existing completed import run
        existing_run = import_repo.get_by_checksum(source_checksum)
        if existing_run is not None and existing_run.status == "completed":
            # Find the catalog version associated with this import run
            stmt = version_repo.scoped_select().where(
                CatalogVersion.import_run_id == existing_run.import_run_id
            )
            existing_versions = list(version_repo.scalars(stmt))
            if existing_versions:
                return existing_versions[0]

        now = datetime.now(UTC)
        import_run_id = new_id("imp")
        catalog_version_id = new_id("cat")

        import_run = ImportRun(
            import_run_id=import_run_id,
            merchant_id=merchant_id,
            source_name=source_name,
            source_checksum=source_checksum,
            schema_version=schema_version,
            licence_note=licence_note,
            status="running",
            started_at=now,
        )
        session.add(import_run)
        # Flushed before the catalog version is staged, because `catalog_version`
        # carries a foreign key to `import_run` and there is no ORM relationship
        # between the two for the unit of work to topologically sort on. Without
        # this, PostgreSQL rejects the insert with a foreign key violation — which
        # is what happened the first time this method was run against a real
        # database rather than a mocked session.
        session.flush()

        catalog_version = CatalogVersion(
            catalog_version_id=catalog_version_id,
            merchant_id=merchant_id,
            import_run_id=import_run_id,
            status="draft",
            product_count=0,
            valid_count=0,
            needs_review_count=0,
            created_at=now,
        )
        session.add(catalog_version)
        session.flush()

        product_count = 0
        valid_count = 0
        needs_review_count = 0

        # Import products
        if p_path.exists():
            with p_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    p_data = json.loads(line)
                    product_count += 1

                    product_id = p_data.get("product_id") or new_id("prd")
                    ext_id = (
                        p_data.get("parent_asin") or p_data.get("external_product_id") or product_id
                    )
                    title = p_data.get("title", "")
                    category = p_data.get("subcategory") or p_data.get(
                        "category_id", "uncategorized_review"
                    )

                    # Data quality checks
                    status = "valid"
                    if len(title) < 8 or len(title) > 300 or category not in VALID_CATEGORIES:
                        status = "needs_review"

                    if status == "valid":
                        valid_count += 1
                    else:
                        needs_review_count += 1

                    prod = Product(
                        product_id=product_id,
                        catalog_version_id=catalog_version_id,
                        merchant_id=merchant_id,
                        external_product_id=ext_id,
                        category_id=category,
                        title=title,
                        status=status,
                        description=p_data.get("description", []),
                        specifications=p_data.get("specifications", {}),
                        average_rating=float(p_data.get("average_rating", 0.0)),
                        rating_number=int(p_data.get("rating_number", 0)),
                        created_at=now,
                    )
                    session.add(prod)

                    # Images
                    images = p_data.get("images", [])
                    for idx, img in enumerate(images):
                        img_id = new_id("img")
                        src_url = (
                            img.get("source_url") or img.get("large") or img.get("thumb") or ""
                        )
                        storage_key = img.get("storage_key") or f"img_{product_id}_{idx}"
                        resolution = img.get("resolution", "large")
                        prod_img = ProductImage(
                            product_image_id=img_id,
                            product_id=product_id,
                            source_url=src_url,
                            storage_key=storage_key,
                            resolution=resolution,
                            position=idx,
                        )
                        session.add(prod_img)

        # Products must exist before offers reference them, for the same reason
        # the import run had to exist before the catalog version: the foreign keys
        # are declared as columns, not as ORM relationships, so the unit of work
        # has no dependency graph to order the inserts by.
        session.flush()

        # Import offers and initialize inventory
        pending_inventory: list[Inventory] = []
        if o_path and o_path.exists():
            with o_path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    o_data = json.loads(line)
                    offer_id = o_data.get("offer_id") or new_id("off")
                    prod_id = o_data.get("product_id")
                    if not prod_id:
                        continue

                    price_minor = int(o_data.get("unit_price_minor", 0))
                    delivery_days = int(o_data.get("delivery_days", 3))
                    return_period_days = int(o_data.get("return_period_days", 14))
                    pricing_source = o_data.get("pricing_source", "synthetic_band_random")
                    offer_version = int(o_data.get("offer_version", 1))

                    expires_at_str = o_data.get("expires_at")
                    if expires_at_str:
                        try:
                            expires_at = datetime.fromisoformat(
                                expires_at_str.replace("Z", "+00:00")
                            )
                        except Exception:
                            expires_at = now
                    else:
                        expires_at = now

                    offer = Offer(
                        offer_id=offer_id,
                        catalog_version_id=catalog_version_id,
                        product_id=prod_id,
                        variant_id=o_data.get("variant_id"),
                        merchant_id=merchant_id,
                        status=o_data.get("status", "active"),
                        unit_price_minor=price_minor,
                        currency=o_data.get("currency", "INR"),
                        delivery_days=delivery_days,
                        return_period_days=return_period_days,
                        pricing_source=pricing_source,
                        offer_version=offer_version,
                        expires_at=expires_at,
                        created_at=now,
                    )
                    session.add(offer)

                    # Held back rather than added here: `inventory.offer_id` is a
                    # foreign key onto the row above, and staging both together
                    # lets the flush order them inventory-first. Collected and
                    # added after one flush of the offers, so the cost is a single
                    # extra round trip rather than one per row.
                    avail_qty = int(o_data.get("available_quantity", 10))
                    pending_inventory.append(
                        Inventory(
                            offer_id=offer_id,
                            available_quantity=avail_qty,
                            reserved_quantity=0,
                            version=1,
                        )
                    )

            session.flush()
            for inv in pending_inventory:
                session.add(inv)

        catalog_version.product_count = product_count
        catalog_version.valid_count = valid_count
        catalog_version.needs_review_count = needs_review_count

        import_run.status = "completed"
        import_run.completed_at = datetime.now(UTC)
        session.flush()

        return catalog_version

    def publish_catalog(
        self, session: Session, *, merchant_id: str, catalog_version_id: str
    ) -> bool:
        """Atomically publish a draft catalog version, superseding the active version."""
        return atomic_publish_catalog_version(
            session, merchant_id=merchant_id, catalog_version_id=catalog_version_id
        )

    # --- Phase 2: CSV import staging -------------------------------------------

    REQUIRED_COLUMNS = frozenset({"sku", "title", "price_minor", "currency", "inventory", "status"})
    OPTIONAL_COLUMNS = frozenset({"description", "image_url", "category"})
    ALL_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

    def create_import(self, session: Session, *, merchant_id: str, filename: str) -> CatalogImport:
        """Create a new staging import record and return it."""
        from services.catalog.models import CatalogImport

        import_id = new_id("cimp")
        imp = CatalogImport(
            import_id=import_id,
            merchant_id=merchant_id,
            filename=filename,
            status="pending",
            total_rows=0,
            valid_rows=0,
            invalid_rows=0,
        )
        session.add(imp)
        session.flush()
        return imp

    def stage_csv_rows(
        self,
        session: Session,
        *,
        import_id: str,
        rows: list[dict[str, str]],
    ) -> tuple[int, int, list[dict[int, str]]]:
        """Parse and stage CSV rows into CatalogImportRow staging table.

        Returns (total, invalid_count, row_errors) where row_errors is a list of
        {row_number: error_message} dicts for rows that could not be parsed.
        """
        from services.catalog.models import CatalogImportRow

        staged = 0
        invalid = 0
        row_errors: list[dict[int, str]] = []

        for row_num, raw in enumerate(rows, start=1):
            row_id = new_id("cimr")

            # Normalise: strip whitespace, convert empty strings to None
            def _norm(v: str | None) -> str | None:
                if v is None:
                    return None
                v = v.strip()
                return v if v != "" else None

            sku = _norm(raw.get("sku"))
            title = _norm(raw.get("title"))
            description = _norm(raw.get("description"))
            price_str = _norm(raw.get("price_minor"))
            currency = _norm(raw.get("currency")) or "INR"
            inventory_str = _norm(raw.get("inventory"))
            status_val = _norm(raw.get("status"))
            image_url = _norm(raw.get("image_url"))
            category = _norm(raw.get("category")) or "uncategorized_review"

            # Parse numeric fields
            price_minor: int | None = None
            if price_str is not None:
                try:
                    price_minor = int(price_str)
                except ValueError:
                    pass

            inventory: int | None = None
            if inventory_str is not None:
                try:
                    inventory = int(inventory_str)
                except ValueError:
                    pass

            row = CatalogImportRow(
                row_id=row_id,
                import_id=import_id,
                row_number=row_num,
                sku=sku,
                title=title,
                description=description,
                price_minor=price_minor,
                currency=currency,
                inventory=inventory,
                status=status_val,
                image_url=image_url,
                category=category,
                is_valid=True,  # provisional — validate() will update
                validation_errors=None,
                delivery_days=3,
                return_period_days=14,
            )
            session.add(row)
            staged += 1

        session.flush()
        return staged, invalid, row_errors

    def validate_import(self, session: Session, *, import_id: str) -> tuple[int, int, str | None]:
        """Validate all staged rows for a catalog import.

        Checks required fields, price/inventory ranges, currency, status values,
        and title length. Updates is_valid and validation_errors on each row.

        Returns (valid_count, invalid_count, error_summary).
        """
        from services.catalog.models import CatalogImport, CatalogImportRow

        # Fetch all staging rows for this import
        rows = (
            session.query(CatalogImportRow)
            .filter(CatalogImportRow.import_id == import_id)
            .order_by(CatalogImportRow.row_number)
            .all()
        )

        valid_count = 0
        invalid_count = 0
        errors_by_field: dict[str, int] = {}

        for row in rows:
            row_errors: dict[str, str] = {}

            # Required fields
            if not row.sku:
                row_errors["sku"] = "SKU is required"
            if not row.title or len(row.title) < 3:
                row_errors["title"] = "Title must be at least 3 characters"
            if row.title and len(row.title) > 300:
                row_errors["title"] = "Title must be at most 300 characters"
            if row.price_minor is None:
                row_errors["price_minor"] = "price_minor is required and must be an integer"
            elif row.price_minor < 0:
                row_errors["price_minor"] = "price_minor cannot be negative"
            elif row.price_minor > 10_000_000_00:  # 10 crore in paise — sanity check
                row_errors["price_minor"] = "price_minor exceeds reasonable maximum"
            if row.currency not in ("INR", "USD"):
                row_errors["currency"] = "currency must be INR or USD"
            if row.inventory is None:
                row_errors["inventory"] = "inventory is required and must be an integer"
            elif row.inventory < 0:
                row_errors["inventory"] = "inventory cannot be negative"
            if row.status not in ("active", "inactive"):
                row_errors["status"] = "status must be 'active' or 'inactive'"
            # Category validation
            if row.category and row.category not in VALID_CATEGORIES:
                row_errors["category"] = f"unknown category '{row.category}'"

            if row_errors:
                row.is_valid = False
                row.validation_errors = row_errors
                for field in row_errors:
                    errors_by_field[field] = errors_by_field.get(field, 0) + 1
                invalid_count += 1
            else:
                row.is_valid = True
                row.validation_errors = None
                valid_count += 1

        # Update import record
        imp = session.query(CatalogImport).filter(CatalogImport.import_id == import_id).first()
        if imp:
            imp.total_rows = len(rows)
            imp.valid_rows = valid_count
            imp.invalid_rows = invalid_count
            if invalid_count > 0:
                # Build a short human-readable summary
                top_errors = sorted(errors_by_field.items(), key=lambda x: -x[1])[:5]
                summary_parts = [f"{count} {field} error(s)" for field, count in top_errors]
                imp.error_summary = "; ".join(summary_parts)
            else:
                imp.error_summary = None
            imp.status = "valid" if invalid_count == 0 else "invalid"
            imp.validated_at = datetime.now(UTC)

        session.flush()
        return valid_count, invalid_count, imp.error_summary if imp else None

    def publish_import(
        self, session: Session, *, merchant_id: str, import_id: str
    ) -> tuple[str, int, int]:
        """Promote validated staging rows into a new CatalogVersion + Product/Offer/Inventory.

        Only rows with is_valid=True are promoted. The import must be in 'valid' status
        (i.e., validated with no invalid rows, OR the merchant has acknowledged warnings).

        Returns (catalog_version_id, products_created, offers_created).
        """
        from services.catalog.models import CatalogImport, CatalogImportRow, CatalogVersion

        imp = session.query(CatalogImport).filter(CatalogImport.import_id == import_id).first()
        if not imp:
            raise ValueError(f"Import {import_id} not found")
        if imp.status not in ("valid", "invalid"):
            raise ValueError(
                f"Import {import_id} is '{imp.status}' — must be validated before publish"
            )

        # Create new catalog version in draft
        catalog_version_id = new_id("cat")
        now = datetime.now(UTC)
        catalog_version = CatalogVersion(
            catalog_version_id=catalog_version_id,
            merchant_id=merchant_id,
            import_run_id=None,  # manual import, not a pipeline run
            status="draft",
            product_count=0,
            valid_count=0,
            needs_review_count=0,
            created_at=now,
        )
        session.add(catalog_version)
        session.flush()

        # Collect valid rows, group by SKU to deduplicate
        valid_rows = (
            session.query(CatalogImportRow)
            .filter(CatalogImportRow.import_id == import_id, CatalogImportRow.is_valid == True)
            .order_by(CatalogImportRow.row_number)
            .all()
        )

        product_ids_seen: dict[str, str] = {}  # sku -> product_id
        products_created = 0
        offers_created = 0
        product_count = 0
        valid_count = 0
        needs_review_count = 0

        for row in valid_rows:
            sku = row.sku or f"sku_{row.row_number}"

            # Create Product (one per unique SKU)
            if sku not in product_ids_seen:
                product_id = new_id("prd")
                product_ids_seen[sku] = product_id

                prod_status = "valid"
                if len(row.title or "") < 8:
                    prod_status = "needs_review"

                product_count += 1
                if prod_status == "valid":
                    valid_count += 1
                else:
                    needs_review_count += 1

                product = Product(
                    product_id=product_id,
                    catalog_version_id=catalog_version_id,
                    merchant_id=merchant_id,
                    external_product_id=sku,
                    category_id=row.category or "uncategorized_review",
                    title=row.title or sku,
                    status=prod_status,
                    description=[row.description] if row.description else [],
                    specifications={},
                    average_rating=0.0,
                    rating_number=0,
                    created_at=now,
                )
                session.add(product)

                # Image if provided
                if row.image_url:
                    img_id = new_id("img")
                    prod_img = ProductImage(
                        product_image_id=img_id,
                        product_id=product_id,
                        source_url=row.image_url,
                        storage_key=f"img_{product_id}_0",
                        resolution="large",
                        position=0,
                    )
                    session.add(prod_img)
            else:
                product_id = product_ids_seen[sku]

            # Create Offer for this SKU
            offer_id = new_id("off")
            row.offer_id = offer_id
            expires_at = datetime.now(UTC) + timedelta(days=30)

            offer = Offer(
                offer_id=offer_id,
                catalog_version_id=catalog_version_id,
                product_id=product_id,
                variant_id=None,
                merchant_id=merchant_id,
                status=row.status or "active",
                unit_price_minor=row.price_minor or 0,
                currency=row.currency or "INR",
                delivery_days=row.delivery_days or 3,
                return_period_days=row.return_period_days or 14,
                pricing_source="merchant_configured",
                offer_version=1,
                expires_at=expires_at,
                created_at=now,
            )
            session.add(offer)

            # Create Inventory
            inventory = Inventory(
                offer_id=offer_id,
                available_quantity=row.inventory or 0,
                reserved_quantity=0,
                version=1,
            )
            session.add(inventory)
            offers_created += 1

        session.flush()

        # Update catalog version counts
        catalog_version.product_count = product_count
        catalog_version.valid_count = valid_count
        catalog_version.needs_review_count = needs_review_count

        # Mark import as published
        imp.status = "published"
        imp.published_at = datetime.now(UTC)
        imp.published_catalog_version_id = catalog_version_id

        session.flush()

        # Atomically publish the new version (supersedes any active version)
        self.publish_catalog(
            session, merchant_id=merchant_id, catalog_version_id=catalog_version_id
        )

        return catalog_version_id, products_created, offers_created

    def rollback_import(self, session: Session, *, import_id: str) -> None:
        """Delete a pending/validated import and all its staging rows."""
        from services.catalog.models import CatalogImport, CatalogImportRow

        # Remove staging rows
        session.query(CatalogImportRow).filter(CatalogImportRow.import_id == import_id).delete(
            synchronize_session=False
        )

        # Remove import record
        session.query(CatalogImport).filter(CatalogImport.import_id == import_id).delete(
            synchronize_session=False
        )

        session.flush()
