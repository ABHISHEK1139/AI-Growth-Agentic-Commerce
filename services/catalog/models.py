"""SQLAlchemy ORM models for catalog, products, and merchant entities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Merchant(Base):
    __tablename__ = "merchant"

    merchant_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class Buyer(Base):
    __tablename__ = "buyer"

    buyer_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class ImportRun(Base):
    __tablename__ = "import_run"

    import_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_checksum: Mapped[str] = mapped_column(String, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    licence_note: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CatalogVersion(Base):
    __tablename__ = "catalog_version"

    catalog_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    import_run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("import_run.import_run_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Product(Base):
    __tablename__ = "product"

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    catalog_version_id: Mapped[str] = mapped_column(
        String, ForeignKey("catalog_version.catalog_version_id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    external_product_id: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="valid")
    description: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    specifications: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    average_rating: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rating_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class Variant(Base):
    __tablename__ = "variant"

    variant_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String, ForeignKey("product.product_id"), nullable=False
    )
    external_variant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    specifications: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ProductImage(Base):
    __tablename__ = "product_image"

    product_image_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String, ForeignKey("product.product_id"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    resolution: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Review(Base):
    __tablename__ = "review"

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        String, ForeignKey("product.product_id"), nullable=False
    )
    parent_asin: Mapped[str] = mapped_column(String, nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_purchase: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_file: Mapped[str] = mapped_column(String, nullable=False)
    raw_body_hash: Mapped[str] = mapped_column(String, nullable=False)


class MerchantRules(Base):
    __tablename__ = "merchant_rules"

    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), primary_key=True
    )
    version: Mapped[str] = mapped_column(String, nullable=False)
    max_transaction_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_approval_limit_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    max_discount_basis_points: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    blocked_categories: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_payment_methods: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    allow_out_of_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class CategoryPairing(Base):
    __tablename__ = "category_pairing"

    pairing_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    source_category_id: Mapped[str] = mapped_column(String, nullable=False)
    target_category_id: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --- Phase 2: Merchant CSV import staging -----------------------------------


class CatalogImport(Base):
    """Staging area for a merchant's CSV catalog upload.

    A single upload goes through: upload → validating → valid/invalid → published/rolled_back.
    Rows are staged in ``CatalogImportRow`` until the merchant explicitly publishes,
    at which point they are promoted into ``Product`` / ``Offer`` / ``Inventory`` rows
    in a new ``CatalogVersion``.  Until then the staging rows are invisible to buyers.
    """

    __tablename__ = "catalog_import"

    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchant.merchant_id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # Counts updated after validation
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set when validation completes
    error_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The catalog version this import was published into (null until published)
    published_catalog_version_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("catalog_version.catalog_version_id"), nullable=True
    )


class CatalogImportRow(Base):
    """Individual row from a CSV upload, held in staging until publish."""

    __tablename__ = "catalog_import_row"

    row_id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(
        String, ForeignKey("catalog_import.import_id"), nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    # Normalised column values (null means absent from CSV or failed validation)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    inventory: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    # Validation
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation_errors: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )  # {"field": "error message"}
    # Offer fields derived during validation
    delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    return_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_id: Mapped[str | None] = mapped_column(String, nullable=True)  # assigned at publish
