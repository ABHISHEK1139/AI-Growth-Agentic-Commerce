"""Offer repository with deterministic SQL filtering and ranking.

The filters and the ordering are not written here. They come from
:mod:`services.offers.constraints`, which declares them once so the SQL evaluator
and the offline Python evaluator cannot drift apart. This module owns the join,
the tenant predicate, and execution — the three things that genuinely belong to
the database layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import select

from packages.db.repository import TenantScopedRepository
from services.catalog.models import Product, ProductImage
from services.inventory.models import Inventory
from services.offers.constraints import OfferConstraints, sql_ordering, sql_predicates
from services.offers.models import Offer


class OfferRepository(TenantScopedRepository[Offer]):
    model: ClassVar[Any] = Offer
    merchant_column: ClassVar[str] = "merchant_id"

    def get_by_id(self, offer_id: str) -> Offer | None:
        """Fetch offer by ID within tenant scope."""
        return self.get(offer_id)

    def search_offers(
        self,
        *,
        constraints: OfferConstraints,
        now: datetime | None = None,
    ) -> list[tuple[Offer, Product, Inventory, str | None]]:
        """Search and rank offers, filtering in SQL rather than in Python.

        Returns the primary product image alongside each row via a correlated
        subquery, so a buyer surface needs one round trip rather than one per
        result. ``ProductImage`` carries no merchant column of its own; it is
        reachable only through the already tenant-filtered ``Offer`` join, so the
        subquery cannot widen the scope.
        """
        current_time = now or datetime.now(UTC)

        primary_image = (
            select(ProductImage.source_url)
            .where(ProductImage.product_id == Offer.product_id)
            .order_by(ProductImage.position.asc(), ProductImage.product_image_id.asc())
            .limit(1)
            .scalar_subquery()
        )

        stmt = (
            select(Offer, Product, Inventory, primary_image.label("image_url"))
            .join(Product, Offer.product_id == Product.product_id)
            .join(Inventory, Offer.offer_id == Inventory.offer_id)
            .where(Offer.merchant_id == self._scope.merchant_id)
            .where(
                *sql_predicates(
                    constraints,
                    offer=Offer,
                    product=Product,
                    inventory=Inventory,
                    now=current_time,
                )
            )
            .order_by(*sql_ordering(offer=Offer, product=Product))
            .limit(constraints.capped_limit)
        )

        statement = stmt.execution_options(agentpay_tenant_scope_marker=self._marker)
        rows = self.execute(statement).all()
        return [(row[0], row[1], row[2], row[3]) for row in rows]
