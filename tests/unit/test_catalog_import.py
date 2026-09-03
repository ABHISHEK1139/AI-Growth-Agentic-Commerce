"""Unit tests for catalog import, validation, and atomic publish (Task 12, Requirement 6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from services.catalog.models import CatalogVersion, ImportRun
from services.catalog.repository import (
    atomic_publish_catalog_version,
)
from services.catalog.service import CatalogService, compute_file_checksum


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
    """Records statements and returns a row only when the publish guard matched."""

    def __init__(self, *, publish_matches: bool) -> None:
        self.publish_matches = publish_matches
        self.statements: list[str] = []

    def execute(self, statement, params=None):  # noqa: ANN001, ANN202 - test double
        self.statements.append(str(statement))
        is_publish = "status = 'published'" in str(statement) and "RETURNING" in str(statement)
        if is_publish and self.publish_matches:
            return _ResultWithoutRowcount([("cat_new",)])
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
    assert "status IN ('draft', 'validating')" in session.statements[1]
    assert "RETURNING" in session.statements[1]


def test_atomic_publish_reports_failure_when_no_version_matched():
    """A version already published, or owned by another merchant, matches nothing."""
    session = _PublishSession(publish_matches=False)

    success = atomic_publish_catalog_version(
        session, merchant_id="merch_1", catalog_version_id="cat_missing"
    )
    assert success is False


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
    assert "status IN ('draft', 'validating')" in second_call_stmt


def test_publish_service_emits_audit_event():
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = ("cat_1",)

    service = CatalogService()
    res = service.publish_catalog(session, merchant_id="merch_1", catalog_version_id="cat_1")
    assert res is True
    # Audit append event should have executed
    assert session.execute.called
