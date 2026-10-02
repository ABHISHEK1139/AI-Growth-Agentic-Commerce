"""Unit tests for catalog import, validation, and atomic publish (Task 12, Requirement 6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.catalog.models import CatalogVersion, ImportRun, Product
from services.catalog.repository import (
    atomic_publish_catalog_version,
)
from services.catalog.service import CatalogService, compute_file_checksum
from services.inventory.models import Inventory
from services.offers.models import Offer


def test_compute_file_checksum(tmp_path: Path):
    file1 = tmp_path / "f1.txt"
    file1.write_text("hello")
    file2 = tmp_path / "f2.txt"
    file2.write_text("world")

    cs1 = compute_file_checksum(file1, file2)
    cs2 = compute_file_checksum(file1, file2)
    assert cs1 == cs2
    assert len(cs1) == 64


def test_import_creates_draft_catalog_and_validates(tmp_path: Path):
    products_file = tmp_path / "products.jsonl"
    products = [
        {
            "product_id": "prod_1",
            "title": "A Great Valid Laptop 15-inch",
            "subcategory": "laptop",
            "description": ["High performance laptop"],
            "specifications": {"memory_gb": 16, "storage_gb": 512},
            "average_rating": 4.5,
            "rating_number": 120,
            "images": [{"source_url": "https://img.example.com/1.jpg", "resolution": "large"}],
        },
        {
            "product_id": "prod_2",
            "title": "Tiny",  # < 8 chars -> needs_review
            "subcategory": "laptop",
            "description": [],
            "specifications": {},
        },
        {
            "product_id": "prod_3",
            "title": "Invalid Category Item Listing Here",
            "subcategory": "invalid_unknown_category",  # unknown category -> needs_review
            "description": [],
            "specifications": {},
        },
    ]
    with products_file.open("w", encoding="utf-8") as f:
        for p in products:
            f.write(json.dumps(p) + "\n")

    session = MagicMock()
    # Mock repositories returning None for existing run
    mock_run_res = MagicMock()
    mock_run_res.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_run_res
    # Nothing loaded yet, so the product/offer/inventory upserts all miss. A bare
    # MagicMock answers every `get` with a truthy object, which the tenant
    # ownership check in the upsert correctly rejects.
    session.get.return_value = None

    service = CatalogService()
    version = service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
    )

    assert version.status == "draft"
    assert version.product_count == 3
    assert version.valid_count == 1
    assert version.needs_review_count == 2
    assert session.add.called


def test_import_is_idempotent(tmp_path: Path):
    products_file = tmp_path / "products.jsonl"
    products_file.write_text(
        '{"product_id": "prod_1", "title": "Valid Laptop 15", "subcategory": "laptop"}\n'
    )

    existing_run = ImportRun(
        import_run_id="imp_existing",
        merchant_id="merch_1",
        source_name="demo",
        source_checksum=compute_file_checksum(products_file),
        schema_version="1.0",
        licence_note="demo",
        status="completed",
        started_at=datetime.now(UTC),
    )
    existing_version = CatalogVersion(
        catalog_version_id="cat_existing",
        merchant_id="merch_1",
        import_run_id="imp_existing",
        status="draft",
        product_count=1,
        valid_count=1,
        needs_review_count=0,
        created_at=datetime.now(UTC),
    )

    session = MagicMock()
    # Return existing completed run and existing version
    mock_run_repo = MagicMock()
    mock_run_repo.scalars.return_value.all.return_value = [existing_run]

    mock_ver_repo = MagicMock()
    mock_ver_repo.scalars.return_value.all.return_value = [existing_version]

    # Hook session.execute to return appropriate mock
    def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        target_model = getattr(
            getattr(stmt, "column_descriptions", [{}])[0].get("type"), "__name__", ""
        )
        if target_model == "CatalogVersion":
            res.scalars.return_value.all.return_value = [existing_version]
        elif target_model == "ImportRun":
            res.scalars.return_value.all.return_value = [existing_run]
        else:
            res.scalars.return_value.all.return_value = []
        return res

    session.execute.side_effect = mock_execute

    service = CatalogService()
    version = service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
    )

    assert version.catalog_version_id == "cat_existing"


def _sqlite_session():
    """A real SQLite session with the commerce tables this import touches.

    Deliberately not a mock. ``product_id`` is the primary key while
    ``catalog_version_id`` is an ordinary column, and every import mints a new
    version id -- so an unconditional insert could only ever succeed once over a
    given catalog. Every mock-based version of this test passed while the real
    code raised UniqueViolation on ``product_pkey`` against a real database,
    which is what stopped a re-seed. A test that cannot fail on the bug is not
    worth having.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import services.catalog.models  # noqa: F401 - register the tables
    import services.inventory.models  # noqa: F401
    import services.offers.models  # noqa: F401
    import tests.sqlite_types  # noqa: F401 - registers the JSONB/ARRAY compilers
    from packages.db.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _write_products(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records),
        encoding="utf-8",
    )
    return path


PRODUCTS = [
    {
        "product_id": "prod_1",
        "title": "A Great Valid Laptop 15-inch",
        "subcategory": "laptop",
        "external_product_id": "EXT-1",
        "unit_price_minor": 100000,
    }
]

OFFERS = [
    {
        "offer_id": "off_1",
        "product_id": "prod_1",
        "status": "active",
        "unit_price_minor": 100000,
        "currency": "INR",
        "available_quantity": 25,
    }
]


def test_import_twice_updates_in_place_rather_than_colliding(tmp_path: Path):
    """The second import of the same catalog must succeed.

    This is the bug: the import inserted unconditionally, so re-seeding a stack
    that already had the catalog died on the product primary key.
    """
    products_file = _write_products(tmp_path / "products.jsonl", PRODUCTS)
    offers_file = _write_products(tmp_path / "offers.jsonl", OFFERS)

    service = CatalogService()
    session = _sqlite_session()

    first = service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    second = service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    # Identical content resolves to the existing version rather than a new one.
    assert first.catalog_version_id == second.catalog_version_id
    assert session.query(Product).count() == 1
    assert session.query(Offer).count() == 1
    assert session.query(Inventory).count() == 1


def test_reimport_preserves_live_inventory_and_holds(tmp_path: Path):
    """Inventory is live commerce state, not catalog metadata.

    The artifacts say 25 available; a re-import must not lower stock already on
    offer, and must not erase ``reserved_quantity`` -- those are the holds buyers
    are mid-checkout on.

    The second artifact differs from the first on purpose. An identical checksum
    short-circuits the whole import, which is correct and is covered by
    ``test_import_twice_updates_in_place_rather_than_colliding``; the branch
    under test here only runs when the content actually changed.
    """
    products_file = _write_products(tmp_path / "products.jsonl", PRODUCTS)
    offers_file = _write_products(tmp_path / "offers.jsonl", OFFERS)

    service = CatalogService()
    session = _sqlite_session()
    service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    inventory = session.query(Inventory).one()
    inventory.available_quantity = 3
    inventory.reserved_quantity = 2
    session.commit()

    _write_products(offers_file, [{**OFFERS[0], "unit_price_minor": 111000}])

    service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    refreshed = session.query(Inventory).one()
    assert refreshed.reserved_quantity == 2, "a re-import erased live holds"
    assert refreshed.available_quantity == 25, (
        "a re-import must not reduce stock below what the artifact offers, and "
        "must not restore a stale count over the live one"
    )
    assert session.query(Inventory).count() == 1, "a re-import duplicated the inventory row"
    assert session.query(Offer).one().unit_price_minor == 111000, "price was not refreshed"


def test_import_refreshes_content_for_an_existing_row(tmp_path: Path):
    """A changed artifact updates the row it collides with instead of failing."""
    products_file = _write_products(tmp_path / "products.jsonl", PRODUCTS)
    offers_file = _write_products(tmp_path / "offers.jsonl", OFFERS)

    service = CatalogService()
    session = _sqlite_session()
    service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    changed = [
        {**PRODUCTS[0], "title": "A Renamed Valid Laptop 15-inch", "unit_price_minor": 123456},
        {**OFFERS[0], "unit_price_minor": 123456},
    ]
    _write_products(products_file, [changed[0]])
    _write_products(offers_file, [changed[1]])

    service.import_catalog_artifacts(
        session,
        merchant_id="merch_1",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    assert session.query(Product).one().title == "A Renamed Valid Laptop 15-inch"
    assert session.query(Offer).one().unit_price_minor == 123456


def test_import_refuses_to_take_over_another_tenants_product(tmp_path: Path):
    """The upsert must not become a cross-tenant write."""
    products_file = _write_products(tmp_path / "products.jsonl", PRODUCTS)
    offers_file = _write_products(tmp_path / "offers.jsonl", OFFERS)

    service = CatalogService()
    session = _sqlite_session()
    service.import_catalog_artifacts(
        session,
        merchant_id="merch_victim",
        products_path=products_file,
        offers_path=offers_file,
    )
    session.commit()

    with pytest.raises(DomainError) as exc:
        service.import_catalog_artifacts(
            session,
            merchant_id="merch_attacker",
            products_path=products_file,
            offers_path=offers_file,
        )
    assert exc.value.code == ErrorCode.FORBIDDEN
    assert session.query(Product).one().merchant_id == "merch_victim"


class _ResultWithoutRowcount:
    """The SQLAlchemy 2.0 ``Result`` surface, and nothing more.

    ``Session.execute`` is typed as returning ``Result``, which carries rows and
    no row count. A bare ``MagicMock`` answers ``.rowcount`` happily and hides
    that, so the publish path is asserted against a stand-in that refuses the
    attribute exactly as the real ``Result`` protocol does.
    """

    __slots__ = ("_rows",)

    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = list(rows or [])

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


class _PublishSession:
    """Records statements and returns a row only when the publish guard matched.

    ``already_published`` models a version that is *currently* published, which is
    what an idempotent re-seed hands to this function. The supersede statement is
    executed as a real statement by this double, so the interaction between
    "supersede everything published" and "promote only a draft" is observable --
    that interaction is the whole bug.
    """

    def __init__(self, *, publish_matches: bool, already_published: bool = False) -> None:
        self.publish_matches = publish_matches
        self.already_published = already_published
        self.statements: list[str] = []
        self.params: list[dict] = []

    def execute(self, statement, params=None):  # noqa: ANN001, ANN202 - test double
        self.statements.append(str(statement))
        self.params.append(params or {})
        is_publish = "status = 'published'" in str(statement) and "RETURNING" in str(statement)
        is_already_check = "SELECT 1 FROM catalog_version" in str(statement)
        if is_publish and self.publish_matches:
            return _ResultWithoutRowcount([("cat_new",)])
        if is_already_check and self.already_published:
            return _ResultWithoutRowcount([(1,)])
        return _ResultWithoutRowcount()


def test_atomic_publish_supersedes_previous_version():
    session = _PublishSession(publish_matches=True)

    success = atomic_publish_catalog_version(
        session, merchant_id="merch_1", catalog_version_id="cat_new"
    )
    assert success is True
    assert len(session.statements) == 2

    # 1st call supersedes old published version
    assert "status = 'superseded'" in session.statements[0]
    assert "status = 'published'" in session.statements[0]

    # 2nd call publishes new version and reports the match through RETURNING
    assert "status = 'published'" in session.statements[1]
    assert "superseded" in session.statements[1]
    assert "RETURNING" in session.statements[1]


def test_atomic_publish_reports_failure_when_no_version_matched():
    """A version owned by another merchant, or absent, matches nothing."""
    session = _PublishSession(publish_matches=False)

    success = atomic_publish_catalog_version(
        session, merchant_id="merch_1", catalog_version_id="cat_missing"
    )
    assert success is False


def test_atomic_publish_does_not_supersede_its_own_target():
    """The supersede must exclude the target version.

    Superseding everything published and then promoting only a draft left a tenant
    with *no* published version whenever the target was already published -- which
    is exactly what a re-run of the idempotent catalog seeder does. It took the
    live catalog offline and no error was raised.
    """
    session = _PublishSession(publish_matches=True, already_published=True)

    success = atomic_publish_catalog_version(
        session, merchant_id="merch_1", catalog_version_id="cat_same"
    )

    supersede = session.statements[0]
    assert "catalog_version_id <> :catalog_version_id" in supersede
    # The target id is bound, not interpolated.
    assert session.params[0]["catalog_version_id"] == "cat_same"
    assert success is True


def test_atomic_publish_recovers_a_version_left_superseded():
    """A version displaced by an earlier buggy run must be re-publishable."""
    session = _PublishSession(publish_matches=True)

    atomic_publish_catalog_version(
        session, merchant_id="merch_1", catalog_version_id="cat_displaced"
    )

    assert "status IN ('draft', 'validating', 'superseded')" in session.statements[1]


def test_atomic_publish_supersedes_previous_version_via_mock_session():
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("cat_new",)

    success = atomic_publish_catalog_version(
        session, merchant_id="merch_1", catalog_version_id="cat_new"
    )
    assert success is True
    assert session.execute.call_count == 2

    # 1st call supersedes old published version
    first_call_stmt = session.execute.call_args_list[0][0][0].text
    assert "status = 'superseded'" in first_call_stmt
    assert "status = 'published'" in first_call_stmt

    # 2nd call publishes new version
    second_call_stmt = session.execute.call_args_list[1][0][0].text
    assert "status = 'published'" in second_call_stmt
    assert "superseded" in second_call_stmt


def test_publish_service_emits_audit_event():
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("cat_1",)

    service = CatalogService()
    res = service.publish_catalog(session, merchant_id="merch_1", catalog_version_id="cat_1")
    assert res is True
    # Audit append event should have executed
    assert session.execute.called
