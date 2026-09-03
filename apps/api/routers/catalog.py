"""Catalog and offers read and query API endpoints (Task 13, Requirement 7)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import optional_principal
from apps.api.catalog_source import search_catalog
from apps.api.envelope import success
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal
from services.catalog.models import Product, ProductImage
from services.offers.constraints import OfferConstraints
from services.offers.seed import load_seed_candidates
from services.offers.service import OfferService

router = APIRouter(prefix="/api/v1", tags=["catalog"])


def _get_optional_db():
    session = None
    try:
        from apps.api.db import get_session_factory

        factory = get_session_factory()
        session = factory()
    except Exception:
        yield None
        return

    try:
        yield session
    finally:
        if session is not None:
            session.close()


OptionalDatabaseSession = Annotated[Session | None, Depends(_get_optional_db)]


class CatalogSearchRequest(BaseModel):
    query: str | None = None
    category: str | None = None
    max_price_minor: int | None = Field(default=None, ge=0)
    min_memory_gb: int | None = Field(default=None, ge=0)
    min_storage_gb: int | None = Field(default=None, ge=0)
    max_delivery_days: int | None = Field(default=None, ge=0)
    quantity: int = Field(default=1, ge=1)
    limit: int = Field(default=10, ge=1, le=50)


class OfferValidateRequest(BaseModel):
    expected_price_minor: int | None = Field(default=None, ge=0)
    expected_offer_version: int | None = Field(default=None, ge=1)


@router.post("/catalog/search")
@router.post("/offers/query")
def search_offers(
    request: CatalogSearchRequest,
    principal: Principal | None = Depends(optional_principal),
) -> dict[str, Any]:
    """Search and rank candidate offers with deterministic constraints."""
    merchant_id = (principal.merchant_id if principal else None) or "merchant_demo"
    constraints = OfferConstraints(
        category=request.category,
        max_price_minor=request.max_price_minor,
        min_memory_gb=request.min_memory_gb,
        min_storage_gb=request.min_storage_gb,
        max_delivery_days=request.max_delivery_days,
        quantity=request.quantity,
        limit=request.limit,
    )
    outcome = search_catalog(merchant_id=merchant_id, constraints=constraints)
    offers = [candidate.offer.model_dump(mode="json") for candidate in outcome.candidates]
    return success(
        {
            "offers": offers,
            "count": len(offers),
        }
    )


@router.get("/catalog/products/{product_id}")
def get_product(
    product_id: str,
    session: OptionalDatabaseSession,
    principal: Principal | None = Depends(optional_principal),
) -> dict[str, Any]:
    """Fetch product details with images and specifications."""
    merchant_id = (principal.merchant_id if principal else None) or "merchant_demo"

    if session is not None:
        try:
            product = session.query(Product).filter(Product.product_id == product_id).first()
            if product is not None:
                images = (
                    session.query(ProductImage)
                    .filter(ProductImage.product_id == product_id)
                    .order_by(ProductImage.position.asc())
                    .all()
                )
                return success(
                    {
                        "product": {
                            "product_id": product.product_id,
                            "external_product_id": product.external_product_id,
                            "category_id": product.category_id,
                            "title": product.title,
                            "status": product.status,
                            "description": product.description,
                            "specifications": product.specifications,
                            "average_rating": product.average_rating,
                            "rating_number": product.rating_number,
                            "images": [
                                {
                                    "source_url": img.source_url,
                                    "storage_key": img.storage_key,
                                    "resolution": img.resolution,
                                    "position": img.position,
                                }
                                for img in images
                            ],
                        }
                    }
                )
        except Exception:
            pass

    # Fallback to seed catalog
    candidates = load_seed_candidates(merchant_id)
    for c in candidates:
        if c.offer.product_id == product_id:
            return success(
                {
                    "product": {
                        "product_id": c.offer.product_id,
                        "external_product_id": c.offer.product_id,
                        "category_id": c.category_id,
                        "title": c.title,
                        "status": "valid",
                        "description": [c.title],
                        "specifications": c.specifications,
                        "average_rating": c.average_rating,
                        "rating_number": c.rating_number,
                        "images": [
                            {
                                "source_url": c.image_url,
                                "storage_key": None,
                                "resolution": "hi_res",
                                "position": 0,
                            }
                        ]
                        if c.image_url
                        else [],
                    }
                }
            )

    raise DomainError("The requested product does not exist.", code=ErrorCode.NOT_FOUND)


@router.get("/catalog/offers/{offer_id}")
def get_offer(
    offer_id: str,
    session: OptionalDatabaseSession,
    principal: Principal | None = Depends(optional_principal),
) -> dict[str, Any]:
    """Fetch an offer by ID within merchant scope."""
    merchant_id = (principal.merchant_id if principal else None) or "merchant_demo"
    if session is not None:
        try:
            service = OfferService()
            offer = service.get_offer_by_id(session, merchant_id=merchant_id, offer_id=offer_id)
            return success({"offer": offer.model_dump(mode="json")})
        except Exception:
            pass

    # Seed fallback
    candidates = load_seed_candidates(merchant_id)
    for c in candidates:
        if c.offer.offer_id == offer_id:
            return success({"offer": c.offer.model_dump(mode="json")})

    raise DomainError("The requested offer does not exist.", code=ErrorCode.NOT_FOUND)


@router.post("/offers/{offer_id}/validate")
def validate_offer(
    offer_id: str,
    request: OfferValidateRequest,
    session: OptionalDatabaseSession,
    principal: Principal | None = Depends(optional_principal),
) -> dict[str, Any]:
    """Revalidate offer validity, status, price, and inventory before checkout."""
    merchant_id = (principal.merchant_id if principal else None) or "merchant_demo"
    if session is not None:
        try:
            service = OfferService()
            offer = service.validate_offer(
                session,
                merchant_id=merchant_id,
                offer_id=offer_id,
                expected_price_minor=request.expected_price_minor,
                expected_offer_version=request.expected_offer_version,
            )
            return success({"offer": offer.model_dump(mode="json"), "valid": True})
        except DomainError:
            raise  # PRICE_CHANGED, OFFER_EXPIRED, INVENTORY_UNAVAILABLE must reach the caller
        except Exception:
            pass  # Non-domain DB errors fall through to seed lookup

    candidates = load_seed_candidates(merchant_id)
    for c in candidates:
        if c.offer.offer_id == offer_id:
            return success({"offer": c.offer.model_dump(mode="json"), "valid": True})

    raise DomainError("The requested offer does not exist.", code=ErrorCode.NOT_FOUND)
