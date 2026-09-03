"""Category pairing cross-sell and add-on recommendation engine (Task 48, Requirement 42)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from services.catalog.models import CategoryPairing, Product
from services.inventory.models import Inventory
from services.offers.models import Offer


@dataclass(frozen=True, slots=True)
class CrossSellRecommendation:
    pairing_id: str
    target_product_id: str
    target_title: str
    target_category: str
    target_unit_price_minor: int
    currency: str
    offer_id: str
    available_quantity: int
    rationale: str


class CrossSellEngine:
    """Computes non-intrusive cross-sell recommendations based on merchant category pairings."""

    @staticmethod
    def get_recommendations_for_product(
        session: Session,
        *,
        merchant_id: str,
        source_category: str,
        limit: int = 3,
    ) -> list[CrossSellRecommendation]:
        """Fetch merchant-curated add-on recommendations with inventory verification."""
        pairings = (
            session.query(CategoryPairing)
            .filter(
                CategoryPairing.merchant_id == merchant_id,
                CategoryPairing.source_category_id == source_category,
                CategoryPairing.enabled.is_(True),
            )
            .limit(limit)
            .all()
        )

        results: list[CrossSellRecommendation] = []
        for p in pairings:
            # Query candidate product in target category — deterministic ordering
            prod = (
                session.query(Product)
                .filter(
                    Product.merchant_id == merchant_id,
                    Product.category_id == p.target_category_id,
                )
                .order_by(Product.product_id)
                .first()
            )
            if not prod:
                continue

            # Must have an active offer with a real price
            offer = (
                session.query(Offer)
                .filter(
                    Offer.product_id == prod.product_id,
                    Offer.merchant_id == merchant_id,
                    Offer.status == "active",
                )
                .order_by(Offer.offer_id)
                .first()
            )
            if not offer:
                continue

            # Verify inventory — only recommend products that are actually in stock
            inventory = (
                session.query(Inventory).filter(Inventory.offer_id == offer.offer_id).first()
            )
            net_available = 0
            if inventory:
                net_available = inventory.available_quantity - inventory.reserved_quantity
            if net_available <= 0:
                continue

            results.append(
                CrossSellRecommendation(
                    pairing_id=p.pairing_id,
                    target_product_id=prod.product_id,
                    target_title=prod.title,
                    target_category=prod.category_id,
                    target_unit_price_minor=offer.unit_price_minor,
                    currency=offer.currency,
                    offer_id=offer.offer_id,
                    available_quantity=net_available,
                    rationale=f"Popular complementary {prod.category_id} for {source_category}.",
                )
            )

        return results
