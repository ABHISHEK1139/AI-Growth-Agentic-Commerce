"""Repository implementations for catalog entities with tenant isolation."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import text
from sqlalchemy.orm import Session

from packages.db.repository import TenantScopedRepository
from services.catalog.models import (
    CatalogVersion,
    ImportRun,
    Product,
)


class CatalogVersionRepository(TenantScopedRepository[CatalogVersion]):
    model: ClassVar[Any] = CatalogVersion
    merchant_column: ClassVar[str] = "merchant_id"

    def get_published(self) -> CatalogVersion | None:
        """Fetch the currently published catalog version for this tenant."""
        stmt = self.scoped_select().where(CatalogVersion.status == "published")
        rows = list(self.scalars(stmt))
        return rows[0] if rows else None

    def get_by_id(self, catalog_version_id: str) -> CatalogVersion | None:
        """Fetch a catalog version by its ID within this tenant."""
        return self.get(catalog_version_id)


class ProductRepository(TenantScopedRepository[Product]):
    model: ClassVar[Any] = Product
    merchant_column: ClassVar[str] = "merchant_id"

    def get_by_external_id(
        self, catalog_version_id: str, external_product_id: str
    ) -> Product | None:
        stmt = self.scoped_select().where(
            Product.catalog_version_id == catalog_version_id,
            Product.external_product_id == external_product_id,
        )
        rows = list(self.scalars(stmt))
        return rows[0] if rows else None

    def list_by_version(self, catalog_version_id: str, limit: int | None = None) -> list[Product]:
        stmt = self.scoped_select().where(Product.catalog_version_id == catalog_version_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.scalars(stmt))


class ImportRunRepository(TenantScopedRepository[ImportRun]):
    model: ClassVar[Any] = ImportRun
    merchant_column: ClassVar[str] = "merchant_id"

    def get_by_checksum(self, source_checksum: str) -> ImportRun | None:
        stmt = self.scoped_select().where(ImportRun.source_checksum == source_checksum)
        rows = list(self.scalars(stmt))
        return rows[0] if rows else None


def atomic_publish_catalog_version(
    session: Session, *, merchant_id: str, catalog_version_id: str
) -> bool:
    """Atomically supersede any existing published version and publish the new version.

    All operations occur within the caller's transaction.
    """
    # 1. Supersede previously published version(s)
    supersede_stmt = text(
        """
        UPDATE catalog_version
           SET status = 'superseded'
         WHERE merchant_id = :merchant_id
           AND status = 'published'
        """
    )
    session.execute(supersede_stmt, {"merchant_id": merchant_id})

    # 2. Publish target version
    publish_stmt = text(
        """
        UPDATE catalog_version
           SET status = 'published',
               published_at = now()
         WHERE catalog_version_id = :catalog_version_id
           AND merchant_id = :merchant_id
           AND status IN ('draft', 'validating')
        RETURNING catalog_version_id
        """
    )
    row = session.execute(
        publish_stmt,
        {"catalog_version_id": catalog_version_id, "merchant_id": merchant_id},
    ).fetchone()
    return row is not None
