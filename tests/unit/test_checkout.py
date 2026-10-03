"""Unit tests for checkout with price integrity (Task 15, Requirement 11, Properties 1, 2, 4)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import OfferV1, ProductSpecificationsV1
from services.checkout.hash import PriceSnapshot, compute_price_hash
from services.checkout.models import Checkout
from services.checkout.service import CheckoutService


def _sample_offer(
    offer_id: str = "off_1",
    unit_price_minor: int = 4999900,
    status: str = "active",
    expires_in_hours: int = 24,
) -> OfferV1:
    now = datetime.now(UTC)
    return OfferV1(
        schema_version="1.0",
        offer_id=offer_id,
        product_id="prod_1",
        merchant_id="merch_1",
        status=status,  # type: ignore[arg-type]
        unit_price_minor=unit_price_minor,
        currency="INR",
        available_quantity=10,
        delivery_days=2,
        return_period_days=14,
        expires_at=(now + timedelta(hours=expires_in_hours)).isoformat(),
        offer_version=1,
        pricing_source="synthetic_band_random",
        specifications=ProductSpecificationsV1(
            memory_gb=16,
            storage_gb=512,
            weight_grams=None,
            length_mm=None,
            width_mm=None,
            height_mm=None,
        ),
    )


# ---------------------------------------------------------------------------
# Property 4: Price hash stability and sensitivity
# ---------------------------------------------------------------------------


def test_price_hash_stability():
    """Property 4: Hash is deterministic for identical pricing tuples across calls."""
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    snap1 = PriceSnapshot(
        offer_id="off_1",
        offer_version=1,
        unit_price_minor=5000000,
        quantity=2,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        currency="INR",
        expires_at=now,
    )
    snap2 = PriceSnapshot(
        offer_id="off_1",
        offer_version=1,
        unit_price_minor=5000000,
        quantity=2,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        currency="INR",
        expires_at=now,
    )
    assert compute_price_hash(snap1) == compute_price_hash(snap2)


def test_price_hash_differs_on_any_field_change():
    """Property 4: Modifying any single factor results in a distinct hash."""
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    base = PriceSnapshot(
        offer_id="off_1",
        offer_version=1,
        unit_price_minor=5000000,
        quantity=1,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        currency="INR",
        expires_at=now,
    )
    base_hash = compute_price_hash(base)

    # Change offer version
    h_ver = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=2,
            unit_price_minor=5000000,
            quantity=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_ver != base_hash

    # Change unit price
    h_price = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=1,
            unit_price_minor=4999900,
            quantity=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_price != base_hash

    # Change quantity
    h_qty = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=1,
            unit_price_minor=5000000,
            quantity=2,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_qty != base_hash

    # Change discount
    h_disc = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=1,
            unit_price_minor=5000000,
            quantity=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=50000,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_disc != base_hash


# ---------------------------------------------------------------------------
# Property 1 & 2: Server-calculated totals and exact integer arithmetic
# ---------------------------------------------------------------------------


def test_create_checkout_computes_exact_server_totals():
    """Property 1: subtotal + shipping + tax - discount == total exactly in minor units."""
    offer = _sample_offer(unit_price_minor=4999900)
    mock_offer_service = MagicMock()
    mock_offer_service.get_offer_by_id.return_value = offer

    mock_inventory_service = MagicMock()

    service = CheckoutService(
        offer_service=mock_offer_service,
        inventory_service=mock_inventory_service,
    )

    session = MagicMock()
    checkout = service.create_checkout(
        session,
        buyer_id="buy_1",
        merchant_id="merch_1",
        offer_id="off_1",
        quantity=2,
    )

    assert checkout.pricing.unit_price_minor == 4999900
    assert checkout.pricing.quantity == 2
    assert checkout.pricing.subtotal_minor == 4999900 * 2
    assert checkout.pricing.total_minor == 4999900 * 2
    assert checkout.price_hash is not None
    assert mock_inventory_service.reserve_stock.called


def test_create_checkout_fails_on_expired_offer():
    offer = _sample_offer(expires_in_hours=-1)
    mock_offer_service = MagicMock()
    mock_offer_service.get_offer_by_id.return_value = offer

    service = CheckoutService(offer_service=mock_offer_service)
    session = MagicMock()

    with pytest.raises(DomainError) as exc_info:
        service.create_checkout(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            offer_id="off_1",
            quantity=1,
        )
    assert exc_info.value.code == ErrorCode.OFFER_EXPIRED


def test_cancel_checkout_releases_inventory():
    now = datetime.now(UTC)
    mock_checkout = Checkout(
        checkout_id="chk_1",
        buyer_id="buy_1",
        merchant_id="merch_1",
        offer_id="off_1",
        offer_version=1,
        status="created",
        subtotal_minor=5000000,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        total_minor=5000000,
        currency="INR",
        price_hash="hash_1",
        price_snapshot={},
        expires_at=now + timedelta(minutes=15),
        created_at=now,
    )

    mock_inventory_service = MagicMock()
    service = CheckoutService(inventory_service=mock_inventory_service)

    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = mock_checkout
    # cancel_checkout re-reads under a row lock after the scoped read.
    session.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_checkout

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.checkout.service.CheckoutRepository", lambda s, scope: mock_repo)
        res = service.cancel_checkout(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
        )

    assert mock_inventory_service.release_stock.called
    assert res.status == "cancelled"


# ---------------------------------------------------------------------------
# The buyer row must exist before a checkout can reference it.
# ---------------------------------------------------------------------------


def _fk_enforced_session():
    """In-memory session with ``PRAGMA foreign_keys`` genuinely on.

    The pragma is attached to the engine at construction, because it has to be set
    on the connection before it is handed out. Without it SQLite ignores foreign
    keys and every assertion below would pass with the fix removed -- which is
    exactly the failure mode these tests exist to rule out.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import services.catalog.models  # noqa: F401 - registers buyer/merchant
    import services.checkout.models  # noqa: F401
    import tests.sqlite_types  # noqa: F401 - JSONB/ARRAY compilers
    from packages.db.base import Base

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    # ``audit_event`` is created by a migration rather than by the ORM metadata, and
    # the checkout service writes one, so it has to exist for the insert to land.
    # The column list is derived from the repository's own INSERT so it cannot drift.
    import re as _re

    from services.audit.repository import append_event as _append_event

    _src = inspect.getsource(_append_event)
    _cols = [
        c.strip()
        for c in _re.search(r"INSERT INTO audit_event\s*\((.*?)\)\s*VALUES", _src, _re.S)
        .group(1)
        .split(",")
        if c.strip()
    ]
    _ddl = ", ".join(f"{c} TEXT" for c in _cols)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS audit_event ({_ddl})"))

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_the_buyer_foreign_key_is_actually_enforced_in_this_harness():
    """Guard for the tests below. If this stops raising, they prove nothing."""
    from sqlalchemy.exc import IntegrityError

    from services.checkout.models import Checkout

    session = _fk_enforced_session()
    session.add(
        Checkout(
            checkout_id="chk_fk_probe",
            buyer_id="nobody",
            merchant_id="nobody",
            offer_id="off_x",
            offer_version=1,
            status="created",
            subtotal_minor=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            total_minor=1,
            currency="INR",
            price_hash="x",
            price_snapshot={},
            expires_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def _seed_minimal_rows(session, tables: dict[str, dict[str, object]]) -> None:
    """Insert the rows a checkout transitively references.

    Building these by hand means chasing foreign keys by hand -- ``offer`` points at
    ``product`` and ``catalog_version``, which point at others. Walking the ORM
    metadata instead means a schema change breaks one helper rather than five tests.
    """
    from packages.db.base import Base

    for table_name, values in tables.items():
        table = Base.metadata.tables[table_name]
        columns = {c.name: c for c in table.columns}
        row: dict[str, object] = {}
        for name, column in columns.items():
            if name in values:
                row[name] = values[name]
            elif not column.nullable and column.default is None and column.server_default is None:
                row[name] = f"seed-{table_name}-{name}"
        session.execute(table.insert().values(**row))
    session.commit()


def test_create_checkout_provisions_the_buyer_row():
    """The end-to-end shape: ``create_checkout`` for a buyer that has no row.

    Goes through the service rather than the helper, because "the helper works" is
    not the same claim as "the helper is called here". Reverting the call site inside
    ``create_checkout`` has to fail this test.
    """
    from services.catalog.models import Buyer

    session = _fk_enforced_session()
    # Dependency order matters: catalog_version -> product -> offer.
    _seed_minimal_rows(
        session,
        {
            "merchant": {"merchant_id": "merch_1", "name": "M1"},
            "catalog_version": {"catalog_version_id": "cv_1", "merchant_id": "merch_1"},
            "product": {
                "product_id": "prd_1",
                "catalog_version_id": "cv_1",
                "merchant_id": "merch_1",
                "external_product_id": "EXT-1",
                "category_id": "cat_1",
                "title": "A product",
            },
            "offer": {
                "offer_id": "off_1",
                "catalog_version_id": "cv_1",
                "product_id": "prd_1",
                "merchant_id": "merch_1",
                "unit_price_minor": 1000,
                "currency": "INR",
                "offer_version": 1,
                "expires_at": datetime.now(UTC) + timedelta(days=30),
                "created_at": datetime.now(UTC),
            },
        },
    )
    assert session.query(Buyer).filter(Buyer.buyer_id == "walk_up").first() is None

    mock_offer_service = MagicMock()
    mock_offer_service.get_offer_by_id.return_value = _sample_offer(
        unit_price_minor=1000, expires_in_hours=24 * 30
    )
    service = CheckoutService(offer_service=mock_offer_service, inventory_service=MagicMock())
    checkout = service.create_checkout(
        session, buyer_id="walk_up", merchant_id="merch_1", offer_id="off_1", quantity=1
    )
    session.commit()

    assert checkout.checkout_id
    row = session.query(Buyer).filter(Buyer.buyer_id == "walk_up").one()
    assert row.tenant_id == "merch_1"


def test_ensure_buyer_creates_the_row_when_absent():
    """The fix itself: an unknown buyer id gets a row before it is referenced."""
    from services.catalog.models import Buyer

    session = _fk_enforced_session()
    assert session.query(Buyer).filter(Buyer.buyer_id == "brand_new").first() is None

    CheckoutService._ensure_buyer(session, buyer_id="brand_new", merchant_id="merch_1")

    row = session.query(Buyer).filter(Buyer.buyer_id == "brand_new").one()
    assert row.tenant_id == "merch_1"
    assert row.status == "active"


def test_ensure_buyer_is_idempotent():
    """Calling twice must not raise on the primary key."""
    from services.catalog.models import Buyer

    session = _fk_enforced_session()
    CheckoutService._ensure_buyer(session, buyer_id="twice", merchant_id="merch_1")
    CheckoutService._ensure_buyer(session, buyer_id="twice", merchant_id="merch_1")
    assert session.query(Buyer).filter(Buyer.buyer_id == "twice").count() == 1


def test_ensure_buyer_does_not_clobber_an_existing_buyer():
    """An existing buyer keeps its tenant and display name.

    A buyer id that shops at two merchants must not have its tenant rewritten by
    whichever merchant happens to see it first.
    """
    from services.catalog.models import Buyer

    session = _fk_enforced_session()
    session.add(
        Buyer(
            buyer_id="returning",
            tenant_id="merch_original",
            display_name="Real Name",
            status="active",
        )
    )
    session.commit()

    CheckoutService._ensure_buyer(session, buyer_id="returning", merchant_id="merch_other")

    row = session.query(Buyer).filter(Buyer.buyer_id == "returning").one()
    assert row.tenant_id == "merch_original"
    assert row.display_name == "Real Name"


def test_ensure_buyer_leaves_the_session_usable():
    """Provisioning must not leave the session in a broken state.

    ``_ensure_buyer`` flushes, so a caller that goes on to write more rows in the
    same transaction has to find a working session rather than a pending one.
    """
    from services.catalog.models import Buyer, Merchant

    session = _fk_enforced_session()
    CheckoutService._ensure_buyer(session, buyer_id="usable", merchant_id="merch_1")
    session.add(Merchant(merchant_id="merch_probe", name="Probe", status="active"))
    session.commit()
    assert session.query(Buyer).filter(Buyer.buyer_id == "usable").count() == 1
