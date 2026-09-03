"""The SQL evaluator and the offline evaluator must answer identically.

``services.offers.constraints`` declares the filter semantics once and hands them
to two evaluators: ``sql_predicates`` for PostgreSQL and ``offer_matches`` for the
offline seed path. The unit suite proves the offline evaluator matches the
declaration. This file proves the SQL evaluator does too, which needs a real
database because the semantics being checked are database semantics — a JSONB key
that is absent, a ``timestamptz`` comparison, integer arithmetic on two columns.

The dataset on both sides is the same seed artifact pair, imported here through
``CatalogService.import_catalog_artifacts``. Any disagreement means a buyer would
see a different answer depending on whether the datastore happened to be up.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.config import get_settings
from services.catalog.models import Merchant
from services.catalog.service import CatalogService
from services.offers.constraints import MAX_SEARCH_LIMIT, OfferConstraints
from services.offers.seed import (
    SEED_OFFERS_PATH,
    SEED_PRODUCTS_PATH,
    search_seed_candidates,
)
from services.offers.service import OfferService

pytestmark = pytest.mark.integration

#: The demo tenant, not a test-only one. ``product_id`` is a global primary key
#: and the seed artifacts carry explicit identifiers, so the same dataset cannot be
#: imported twice under two merchants. Importing under the tenant that actually
#: serves the demo is the better trade anyway: it compares the offline fixture
#: against the catalog a reviewer will really query.
MERCHANT = get_settings().default_merchant_id

HERO = OfferConstraints(
    category="laptop",
    max_price_minor=7_000_000,
    min_memory_gb=16,
    limit=MAX_SEARCH_LIMIT,
)

CONSTRAINT_MATRIX = [
    OfferConstraints(limit=MAX_SEARCH_LIMIT),
    HERO,
    replace(HERO, limit=3),
    replace(HERO, min_storage_gb=512),
    replace(HERO, max_delivery_days=2),
    replace(HERO, quantity=2),
    replace(HERO, quantity=5),
    OfferConstraints(category="smartphone", limit=MAX_SEARCH_LIMIT),
    OfferConstraints(min_memory_gb=16, limit=MAX_SEARCH_LIMIT),
    OfferConstraints(min_storage_gb=1024, limit=MAX_SEARCH_LIMIT),
    OfferConstraints(max_delivery_days=1, limit=MAX_SEARCH_LIMIT),
    OfferConstraints(category="laptop", max_price_minor=1, limit=MAX_SEARCH_LIMIT),
]


@pytest.fixture(scope="module")
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not reachable; start the compose stack")
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def imported_catalog(session_factory) -> None:
    """Ensure the seed catalog is imported and published for the demo merchant.

    Idempotent on the source checksum, so running this against an already-seeded
    database returns the existing version rather than inserting a duplicate. That
    is also why there is no teardown: the fixture converges on a state rather than
    creating and destroying one, and deleting the demo catalog afterwards would
    leave a developer's database emptier than they left it.
    """
    import json

    from services.inventory.models import Inventory
    from services.offers.models import Offer

    with session_factory() as session:
        existing = session.execute(
            select(Merchant).where(Merchant.merchant_id == MERCHANT)
        ).scalar_one_or_none()
        if existing is None:
            session.add(Merchant(merchant_id=MERCHANT, name=MERCHANT, status="active"))
            session.flush()

        version = CatalogService().import_catalog_artifacts(
            session,
            merchant_id=MERCHANT,
            products_path=SEED_PRODUCTS_PATH,
            offers_path=SEED_OFFERS_PATH,
            source_name="seed_catalog",
        )
        if version.status != "published":
            CatalogService().publish_catalog(
                session, merchant_id=MERCHANT, catalog_version_id=version.catalog_version_id
            )

        # Restore seed inventory and offer statuses for test determinism
        session.query(Offer).filter(Offer.offer_id.like("off_web_%")).update(
            {"status": "inactive"}, synchronize_session=False
        )
        with SEED_OFFERS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                off_id = item["offer_id"]
                avail_qty = item.get("available_quantity", 10)
                inv = session.query(Inventory).filter(Inventory.offer_id == off_id).first()
                if inv:
                    inv.available_quantity = avail_qty
                    inv.reserved_quantity = 0
                off = session.query(Offer).filter(Offer.offer_id == off_id).first()
                if off:
                    off.status = item.get("status", "active")

        session.commit()


@pytest.mark.parametrize("constraints", CONSTRAINT_MATRIX)
def test_sql_and_offline_paths_return_the_same_offers(
    session_factory, imported_catalog, constraints
) -> None:
    """Same dataset, same constraints, same answer — including the order."""
    now = datetime.now(UTC)

    with session_factory() as session:
        sql_results = OfferService().search_offer_candidates(
            session, merchant_id=MERCHANT, constraints=constraints, now=now
        )

    offline_results = search_seed_candidates(merchant_id=MERCHANT, constraints=constraints, now=now)

    assert [c.offer.offer_id for c in sql_results] == [
        c.offer.offer_id for c in offline_results
    ], "the SQL and offline evaluators disagreed"

    for sql_candidate, offline_candidate in zip(sql_results, offline_results, strict=True):
        assert sql_candidate.offer.unit_price_minor == offline_candidate.offer.unit_price_minor
        assert sql_candidate.offer.available_quantity == offline_candidate.offer.available_quantity
        assert sql_candidate.offer.delivery_days == offline_candidate.offer.delivery_days
        assert sql_candidate.category_id == offline_candidate.category_id
        assert (
            sql_candidate.offer.specifications.memory_gb
            == offline_candidate.offer.specifications.memory_gb
        )


def test_the_hero_query_returns_offers_from_sql(session_factory, imported_catalog) -> None:
    """The endpoint's hero scenario, answered by PostgreSQL rather than a fixture."""
    with session_factory() as session:
        results = OfferService().search_offer_candidates(
            session, merchant_id=MERCHANT, constraints=HERO
        )

    assert results, "the hero query must return offers from the published catalog"
    for candidate in results:
        assert candidate.category_id == "laptop"
        assert candidate.offer.unit_price_minor <= 7_000_000
        assert candidate.offer.specifications.memory_gb is not None
        assert candidate.offer.specifications.memory_gb >= 16
        assert candidate.offer.available_quantity >= 1


def test_a_missing_jsonb_spec_does_not_satisfy_a_minimum(session_factory, imported_catalog) -> None:
    """The case the old Python search got wrong: an absent memory specification
    passed the filter. In SQL the NULL comparison must drop the row."""
    with session_factory() as session:
        results = OfferService().search_offer_candidates(
            session,
            merchant_id=MERCHANT,
            constraints=OfferConstraints(min_memory_gb=8, limit=MAX_SEARCH_LIMIT),
        )
        unconstrained = OfferService().search_offer_candidates(
            session,
            merchant_id=MERCHANT,
            constraints=OfferConstraints(limit=MAX_SEARCH_LIMIT),
        )

    # `off_seed_lap_09` has no `memory_gb` key at all.
    assert "off_seed_lap_09" in {c.offer.offer_id for c in unconstrained}
    assert "off_seed_lap_09" not in {c.offer.offer_id for c in results}


def test_out_of_stock_and_expired_offers_never_reach_sql_results(
    session_factory, imported_catalog
) -> None:
    with session_factory() as session:
        results = OfferService().search_offer_candidates(
            session,
            merchant_id=MERCHANT,
            constraints=OfferConstraints(category="laptop", limit=MAX_SEARCH_LIMIT),
        )

    returned = {c.offer.offer_id for c in results}
    assert "off_seed_lap_07" not in returned  # zero available
    assert "off_seed_lap_08" not in returned  # lapsed in 2020


def test_the_query_cannot_read_another_tenants_catalog(session_factory, imported_catalog) -> None:
    """The repository is tenant-scoped at construction, so a different merchant sees
    nothing rather than seeing the seed rows."""
    with session_factory() as session:
        results = OfferService().search_offer_candidates(
            session,
            merchant_id="merchant_not_the_importer",
            constraints=OfferConstraints(limit=MAX_SEARCH_LIMIT),
        )
    assert results == []
