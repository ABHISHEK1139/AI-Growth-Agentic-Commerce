"""Merchant Catalog CSV Import API — Phase 2.

Routes for uploading, validating, previewing, publishing, and rolling back
merchant CSV catalog imports.

Required CSV columns: sku, title, description, price_minor, currency,
inventory, status, image_url, category

Workflow:
    POST   /imports                  → create import record, stage rows
    GET    /imports/{import_id}       → get import status and row preview
    POST   /imports/{import_id}/validate → run validation pass
    POST   /imports/{import_id}/publish → promote to new catalog version
    POST   /imports/{import_id}/rollback → delete staged rows
    GET    /imports/{import_id}/rows → paginated row list with errors
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.api.auth import require_roles
from apps.api.db import get_db
from packages.security.principals import Principal, Role
from services.catalog.service import CatalogService

router = APIRouter(prefix="/api/v1/merchant/catalog", tags=["merchant-catalog"])
DatabaseSession = Annotated[Session, Depends(get_db)]

MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN)),
]


# --- Request/response models -----------------------------------------------


class ImportCreatedResponse(BaseModel):
    import_id: str
    filename: str
    total_rows: int
    status: str


class ImportStatusResponse(BaseModel):
    import_id: str
    merchant_id: str
    filename: str
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    error_summary: str | None
    created_at: str
    validated_at: str | None
    published_at: str | None
    published_catalog_version_id: str | None


class ValidationResultResponse(BaseModel):
    import_id: str
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    error_summary: str | None


class PublishResultResponse(BaseModel):
    import_id: str
    status: str
    catalog_version_id: str
    products_created: int
    offers_created: int


class RowPreviewResponse(BaseModel):
    import_id: str
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


# --- Helpers -------------------------------------------------------------

_SERVICE: CatalogService | None = None


def _catalog_service() -> CatalogService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CatalogService()
    return _SERVICE


def _parse_csv(file_content: bytes) -> list[dict[str, str]]:
    """Parse CSV content into a list of row dicts."""
    text = file_content.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _require_import_in_status(session: Session, import_id: str, allowed: set[str]) -> Any:
    """Load a CatalogImport and verify its status is in the allowed set."""
    from services.catalog.models import CatalogImport

    imp = session.query(CatalogImport).filter(CatalogImport.import_id == import_id).first()
    if not imp:
        raise HTTPException(status_code=404, detail=f"Import {import_id} not found")
    if imp.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Import {import_id} is '{imp.status}'; expected one of {sorted(allowed)}",
        )
    return imp


# --- Routes -------------------------------------------------------------


@router.post("/imports", response_model=ImportCreatedResponse)
def create_import(
    file: UploadFile,
    session: DatabaseSession,
    principal: MerchantPrincipal,
) -> ImportCreatedResponse:
    """Upload a CSV catalog file and stage its rows for validation.

    The CSV must have these columns (case-insensitive):
        sku, title, description, price_minor, currency, inventory, status, image_url, category

    Rows are staged immediately and can be previewed before validation.
    """
    merchant_id = principal.merchant_id or "default"

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Only .csv files are accepted",
        )

    content = file.file.read()
    rows = _parse_csv(content)

    if not rows:
        raise HTTPException(
            status_code=400,
            detail="CSV file is empty",
        )

    # Check required columns (case-insensitive)
    normalized_keys = {k.lower().strip() for k in rows[0].keys()}
    missing = CatalogService.REQUIRED_COLUMNS - normalized_keys
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"CSV is missing required columns: {sorted(missing)}",
        )

    service = _catalog_service()
    imp = service.create_import(session, merchant_id=merchant_id, filename=file.filename)
    total, _invalid, _row_errors = service.stage_csv_rows(
        session, import_id=imp.import_id, rows=rows
    )
    session.commit()

    return ImportCreatedResponse(
        import_id=imp.import_id,
        filename=imp.filename,
        total_rows=total,
        status=imp.status,
    )


@router.get("/imports/{import_id}", response_model=ImportStatusResponse)
def get_import(
    import_id: str,
    session: DatabaseSession,
    _principal: MerchantPrincipal,
) -> ImportStatusResponse:
    """Get the current status of a catalog import."""
    from services.catalog.models import CatalogImport

    imp = session.query(CatalogImport).filter(CatalogImport.import_id == import_id).first()
    if not imp:
        raise HTTPException(status_code=404, detail=f"Import {import_id} not found")

    return ImportStatusResponse(
        import_id=imp.import_id,
        merchant_id=imp.merchant_id,
        filename=imp.filename,
        status=imp.status,
        total_rows=imp.total_rows,
        valid_rows=imp.valid_rows,
        invalid_rows=imp.invalid_rows,
        error_summary=imp.error_summary,
        created_at=imp.created_at.isoformat() if imp.created_at else "",
        validated_at=imp.validated_at.isoformat() if imp.validated_at else None,
        published_at=imp.published_at.isoformat() if imp.published_at else None,
        published_catalog_version_id=imp.published_catalog_version_id,
    )


@router.post("/imports/{import_id}/validate", response_model=ValidationResultResponse)
def validate_import(
    import_id: str,
    session: DatabaseSession,
    principal: MerchantPrincipal,
) -> ValidationResultResponse:
    """Run validation on all staged rows for a catalog import.

    Checks required fields, price/inventory ranges, currency codes, status values,
    and title length. Updates each row's is_valid flag and validation_errors.
    """
    imp = _require_import_in_status(session, import_id, {"pending"})
    service = _catalog_service()
    valid_count, invalid_count, error_summary = service.validate_import(
        session, import_id=import_id
    )
    session.commit()

    # Re-fetch to get updated counts
    from services.catalog.models import CatalogImport

    imp = session.query(CatalogImport).filter(CatalogImport.import_id == import_id).first()

    return ValidationResultResponse(
        import_id=import_id,
        status=imp.status if imp else "unknown",
        total_rows=imp.total_rows if imp else 0,
        valid_rows=valid_count,
        invalid_rows=invalid_count,
        error_summary=error_summary,
    )


@router.get("/imports/{import_id}/rows", response_model=RowPreviewResponse)
def list_import_rows(
    import_id: str,
    session: DatabaseSession,
    _principal: MerchantPrincipal,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    valid_only: bool = Query(default=False),
) -> RowPreviewResponse:
    """List staged rows for an import with pagination.

    Set valid_only=true to return only rows where is_valid=True.
    """
    from services.catalog.models import CatalogImportRow

    query = session.query(CatalogImportRow).filter(CatalogImportRow.import_id == import_id)
    if valid_only:
        query = query.filter(CatalogImportRow.is_valid == True)

    total = query.count()
    offset = (page - 1) * page_size
    rows = query.order_by(CatalogImportRow.row_number).offset(offset).limit(page_size).all()

    return RowPreviewResponse(
        import_id=import_id,
        rows=[
            {
                "row_number": r.row_number,
                "sku": r.sku,
                "title": r.title,
                "price_minor": r.price_minor,
                "currency": r.currency,
                "inventory": r.inventory,
                "status": r.status,
                "category": r.category,
                "is_valid": r.is_valid,
                "validation_errors": r.validation_errors,
            }
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/imports/{import_id}/publish", response_model=PublishResultResponse)
def publish_import(
    import_id: str,
    session: DatabaseSession,
    principal: MerchantPrincipal,
) -> PublishResultResponse:
    """Publish a validated catalog import.

    Only imports in 'valid' status (no invalid rows) can be published.
    This promotes all valid rows into a new CatalogVersion, creates Product/Offer/Inventory
    rows, and atomically publishes the new version (superseding any active version).
    """
    merchant_id = principal.merchant_id or "default"
    imp = _require_import_in_status(session, import_id, {"valid"})
    service = _catalog_service()

    catalog_version_id, products, offers = service.publish_import(
        session, merchant_id=merchant_id, import_id=import_id
    )
    session.commit()

    return PublishResultResponse(
        import_id=import_id,
        status="published",
        catalog_version_id=catalog_version_id,
        products_created=products,
        offers_created=offers,
    )


@router.post("/imports/{import_id}/rollback")
def rollback_import(
    import_id: str,
    session: DatabaseSession,
    _principal: MerchantPrincipal,
) -> dict[str, str]:
    """Delete a pending/validated import and all its staged rows.

    Only imports that have not been published can be rolled back.
    """
    imp = _require_import_in_status(session, import_id, {"pending", "valid", "invalid"})
    service = _catalog_service()
    service.rollback_import(session, import_id=import_id)
    session.commit()
    return {"import_id": import_id, "status": "rolled_back"}
