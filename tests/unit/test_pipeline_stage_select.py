"""Stage 2 end to end: quotas as caps, honest shortfalls, verbatim provenance.

Every test builds its own tiny ``candidates.sqlite`` in ``tmp_path`` through the
real stage-1 writer, then runs the real stage against real files. Nothing depends
on a prior pipeline run, and nothing is mocked -- "the quota is never exceeded"
and "the provenance file is the source record" are properties of the actual I/O.
"""

from __future__ import annotations

import json
import random
import sqlite3

import pytest

from pipeline.build_catalog import (
    STAGE_PRODUCTS,
    STAGE_SELECT,
    SUBCATEGORY_QUOTAS,
    Candidate,
    CandidateWriter,
    QuotaOutcome,
    connect,
    derive_product_id,
    ensure_schema,
    evaluate_record,
    normalize_images,
    read_selection_quotas,
    read_stage_state,
    stage_select,
    write_stage_state,
)
from pipeline.config import PipelineConfig
from tests.unit.test_pipeline_normalization import IMAGE_HI, IMAGE_LARGE, product

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_dir(tmp_path):
    path = tmp_path / "datasets"
    path.mkdir()
    return path


@pytest.fixture
def config(tmp_path, raw_dir):
    return PipelineConfig(raw_dir=raw_dir, out_dir=tmp_path / "out")


def candidate(parent_asin: str, **overrides) -> Candidate:
    """A stored candidate with the ranking fields under the test's control.

    Built as a real :class:`Candidate` so it goes through the same
    ``to_row``/insert path stage 1 uses. ``raw`` is a genuine source record, which
    is what lets the provenance test compare bytes.
    """
    record = product(parent_asin=parent_asin, **overrides.pop("record", {}))
    fields = {
        "parent_asin": parent_asin,
        "source_file": "meta_Electronics.jsonl.gz",
        "main_category": record.get("main_category"),
        "subcategory": "laptop",
        "score": 50,
        "title": record["title"],
        "store": record.get("store"),
        "average_rating": record.get("average_rating", 0.0),
        "rating_number": record.get("rating_number", 0),
        "price_usd": record.get("price"),
        "features": record["features"],
        "description": record["description"],
        "images": normalize_images(record["images"]),
        "details": record["details"],
        "categories": record["categories"],
        "raw": record,
    }
    fields.update(overrides)
    return Candidate(**fields)


def seed(config: PipelineConfig, candidates, *, cap: int | None = None) -> None:
    """Write candidates into a fresh ``candidates.sqlite`` and close stage 1."""
    connection = connect(config.candidates_db)
    try:
        ensure_schema(connection)
        writer = CandidateWriter(connection, batch_size=500)
        for item in candidates:
            writer.add(item)
        writer.flush()
        write_stage_state(connection, STAGE_PRODUCTS, "complete", cap, records=writer.written)
    finally:
        connection.close()


def products_of(config: PipelineConfig) -> list[dict]:
    with config.products_jsonl.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def counts_by_category(entries) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["category_id"]] = counts.get(entry["category_id"], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_stage_writes_products_and_one_provenance_file_each(config):
    seed(
        config,
        [
            candidate("L1", score=90, rating_number=500),
            candidate("S1", subcategory="smartphone", score=80),
            candidate("A1", subcategory="appliance", score=70),
        ],
    )

    result = stage_select(config, quotas={"laptop": 5, "smartphone": 5, "appliance": 5})

    assert result.products_written == 3
    assert result.raw_metadata_written == 3
    entries = products_of(config)
    assert {entry["external_product_id"] for entry in entries} == {"L1", "S1", "A1"}
    assert counts_by_category(entries) == {"laptop": 1, "smartphone": 1, "appliance": 1}

    written = sorted(path.name for path in config.raw_metadata_dir.glob("*.json"))
    assert written == sorted(f"{derive_product_id(a)}.json" for a in ("L1", "S1", "A1"))


def test_selection_is_ordered_by_score_then_rating_volume(config):
    """Requirement 2.1: score descending, then rating volume descending."""
    seed(
        config,
        [
            candidate("LOW", score=10, rating_number=9_999),
            candidate("MID_FEW", score=50, rating_number=1),
            candidate("MID_MANY", score=50, rating_number=900),
            candidate("TOP", score=99, rating_number=2),
        ],
    )

    stage_select(config, quotas={"laptop": 4})

    assert [entry["external_product_id"] for entry in products_of(config)] == [
        "TOP",
        "MID_MANY",
        "MID_FEW",
        "LOW",
    ]


def test_only_the_best_candidates_survive_a_tight_quota(config):
    seed(config, [candidate(f"L{i:02d}", score=i) for i in range(20)])

    stage_select(config, quotas={"laptop": 3})

    assert [entry["completeness_score"] for entry in products_of(config)] == [19, 18, 17]


# ---------------------------------------------------------------------------
# Quotas are caps (Requirement 2.2, 2.3, Property 25)
# ---------------------------------------------------------------------------


def test_quota_is_never_exceeded(config):
    """Property 25: more candidates than quota changes the quota not at all."""
    seed(
        config,
        [candidate(f"L{i:03d}", score=i % 100) for i in range(120)]
        + [candidate(f"S{i:03d}", subcategory="smartphone", score=i) for i in range(40)],
    )
    quotas = {"laptop": 10, "smartphone": 7}

    result = stage_select(config, quotas=quotas)

    assert result.selected_by_subcategory == {"laptop": 10, "smartphone": 7}
    for outcome in result.outcomes:
        assert outcome.selected <= outcome.quota
    assert counts_by_category(products_of(config)) == {"laptop": 10, "smartphone": 7}


def test_an_underfilled_subcategory_is_reported_and_never_padded(config):
    """Requirement 2.2, 2.3: the shortfall is the honest answer, not a total to hit."""
    seed(
        config,
        [candidate(f"L{i}", score=90 - i) for i in range(2)]
        + [candidate(f"S{i:03d}", subcategory="smartphone", score=i) for i in range(50)],
    )
    quotas = {"laptop": 10, "smartphone": 6, "camera": 4}

    result = stage_select(config, quotas=quotas)

    # The laptop bucket stays short. Nothing is borrowed from smartphone to fill
    # it, and the smartphone bucket does not overrun to compensate.
    assert result.selected_by_subcategory == {"laptop": 2, "smartphone": 6, "camera": 0}
    assert result.shortfalls == {"laptop": 8, "camera": 4}
    assert result.total_quota == 20
    assert result.total_selected == 8
    assert result.total_shortfall == 12
    assert counts_by_category(products_of(config)) == {"laptop": 2, "smartphone": 6}


def test_shortfall_is_persisted_for_the_quality_report(config):
    """Requirement 2.3: stage 6 reads a measured number, it does not infer one."""
    seed(config, [candidate("L1", score=90), candidate("L2", score=80)])

    stage_select(config, quotas={"laptop": 5, "monitor": 3})

    connection = connect(config.candidates_db)
    try:
        stored = read_selection_quotas(connection)
    finally:
        connection.close()

    assert stored == [
        QuotaOutcome(subcategory="laptop", quota=5, available=2, selected=2),
        QuotaOutcome(subcategory="monitor", quota=3, available=0, selected=0),
    ]
    assert [outcome.shortfall for outcome in stored] == [3, 3]


def test_available_count_is_reported_separately_from_selected(config):
    """A shortfall caused by a thin source reads differently from a bug."""
    seed(config, [candidate(f"L{i:02d}", score=i) for i in range(30)])

    result = stage_select(config, quotas={"laptop": 4})

    assert result.outcomes[0].available == 30
    assert result.outcomes[0].selected == 4
    assert result.outcomes[0].shortfall == 0


def test_quotas_hold_across_many_random_candidate_distributions(config, tmp_path):
    """Property 25 over generated inputs: selected == min(quota, available), always.

    The distributions deliberately include empty buckets, buckets far under quota,
    and buckets far over it, since those are the three cases where padding would
    be tempting.
    """
    rng = random.Random(4242)
    subcategories = list(SUBCATEGORY_QUOTAS)

    for trial in range(25):
        out_dir = tmp_path / f"trial_{trial}"
        trial_config = PipelineConfig(raw_dir=config.raw_dir, out_dir=out_dir)
        available = {name: rng.choice([0, 1, 2, 5, 12, 30]) for name in subcategories}
        quotas = {name: rng.choice([0, 1, 3, 8, 20]) for name in subcategories}
        seed(
            trial_config,
            [
                candidate(
                    f"{name}-{index}",
                    subcategory=name,
                    score=rng.randrange(0, 101),
                    rating_number=rng.randrange(0, 5_000),
                )
                for name in subcategories
                for index in range(available[name])
            ],
        )

        result = stage_select(trial_config, quotas=quotas)

        expected = {name: min(quotas[name], available[name]) for name in subcategories}
        assert result.selected_by_subcategory == expected, (trial, quotas, available)
        assert counts_by_category(products_of(trial_config)) == {
            name: count for name, count in expected.items() if count
        }
        assert result.total_selected == sum(expected.values())
        assert result.total_selected <= result.total_quota


def test_a_negative_quota_is_refused(config):
    seed(config, [candidate("L1")])

    with pytest.raises(ValueError, match="quota must be non-negative"):
        stage_select(config, quotas={"laptop": -1})


# ---------------------------------------------------------------------------
# Deterministic identifiers (Requirement 2.4, 2.5, Property 24)
# ---------------------------------------------------------------------------


def test_two_runs_over_the_same_input_produce_identical_output(config):
    """Property 24, at stage level: identical identifiers and identical bytes."""
    seed(
        config,
        [candidate(f"L{i:02d}", score=i, rating_number=i * 3) for i in range(12)]
        + [candidate(f"S{i:02d}", subcategory="smartphone", score=i) for i in range(6)],
    )
    quotas = {"laptop": 8, "smartphone": 4}

    stage_select(config, quotas=quotas)
    first_bytes = config.products_jsonl.read_bytes()
    first_ids = [entry["product_id"] for entry in products_of(config)]

    stage_select(config, quotas=quotas, force=True)
    second_bytes = config.products_jsonl.read_bytes()
    second_ids = [entry["product_id"] for entry in products_of(config)]

    assert first_ids == second_ids
    assert first_bytes == second_bytes
    assert len(set(first_ids)) == len(first_ids)


def test_product_id_is_derived_from_the_parent_identifier_which_is_retained(config):
    """Requirement 2.4 and 2.5 together."""
    seed(config, [candidate("B0BXYZ1234")])

    stage_select(config, quotas={"laptop": 1})

    entry = products_of(config)[0]
    assert entry["product_id"] == derive_product_id("B0BXYZ1234")
    assert entry["external_product_id"] == "B0BXYZ1234"
    assert entry["provenance"]["parent_asin"] == "B0BXYZ1234"


# ---------------------------------------------------------------------------
# Provenance (Requirement 2.6)
# ---------------------------------------------------------------------------


def test_provenance_file_is_the_verbatim_source_record(config):
    """Requirement 2.6: the original record, not a re-serialization of our fields.

    The comparison is against the record as stage 1 retained it, byte for byte,
    and against the source object itself after parsing. A file rebuilt from the
    normalized product would fail both: it would carry ``normalized_features``
    and would have lost ``videos``, the string-encoded ``details``, and every
    other field the pipeline does not model.
    """
    source = product(
        parent_asin="RAW1",
        details='{"Brand": "ASUS"}',  # string-encoded in the source
        videos=[{"title": "demo"}],  # a field the product object does not model
        bought_together=None,
    )
    seed(config, [candidate("RAW1", record={}, raw=source)])

    stage_select(config, quotas={"laptop": 1})

    path = config.raw_metadata_dir / f"{derive_product_id('RAW1')}.json"
    expected = json.dumps(source, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert path.read_bytes() == expected
    assert json.loads(path.read_text(encoding="utf-8")) == source
    assert json.loads(path.read_text(encoding="utf-8"))["details"] == '{"Brand": "ASUS"}'
    assert "normalized_features" not in path.read_text(encoding="utf-8")


def test_every_product_points_at_its_own_provenance_file(config):
    seed(config, [candidate(f"L{i}", score=i) for i in range(5)])

    stage_select(config, quotas={"laptop": 5})

    for entry in products_of(config):
        relative = entry["provenance"]["raw_metadata_path"]
        assert relative == f"raw_metadata/{entry['product_id']}.json"
        assert (config.catalog_dir / relative).is_file()


def test_reselection_removes_provenance_files_for_dropped_products(config):
    """A stale file would claim a product is in a catalog that no longer has it."""
    seed(config, [candidate(f"L{i}", score=i) for i in range(6)])
    stage_select(config, quotas={"laptop": 6})
    assert len(list(config.raw_metadata_dir.glob("*.json"))) == 6

    stage_select(config, quotas={"laptop": 2}, force=True)

    remaining = {path.stem for path in config.raw_metadata_dir.glob("*.json")}
    assert remaining == {entry["product_id"] for entry in products_of(config)}
    assert len(remaining) == 2


# ---------------------------------------------------------------------------
# Normalized fields on the artifact (Requirement 2.7, 2.8)
# ---------------------------------------------------------------------------


def test_units_are_normalized_on_the_written_product(config):
    """Requirement 2.7, asserted on the artifact rather than on the helper."""
    seed(
        config,
        [
            candidate(
                "SPEC1",
                details={
                    "Brand": "Acme",
                    "RAM": "16 GB",
                    "Hard Drive": "1 TB",
                    "Item Weight": "4 lbs",
                    "Product Dimensions": "12.8 x 8.9 x 0.6 inches",
                    "Shipping": "3-5 business days",
                    "Is Discontinued By Manufacturer": "No",
                },
            )
        ],
    )

    stage_select(config, quotas={"laptop": 1})

    specs = products_of(config)[0]["specifications"]
    assert specs["memory_gb"] == 16.0
    assert specs["storage_gb"] == 1024.0
    assert specs["weight_grams"] == 1_814
    assert specs["dimensions_mm"] == {
        "length_mm": 325.1,
        "width_mm": 226.1,
        "height_mm": 15.2,
    }
    assert specs["delivery_days"] == 5
    assert specs["flags"]["is_discontinued_by_manufacturer"] is False


def test_source_price_is_recorded_dataset_metadata(config):
    """The USD source price is present in minor units with its provenance note."""
    seed(config, [candidate("P1", price_usd=1_299.99)])

    stage_select(config, quotas={"laptop": 1})

    price = products_of(config)[0]["source_price"]
    assert price["amount_minor"] == 129_999
    assert price["currency"] == "USD"
    assert price["is_authoritative"] is True
    assert "historical dataset metadata" in price["note"]


def test_images_are_resolved_to_their_best_resolution_on_the_product(config):
    seed(
        config,
        [
            candidate(
                "IMG1",
                record={"images": [{"hi_res": IMAGE_HI, "large": IMAGE_LARGE, "thumb": None}]},
            )
        ],
    )

    stage_select(config, quotas={"laptop": 1})

    images = products_of(config)[0]["images"]
    assert [(image["resolution"], image["url"]) for image in images] == [("hi_res", IMAGE_HI)]


# ---------------------------------------------------------------------------
# Stage boundary and resumability (Requirement 1.16)
# ---------------------------------------------------------------------------


def test_second_run_is_a_no_op_at_the_stage_boundary(config):
    seed(config, [candidate(f"L{i}", score=i) for i in range(4)])
    quotas = {"laptop": 4}
    stage_select(config, quotas=quotas)
    stamp = config.products_jsonl.stat().st_mtime_ns

    second = stage_select(config, quotas=quotas)

    assert second.already_complete is True
    assert second.products_written == 4
    assert second.outcomes == [QuotaOutcome("laptop", quota=4, available=4, selected=4)]
    assert config.products_jsonl.stat().st_mtime_ns == stamp


def test_force_reselects(config):
    seed(config, [candidate(f"L{i}", score=i) for i in range(4)])
    stage_select(config, quotas={"laptop": 4})

    forced = stage_select(config, quotas={"laptop": 2}, force=True)

    assert forced.already_complete is False
    assert forced.products_written == 2


def test_a_changed_debug_cap_reselects_because_the_pool_changed(config, raw_dir, tmp_path):
    seed(config, [candidate(f"L{i}", score=i) for i in range(4)], cap=100)
    stage_select(config, quotas={"laptop": 4}, max_lines=100)

    widened = PipelineConfig(raw_dir=raw_dir, out_dir=config.out_dir, max_lines_debug=500)
    rerun = stage_select(widened, quotas={"laptop": 4})

    assert rerun.already_complete is False


def test_a_deleted_artifact_reselects_even_when_state_says_complete(config):
    seed(config, [candidate("L1")])
    stage_select(config, quotas={"laptop": 1})
    config.products_jsonl.unlink()

    rerun = stage_select(config, quotas={"laptop": 1})

    assert rerun.already_complete is False
    assert config.products_jsonl.is_file()


def test_stage_state_records_the_boundary(config):
    seed(config, [candidate(f"L{i}", score=i) for i in range(3)], cap=7)

    stage_select(config, quotas={"laptop": 3}, max_lines=7)

    connection = connect(config.candidates_db)
    try:
        state = read_stage_state(connection, STAGE_SELECT)
    finally:
        connection.close()

    assert state["status"] == "complete"
    assert state["records"] == 3
    assert state["max_lines_debug"] == 7


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


def test_stage_two_reads_only_the_candidate_database(config, raw_dir):
    """Requirement 1.2 and the stage contract: the sources are not touched again."""
    (raw_dir / "meta_Electronics.jsonl.gz").write_bytes(b"not read by stage 2")
    seed(config, [candidate("L1")])
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in raw_dir.iterdir()
    }

    stage_select(config, quotas={"laptop": 1})

    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in raw_dir.iterdir()
    }
    assert after == before


def test_missing_candidate_database_says_which_stage_to_run(config):
    with pytest.raises(FileNotFoundError, match=r"run 'products' \(stage 1\) first"):
        stage_select(config)


def test_output_inside_the_raw_directory_is_refused(raw_dir):
    config = PipelineConfig(raw_dir=raw_dir, out_dir=raw_dir / "out")

    with pytest.raises(ValueError, match="immutable raw directory"):
        stage_select(config)


def test_candidates_from_a_real_stage_one_record_select_cleanly(config):
    """A sanity bridge: the row shape stage 1 writes is the row shape stage 2 reads."""
    record = product(parent_asin="BRIDGE1")
    stored, reason = evaluate_record(record, "meta_Electronics.jsonl.gz", set())
    assert stored is not None, reason
    seed(config, [stored])

    result = stage_select(config, quotas={stored.subcategory: 1})

    assert result.products_written == 1
    entry = products_of(config)[0]
    assert entry["title"] == record["title"]
    assert entry["category_id"] == stored.subcategory
    assert entry["completeness_score"] == stored.score


def test_the_candidate_table_is_left_unchanged_by_selection(config):
    seed(config, [candidate(f"L{i}", score=i) for i in range(5)])
    connection = connect(config.candidates_db)
    try:
        before = [tuple(row) for row in connection.execute("SELECT * FROM candidates")]
    finally:
        connection.close()

    stage_select(config, quotas={"laptop": 2})

    connection = connect(config.candidates_db)
    try:
        after = [tuple(row) for row in connection.execute("SELECT * FROM candidates")]
    finally:
        connection.close()
    assert after == before


def test_selection_quota_table_is_created_by_the_schema():
    connection = sqlite3.connect(":memory:")
    try:
        ensure_schema(connection)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(selection_quota)").fetchall()
        }
    finally:
        connection.close()

    assert columns == {
        "subcategory",
        "quota",
        "available",
        "selected",
        "shortfall",
        "updated_at",
    }
