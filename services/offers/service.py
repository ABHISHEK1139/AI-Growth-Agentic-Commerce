"""Offer search, query, and revalidation domain service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import OfferV1, ProductSpecificationsV1
from packages.security.tenancy import TenantScope
from services.catalog.models import Product
from services.inventory.models import Inventory
from services.offers.constraints import OfferCandidate, OfferConstraints
from services.offers.models import Offer
from services.offers.repository import OfferRepository


def _offer_to_schema(offer: Offer, product: Product, inventory: Inventory) -> OfferV1:
    """Convert database entities to public OfferV1 schema."""
    specs = product.specifications or {}
    spec_schema = ProductSpecificationsV1(
        memory_gb=specs.get("memory_gb"),
        storage_gb=specs.get("storage_gb"),
        weight_grams=specs.get("weight_grams"),
        length_mm=specs.get("length_mm"),
        width_mm=specs.get("width_mm"),
        height_mm=specs.get("height_mm"),
    )

    expires_str = (
        offer.expires_at.isoformat()
        if isinstance(offer.expires_at, datetime)
        else str(offer.expires_at)
    )

    # Calculate usable available quantity
    effective_available = max(0, inventory.available_quantity - inventory.reserved_quantity)

    return OfferV1(
        schema_version="1.0",
        offer_id=offer.offer_id,
        product_id=offer.product_id,
        merchant_id=offer.merchant_id,
        status=offer.status,  # type: ignore[arg-type]
        unit_price_minor=offer.unit_price_minor,
        currency=offer.currency,  # type: ignore[arg-type]
        available_quantity=effective_available,
        delivery_days=offer.delivery_days,
        return_period_days=offer.return_period_days,
        expires_at=expires_str,
        offer_version=offer.offer_version,
        pricing_source=offer.pricing_source,  # type: ignore[arg-type]
        specifications=spec_schema,
    )


def _candidate_of(
    offer: Offer, product: Product, inventory: Inventory, image_url: str | None
) -> OfferCandidate:
    """Project a database row onto the shape both search paths return."""
    return OfferCandidate(
        offer=_offer_to_schema(offer, product, inventory),
        category_id=product.category_id,
        title=product.title,
        average_rating=float(product.average_rating),
        rating_number=int(product.rating_number),
        image_url=image_url,
        specifications=dict(product.specifications or {}),
    )


class OfferService:
    """Domain service for querying and validating offers."""

    def search_offer_candidates(
        self,
        session: Session,
        *,
        merchant_id: str,
        constraints: OfferConstraints,
        now: datetime | None = None,
    ) -> list[OfferCandidate]:
        """Constrained, tenant-scoped, database-backed offer search.

        The single entry point for a constrained search over PostgreSQL. Filtering
        happens in SQL: nothing is fetched and then discarded in Python, so a
        ``limit`` means what it says and a constraint cannot be applied to a page
        of results that was already truncated.
        """
        scope = TenantScope(merchant_id=merchant_id)
        repo = OfferRepository(session, scope)
        rows = repo.search_offers(constraints=constraints, now=now)
        return [_candidate_of(offer, product, inv, image) for offer, product, inv, image in rows]

    def search_offers(
        self,
        session: Session,
        *,
        merchant_id: str,
        category: str | None = None,
        max_price_minor: int | None = None,
        min_memory_gb: int | None = None,
        min_storage_gb: int | None = None,
        max_delivery_days: int | None = None,
        quantity: int = 1,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[OfferV1]:
        """Search and rank candidate offers with deterministic constraints."""
        from services.audit.repository import append_event

        append_event(
            session,
            event_type="CATALOG_SEARCHED",
            aggregate_type="catalog",
            aggregate_id=merchant_id,
            actor_type="buyer",
            actor_id=None,
            merchant_id=merchant_id,
            metadata={
                "category": category,
                "max_price_minor": max_price_minor,
                "limit": limit,
            },
        )

        constraints = OfferConstraints(
            category=category,
            max_price_minor=max_price_minor,
            min_memory_gb=min_memory_gb,
            min_storage_gb=min_storage_gb,
            max_delivery_days=max_delivery_days,
            quantity=quantity,
            limit=limit,
        )
        candidates = self.search_offer_candidates(
            session, merchant_id=merchant_id, constraints=constraints, now=now
        )
        results = [candidate.offer for candidate in candidates]

        append_event(
            session,
            event_type="OFFERS_RETURNED",
            aggregate_type="catalog",
            aggregate_id=merchant_id,
            actor_type="system",
            actor_id=None,
            merchant_id=merchant_id,
            metadata={
                "returned_count": len(results),
                "offer_ids": [o.offer_id for o in results[:5]],
            },
        )

        return results

    def get_offer_by_id(self, session: Session, *, merchant_id: str, offer_id: str) -> OfferV1:
        """Fetch a single offer by ID within tenant scope."""
        scope = TenantScope(merchant_id=merchant_id)
        repo = OfferRepository(session, scope)
        offer = repo.get_by_id(offer_id)
        if offer is None:
            raise DomainError("The requested offer does not exist.", code=ErrorCode.OFFER_NOT_FOUND)

        product = session.query(Product).filter(Product.product_id == offer.product_id).first()
        inventory = session.query(Inventory).filter(Inventory.offer_id == offer.offer_id).first()
        if product is None or inventory is None:
            raise DomainError("The requested offer does not exist.", code=ErrorCode.OFFER_NOT_FOUND)

        return _offer_to_schema(offer, product, inventory)

    def validate_offer(
        self,
        session: Session,
        *,
        merchant_id: str,
        offer_id: str,
        expected_price_minor: int | None = None,
        expected_offer_version: int | None = None,
        now: datetime | None = None,
    ) -> OfferV1:
        """Revalidate offer status, expiry, price, and inventory before checkout."""
        current_time = now or datetime.now(UTC)
        offer_schema = self.get_offer_by_id(session, merchant_id=merchant_id, offer_id=offer_id)

        # Check status
        if offer_schema.status != "active":
            raise DomainError(
                "The selected offer is no longer valid.", code=ErrorCode.OFFER_EXPIRED
            )

        # Check expiry
        expires_raw = offer_schema.expires_at.replace("Z", "+00:00")
        try:
            expires_dt = datetime.fromisoformat(expires_raw)
        except Exception:
            expires_dt = current_time
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=UTC)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)

        if current_time >= expires_dt:
            raise DomainError(
                "The selected offer is no longer valid.", code=ErrorCode.OFFER_EXPIRED
            )

        # Check price integrity
        if (
            expected_price_minor is not None
            and offer_schema.unit_price_minor != expected_price_minor
        ):
            from services.audit.repository import append_event

            append_event(
                session,
                event_type="PRICE_CHANGE_DETECTED",
                aggregate_type="offer",
                aggregate_id=offer_id,
                actor_type="system",
                actor_id=None,
                merchant_id=merchant_id,
                amount_minor=offer_schema.unit_price_minor,
                metadata={
                    "expected_price_minor": expected_price_minor,
                    "actual_price_minor": offer_schema.unit_price_minor,
                },
            )
            raise DomainError(
                "The price changed after approval, so no charge was made.",
                code=ErrorCode.PRICE_CHANGED,
            )

        # Check version integrity
        if (
            expected_offer_version is not None
            and offer_schema.offer_version != expected_offer_version
        ):
            raise DomainError(
                "The record changed while this request was in flight.",
                code=ErrorCode.VERSION_CONFLICT,
            )

        # Check available quantity
        if offer_schema.available_quantity <= 0:
            raise DomainError(
                "The requested quantity is not available.",
                code=ErrorCode.INVENTORY_UNAVAILABLE,
            )

        from services.audit.repository import append_event

        append_event(
            session,
            event_type="OFFER_REVALIDATED",
            aggregate_type="offer",
            aggregate_id=offer_id,
            actor_type="system",
            actor_id=None,
            merchant_id=merchant_id,
            amount_minor=offer_schema.unit_price_minor,
            metadata={
                "status": offer_schema.status,
                "version": offer_schema.offer_version,
            },
        )

        return offer_schema
