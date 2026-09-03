"""Unit tests for catalog seeding worker CLI (apps.worker.seed_catalog)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from apps.worker.seed_catalog import build_parser, main
from services.offers.seed import SEED_OFFERS_PATH, SEED_PRODUCTS_PATH


def test_seed_catalog_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.products == SEED_PRODUCTS_PATH
    assert args.offers == SEED_OFFERS_PATH
    assert args.merchant_id is None
    assert args.no_publish is False


def test_seed_catalog_parser_custom_args(tmp_path: Path):
    prod_file = tmp_path / "prod.jsonl"
    off_file = tmp_path / "off.jsonl"
    prod_file.write_text("{}", encoding="utf-8")
    off_file.write_text("{}", encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        [
            "--products",
            str(prod_file),
            "--offers",
            str(off_file),
            "--merchant-id",
            "merch_custom",
            "--source-name",
            "custom_src",
            "--no-publish",
        ]
    )
    assert args.products == prod_file
    assert args.offers == off_file
    assert args.merchant_id == "merch_custom"
    assert args.source_name == "custom_src"
    assert args.no_publish is True


def test_seed_catalog_missing_products_file(tmp_path: Path):
    non_existent = tmp_path / "missing_products.jsonl"
    code = main(["--products", str(non_existent)])
    assert code == 2


def test_seed_catalog_success(tmp_path: Path):
    prod_file = tmp_path / "products.jsonl"
    off_file = tmp_path / "offers.jsonl"
    prod_file.write_text("{}", encoding="utf-8")
    off_file.write_text("{}", encoding="utf-8")

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar_one_or_none.return_value = None

    mock_version = MagicMock()
    mock_version.catalog_version_id = "cat_test_123"
    mock_version.product_count = 10
    mock_version.valid_count = 10
    mock_version.needs_review_count = 0

    with (
        patch("apps.worker.seed_catalog.get_session_factory") as mock_factory,
        patch("apps.worker.seed_catalog.CatalogService") as mock_catalog_service_cls,
    ):
        mock_factory.return_value.return_value.__enter__.return_value = mock_session
        mock_service = mock_catalog_service_cls.return_value
        mock_service.import_catalog_artifacts.return_value = mock_version
        mock_service.publish_catalog.return_value = True

        code = main(
            [
                "--products",
                str(prod_file),
                "--offers",
                str(off_file),
                "--merchant-id",
                "merchant_demo",
            ]
        )

        assert code == 0
        mock_service.import_catalog_artifacts.assert_called_once()
        mock_service.publish_catalog.assert_called_once_with(
            mock_session,
            merchant_id="merchant_demo",
            catalog_version_id="cat_test_123",
        )
        mock_session.commit.assert_called_once()
