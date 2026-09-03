"""Stage 1 end to end: batching, dedupe across files, resumability, immutability.

These tests run the real stage against real gzipped fixtures and a real SQLite
file. Nothing is mocked, because the properties under test -- "no more than one
batch in memory", "the source directory is never written to", "a second run does
no work" -- are properties of the actual I/O.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from pipeline.build_catalog import (
    BATCH_SIZE,
    REJECT_DUPLICATE,
    REJECT_NO_IMAGE,
    REJECT_NO_PARENT,
    REJECT_TITLE,
    SUBCATEGORIES,
    CandidateWriter,
    connect,
    ensure_schema,
    evaluate_record,
    stage_products,
    subcategory_counts,
)
from pipeline.config import PipelineConfig
from tests.unit.test_pipeline_normalization import IMAGE_HI, IMAGE_LARGE, product, write_jsonl_gz


@pytest.fixture
def raw_dir(tmp_path):
    path = tmp_path / "datasets"
    path.mkdir()
    return path


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "out"


@pytest.fixture
def config(raw_dir, out_dir):
    return PipelineConfig(raw_dir=raw_dir, out_dir=out_dir)


def rows(config) -> list[sqlite3.Row]:
    connection = connect(config.candidates_db)
    try:
        return connection.execute("SELECT * FROM candidates ORDER BY parent_asin").fetchall()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_stage_populates_candidates_with_normalized_and_raw_fields(config, raw_dir):
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [
            product(parent_asin="E1", title="Dell Inspiron 15 Laptop Computer"),
            product(
                parent_asin="E2",
                title="ASUS 24 inch LED Monitor Full HD",
                details='{"Brand": "ASUS"}',
                images={"hi_res": [IMAGE_HI], "large": [IMAGE_LARGE], "variant": ["MAIN"]},
            ),
        ],
    )

    result = stage_products(config, sources=("meta_Electronics.jsonl.gz",))

    assert result.kept == 2
    assert config.candidates_db.is_file()

    stored = rows(config)
    assert [row["parent_asin"] for row in stored] == ["E1", "E2"]
    assert stored[0]["subcategory"] == "laptop"
    assert stored[1]["subcategory"] == "monitor"
    assert stored[0]["source_file"] == "meta_Electronics.jsonl.gz"
    assert stored[0]["status"] == "valid"
    assert 0 <= stored[0]["score"] <= 100

    # Requirement 1.14: the complete original record round-trips.
    raw = json.loads(stored[1]["raw_json"])
    assert raw["details"] == '{"Brand": "ASUS"}'
    assert json.loads(stored[1]["details_json"]) == {"Brand": "ASUS"}
    assert json.loads(stored[1]["images_json"])[0]["hi_res"] == IMAGE_HI


def test_index_on_subcategory_and_score_exists(config, raw_dir):
    write_jsonl_gz(raw_dir / "meta_Electronics.jsonl.gz", [product()])

    stage_products(config, sources=("meta_Electronics.jsonl.gz",))

    connection = connect(config.candidates_db)
    try:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'candidates'"
            )
        }
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT parent_asin FROM candidates "
            "WHERE subcategory = ? ORDER BY score DESC",
            ("laptop",),
        ).fetchall()
    finally:
        connection.close()

    assert "idx_candidates_subcategory_score" in indexes
    assert "idx_candidates_subcategory_score" in " ".join(str(row[-1]) for row in plan)


def test_per_subcategory_counts_cover_the_fixed_set(config, raw_dir):
    titles = {
        "L1": "Dell Inspiron 15 Laptop Computer",
        "S1": "Apple iPhone 12 Unlocked Smartphone",
        "M1": "ASUS 24 inch LED Monitor Full HD",
        "A1": "Sony Wireless Noise Cancelling Headphones",
        "C1": "Canon EOS Rebel T7 DSLR Camera Body",
        "CA1": "Logitech M510 Wireless Mouse",
        "PA1": "Tempered Glass Screen Protector 2 Pack",
        "HE1": "Samsung 55 inch 4K Smart TV",
        "AP1": "Whirlpool Front Load Washing Machine",
        "U1": "Mysterious Widget Assembly Kit",
    }
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=asin, title=title) for asin, title in titles.items()],
    )

    stage_products(config, sources=("meta_Electronics.jsonl.gz",))

    connection = connect(config.candidates_db)
    try:
        counts = subcategory_counts(connection)
    finally:
        connection.close()

    assert list(counts) == list(SUBCATEGORIES)
    assert all(count == 1 for count in counts.values()), counts
    assert sum(counts.values()) == len(titles)


# ---------------------------------------------------------------------------
# Hard rejects, at stage level
# ---------------------------------------------------------------------------


def test_every_hard_reject_drops_its_case_across_files(config, raw_dir):
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [
            product(parent_asin="KEEP1"),
            product(parent_asin=None),  # 1.4
            product(parent_asin="T1", title="tiny"),  # 1.5 short
            product(parent_asin="T2", title="x" * 301),  # 1.5 long
            product(parent_asin="I1", images=[]),  # 1.6
            product(parent_asin="KEEP1"),  # 1.7 within a file
            '{"parent_asin": "M1", "title": "truncated',  # 1.3
        ],
    )
    write_jsonl_gz(
        raw_dir / "meta_Appliances.jsonl.gz",
        [
            product(parent_asin="KEEP1"),  # 1.7 across files
            product(parent_asin="KEEP2", title="Whirlpool Front Load Washing Machine"),
        ],
    )

    result = stage_products(
        config, sources=("meta_Electronics.jsonl.gz", "meta_Appliances.jsonl.gz")
    )

    assert [row["parent_asin"] for row in rows(config)] == ["KEEP1", "KEEP2"]
    assert result.kept == 2
    assert result.malformed == 1
    assert result.rejected[REJECT_NO_PARENT] == 1
    assert result.rejected[REJECT_TITLE] == 2
    assert result.rejected[REJECT_NO_IMAGE] == 1
    assert result.rejected[REJECT_DUPLICATE] == 2


def test_duplicate_identifier_survives_a_resumed_run(config, raw_dir):
    """The dedupe set is seeded from stored rows, so a resume still deduplicates."""
    write_jsonl_gz(raw_dir / "meta_Electronics.jsonl.gz", [product(parent_asin="DUP")])
    write_jsonl_gz(raw_dir / "meta_Appliances.jsonl.gz", [product(parent_asin="DUP")])

    stage_products(config, sources=("meta_Electronics.jsonl.gz",))
    second = stage_products(config, sources=("meta_Appliances.jsonl.gz",))

    assert second.kept == 0
    assert second.rejected[REJECT_DUPLICATE] == 1
    assert len(rows(config)) == 1


# ---------------------------------------------------------------------------
# Memory behaviour
# ---------------------------------------------------------------------------


def test_writer_never_holds_more_than_one_batch(config, raw_dir):
    """Requirement 1.13: peak buffered rows equals the batch size, never more."""
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=f"B{i:05d}") for i in range(450)],
    )

    result = stage_products(config, sources=("meta_Electronics.jsonl.gz",), batch_size=100)

    assert result.kept == 450
    assert result.peak_pending == 100
    assert len(rows(config)) == 450


def test_writer_flushes_on_the_batch_boundary_and_clears():
    connection = sqlite3.connect(":memory:")
    try:
        ensure_schema(connection)
        writer = CandidateWriter(connection, batch_size=3)
        candidates = []
        for i in range(7):
            candidate, _ = evaluate_record(product(parent_asin=f"P{i}"), "f.gz", set())
            assert candidate is not None
            candidates.append(candidate)

        observed = []
        for candidate in candidates:
            writer.add(candidate)
            observed.append(writer.pending)

        assert observed == [1, 2, 0, 1, 2, 0, 1]
        assert writer.written == 6
        assert writer.peak_pending == 3

        writer.flush()
        assert writer.written == 7
        assert writer.pending == 0
        assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 7
    finally:
        connection.close()


def test_default_batch_size_is_two_thousand():
    assert BATCH_SIZE == 2_000


def test_writer_rejects_a_nonsense_batch_size():
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="batch_size"):
            CandidateWriter(connection, batch_size=0)
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Cap and resumability
# ---------------------------------------------------------------------------


def test_debug_cap_bounds_each_source_file(config, raw_dir):
    """Requirement 1.15."""
    for name in ("meta_Electronics.jsonl.gz", "meta_Appliances.jsonl.gz"):
        write_jsonl_gz(raw_dir / name, [product(parent_asin=f"{name[5]}{i}") for i in range(20)])

    result = stage_products(
        config,
        sources=("meta_Electronics.jsonl.gz", "meta_Appliances.jsonl.gz"),
        max_lines=5,
    )

    assert result.lines_read == 10
    assert result.kept == 10


def test_configured_cap_is_used_when_no_override_is_given(raw_dir, out_dir):
    config = PipelineConfig(raw_dir=raw_dir, out_dir=out_dir, max_lines_debug=4)
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=f"E{i}") for i in range(30)],
    )

    result = stage_products(config, sources=("meta_Electronics.jsonl.gz",))

    assert result.lines_read == 4


def test_second_run_is_a_no_op_at_the_stage_boundary(config, raw_dir):
    """Requirement 1.16: a completed stage does no work on re-run."""
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=f"E{i}") for i in range(10)],
    )
    sources = ("meta_Electronics.jsonl.gz",)

    first = stage_products(config, sources=sources)
    second = stage_products(config, sources=sources)

    assert first.kept == 10
    assert second.already_complete is True
    assert second.lines_read == 0
    assert second.kept == 10  # reported from stored state, not re-read
    assert len(rows(config)) == 10


def test_force_rescans_without_creating_duplicate_rows(config, raw_dir):
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=f"E{i}") for i in range(10)],
    )
    sources = ("meta_Electronics.jsonl.gz",)
    stage_products(config, sources=sources)

    forced = stage_products(config, sources=sources, force=True)

    assert forced.already_complete is False
    assert forced.lines_read == 10
    assert forced.rejected[REJECT_DUPLICATE] == 10  # all already stored
    assert len(rows(config)) == 10


def test_a_changed_cap_reruns_the_stage(config, raw_dir):
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=f"E{i}") for i in range(10)],
    )
    sources = ("meta_Electronics.jsonl.gz",)
    stage_products(config, sources=sources, max_lines=3)

    widened = stage_products(config, sources=sources, max_lines=10)

    assert widened.already_complete is False
    assert widened.lines_read == 10
    assert len(rows(config)) == 10


def test_resume_skips_source_files_already_streamed(config, raw_dir):
    write_jsonl_gz(raw_dir / "meta_Electronics.jsonl.gz", [product(parent_asin="E1")])
    write_jsonl_gz(
        raw_dir / "meta_Appliances.jsonl.gz",
        [product(parent_asin="A1", title="Whirlpool Front Load Washing Machine")],
    )

    stage_products(config, sources=("meta_Electronics.jsonl.gz",))
    resumed = stage_products(
        config,
        sources=("meta_Electronics.jsonl.gz", "meta_Appliances.jsonl.gz"),
        force=False,
    )

    assert resumed.files_skipped == ["meta_Electronics.jsonl.gz"]
    assert resumed.files_processed == ["meta_Appliances.jsonl.gz"]
    assert resumed.lines_read == 1
    assert len(rows(config)) == 2


def test_stage_state_records_the_boundary(config, raw_dir):
    write_jsonl_gz(raw_dir / "meta_Electronics.jsonl.gz", [product(parent_asin="E1")])

    stage_products(config, sources=("meta_Electronics.jsonl.gz",), max_lines=7)

    connection = connect(config.candidates_db)
    try:
        state = connection.execute("SELECT * FROM stage_state WHERE stage = 'products'").fetchone()
        progress = connection.execute("SELECT * FROM source_progress").fetchall()
    finally:
        connection.close()

    assert state["status"] == "complete"
    assert state["max_lines_debug"] == 7
    assert state["records"] == 1
    assert [row["source_file"] for row in progress] == ["meta_Electronics.jsonl.gz"]


# ---------------------------------------------------------------------------
# Immutability of the source tree
# ---------------------------------------------------------------------------


def test_raw_directory_is_never_modified(config, raw_dir):
    """Requirement 1.2."""
    write_jsonl_gz(
        raw_dir / "meta_Electronics.jsonl.gz",
        [product(parent_asin=f"E{i}") for i in range(50)],
    )
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(raw_dir.iterdir())
    }

    stage_products(config, sources=("meta_Electronics.jsonl.gz",))

    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(raw_dir.iterdir())
    }
    assert after == before


def test_output_inside_the_raw_directory_is_refused(raw_dir):
    config = PipelineConfig(raw_dir=raw_dir, out_dir=raw_dir / "out")

    with pytest.raises(ValueError, match="immutable raw directory"):
        stage_products(config)


def test_missing_sources_are_reported_not_crashed_over(config, raw_dir):
    write_jsonl_gz(raw_dir / "meta_Electronics.jsonl.gz", [product(parent_asin="E1")])

    result = stage_products(
        config, sources=("meta_Electronics.jsonl.gz", "meta_Nonexistent.jsonl.gz")
    )

    assert result.files_missing == ["meta_Nonexistent.jsonl.gz"]
    assert result.kept == 1


def test_no_sources_at_all_fails_loudly(config):
    with pytest.raises(FileNotFoundError, match="no metadata source found"):
        stage_products(config, sources=("meta_Nonexistent.jsonl.gz",))
