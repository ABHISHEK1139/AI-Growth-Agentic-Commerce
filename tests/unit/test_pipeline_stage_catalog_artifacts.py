"""Stages 4-6: reproducible offers, bounded review linking, honest reporting."""

from __future__ import annotations

import gzip
import json
import sqlite3

import pytest

from pipeline.build_catalog import (
    DATASET_USD_TO_INR_RATE,
    REVIEW_SOURCES,
    STAGE_OFFERS,
    STAGE_REPORT,
    STAGE_REVIEWS,
    build_offer,
    connect,
    derive_product_id,
    read_stage_state,
    stage_offers,
    stage_report,
    stage_reviews,
    stage_select,
)
from pipeline.config import PipelineConfig
from tests.unit.test_pipeline_stage_select import candidate, seed


@pytest.fixture
def raw_dir(tmp_path):  # noqa: ANN001
    path = tmp_path / "datasets"
    path.mkdir()
    return path


@pytest.fixture
def config(tmp_path, raw_dir):  # noqa: ANN001
    return PipelineConfig(raw_dir=raw_dir, out_dir=tmp_path / "out")


def select(config: PipelineConfig, candidates) -> None:  # noqa: ANN001
    seed(config, candidates)
    stage_select(config, quotas={"laptop": len(candidates)})


def read_jsonl(path):  # noqa: ANN001
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_reviews(path, records) -> None:  # noqa: ANN001
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def test_offers_are_byte_identical_and_convert_source_usd_at_the_fixed_demo_rate(config):
    select(config, [candidate("P1"), candidate("P2")])

    first = stage_offers(config)
    first_bytes = config.offers_jsonl.read_bytes()
    second = stage_offers(config, force=True)
    offers = read_jsonl(config.offers_jsonl)

    assert first.offers_written == second.offers_written == 2
    assert config.offers_jsonl.read_bytes() == first_bytes
    assert [offer["offer_id"] for offer in offers] == [
        "off_" + build_offer(product)["offer_id"].removeprefix("off_")
        for product in read_jsonl(config.products_jsonl)
    ]
    products = {product["product_id"]: product for product in read_jsonl(config.products_jsonl)}
    for offer in offers:
        assert offer["unit_price_minor"] == (
            products[offer["product_id"]]["source_price"]["amount_minor"] * DATASET_USD_TO_INR_RATE
        )
        assert offer["currency"] == "INR"
        assert offer["pricing_source"] == "amazon_reviews_2023_usd_fx_100"
        assert offer["reserved_quantity"] == 0
        assert offer["offer_version"] == 1


def test_reviews_are_scoped_to_selected_products_and_indexed(config):
    select(config, [candidate("P1")])
    write_reviews(
        config.raw_dir / REVIEW_SOURCES[0],
        [
            {"parent_asin": "P1", "rating": 5, "title": "Good", "text": "Works"},
            {"parent_asin": "P2", "rating": 1, "title": "Ignored", "text": "Nope"},
            {"parent_asin": "P1", "rating": 5, "title": "Good", "text": "Works"},
        ],
    )

    result = stage_reviews(config)
    rerun = stage_reviews(config)
    connection = sqlite3.connect(config.reviews_db)
    try:
        rows = connection.execute("SELECT parent_asin, product_id FROM review").fetchall()
        indexes = connection.execute("PRAGMA index_list('review')").fetchall()
    finally:
        connection.close()

    assert result.reviews_seen == 3
    assert result.reviews_discarded == 1
    assert result.reviews_linked == 1
    assert rerun.already_complete is True
    assert rows == [("P1", derive_product_id("P1"))]
    assert any("parent_asin" in row[1] for row in indexes)


def test_quality_report_is_computed_from_artifacts(config):
    select(config, [candidate("P1"), candidate("P2", record={"description": []})])
    stage_offers(config)
    write_reviews(config.raw_dir / REVIEW_SOURCES[0], [{"parent_asin": "P1", "rating": 4}])
    stage_reviews(config)

    report = stage_report(config)
    persisted = json.loads(config.quality_report_json.read_text(encoding="utf-8"))
    state_connection = connect(config.candidates_db)
    try:
        assert read_stage_state(state_connection, STAGE_OFFERS)["status"] == "complete"
        assert read_stage_state(state_connection, STAGE_REVIEWS)["status"] == "complete"
        assert read_stage_state(state_connection, STAGE_REPORT)["status"] == "complete"
    finally:
        state_connection.close()

    assert persisted == report
    assert report["achieved_product_total"] == 2
    assert report["product_count_by_subcategory"] == {"laptop": 2}
    assert report["missing_description_count"] == 1
    assert report["generated_offer_count"] == 2
    assert report["linked_review_count"] == 1
