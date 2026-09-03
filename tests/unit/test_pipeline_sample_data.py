"""Tests for pipeline.sample_data module."""

from __future__ import annotations

import gzip
import json
import random
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.sample_data import (
    DEFAULT_RECORDS_PER_FILE,
    DEFAULT_REVIEWS_PER_FILE,
    DEFAULT_SEED,
    ELECTRONICS_TITLES,
    META_FILES,
    REVIEW_FILES,
    build_parser,
    generate_metadata_record,
    generate_review_record,
    generate_sample_data,
    main,
    write_jsonl_gz,
)

# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_build_parser_returns_parser() -> None:
    """The parser should be constructable without errors."""
    parser = build_parser()
    assert parser is not None


def test_parser_defaults() -> None:
    """Default values match module constants."""
    parser = build_parser()
    args = parser.parse_args([])
    assert args.output_dir is None
    assert args.records == DEFAULT_RECORDS_PER_FILE
    assert args.reviews == DEFAULT_REVIEWS_PER_FILE
    assert args.seed == DEFAULT_SEED


def test_parser_output_dir_flag() -> None:
    """--output-dir should accept a path."""
    parser = build_parser()
    args = parser.parse_args(["--output-dir", "/tmp/sample"])
    assert args.output_dir == Path("/tmp/sample")


def test_parser_records_flag() -> None:
    """--records should accept an integer."""
    parser = build_parser()
    args = parser.parse_args(["--records", "50"])
    assert args.records == 50


def test_parser_seed_flag() -> None:
    """--seed should accept an integer."""
    parser = build_parser()
    args = parser.parse_args(["--seed", "123"])
    assert args.seed == 123


# ---------------------------------------------------------------------------
# Record generation tests
# ---------------------------------------------------------------------------


def test_generate_metadata_record_has_required_fields() -> None:
    """Metadata records must have all fields build_catalog expects."""
    rng = random.Random(42)
    record = generate_metadata_record(rng, ELECTRONICS_TITLES, "All Electronics")

    required_fields = {
        "parent_asin",
        "title",
        "images",
        "features",
        "description",
        "details",
        "average_rating",
        "rating_number",
        "price",
    }
    assert required_fields.issubset(set(record.keys()))


def test_generate_metadata_record_parent_asin_format() -> None:
    """parent_asin should look like a real ASIN (starts with B0, 10 chars)."""
    rng = random.Random(42)
    record = generate_metadata_record(rng, ELECTRONICS_TITLES, "All Electronics")
    asin = record["parent_asin"]
    assert isinstance(asin, str)
    assert len(asin) == 10
    assert asin.startswith("B0")


def test_generate_metadata_record_images_are_list_of_dicts() -> None:
    """Images must be a list of dicts with hi_res, large, thumb, variant keys."""
    rng = random.Random(42)
    record = generate_metadata_record(rng, ELECTRONICS_TITLES, "All Electronics")
    images = record["images"]
    assert isinstance(images, list)
    assert len(images) >= 1
    for img in images:
        assert isinstance(img, dict)
        assert "hi_res" in img
        assert "large" in img
        assert "thumb" in img
        assert "variant" in img
        # URLs should be valid
        assert img["hi_res"].startswith("https://")


def test_generate_metadata_record_rating_bounds() -> None:
    """average_rating should be between 2.5 and 5.0."""
    rng = random.Random(42)
    for _ in range(20):
        record = generate_metadata_record(rng, ELECTRONICS_TITLES, "All Electronics")
        assert 2.5 <= record["average_rating"] <= 5.0


def test_generate_metadata_record_price_format() -> None:
    """Price should be a string starting with $."""
    rng = random.Random(42)
    record = generate_metadata_record(rng, ELECTRONICS_TITLES, "All Electronics")
    price = record["price"]
    assert isinstance(price, str)
    assert price.startswith("$")
    # Should be parseable as a float after removing $
    float(price.lstrip("$"))


def test_generate_review_record_has_required_fields() -> None:
    """Review records must have the fields stage 5 expects."""
    rng = random.Random(42)
    record = generate_review_record(rng, "B012345678")

    required_fields = {"rating", "title", "text", "parent_asin", "user_id"}
    assert required_fields.issubset(set(record.keys()))


def test_generate_review_record_rating_bounds() -> None:
    """rating should be 1-5."""
    rng = random.Random(42)
    for _ in range(20):
        record = generate_review_record(rng, "B012345678")
        assert 1 <= record["rating"] <= 5


def test_generate_review_record_parent_asin_matches() -> None:
    """The review's parent_asin should match the input."""
    rng = random.Random(42)
    record = generate_review_record(rng, "B0TESTTEST")
    assert record["parent_asin"] == "B0TESTTEST"


# ---------------------------------------------------------------------------
# File writing tests
# ---------------------------------------------------------------------------


def test_write_jsonl_gz_creates_readable_gzipped_file(tmp_path: Path) -> None:
    """write_jsonl_gz should create a valid gzipped JSONL file."""
    records: list[dict[str, object]] = [{"key": "value1"}, {"key": "value2"}]
    path = tmp_path / "test.jsonl.gz"
    write_jsonl_gz(path, records)

    assert path.is_file()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"key": "value1"}
    assert json.loads(lines[1]) == {"key": "value2"}


def test_write_jsonl_gz_creates_parent_dirs(tmp_path: Path) -> None:
    """write_jsonl_gz should create parent directories if needed."""
    path = tmp_path / "sub" / "dir" / "test.jsonl.gz"
    write_jsonl_gz(path, [{"a": 1}])
    assert path.is_file()


# ---------------------------------------------------------------------------
# Full generation tests
# ---------------------------------------------------------------------------


def test_generate_sample_data_creates_all_files(tmp_path: Path) -> None:
    """generate_sample_data should create all 6 expected files."""
    counts = generate_sample_data(tmp_path, records_per_file=10, reviews_per_file=15, seed=42)

    for filename in META_FILES:
        assert (tmp_path / filename).is_file(), f"Missing {filename}"
    for filename in REVIEW_FILES:
        assert (tmp_path / filename).is_file(), f"Missing {filename}"

    # Check counts
    assert len(counts) == 6
    for filename in META_FILES:
        assert counts[filename] == 10
    for filename in REVIEW_FILES:
        assert counts[filename] == 15


def test_generate_sample_data_is_deterministic(tmp_path: Path) -> None:
    """Same seed should produce identical output."""
    dir1 = tmp_path / "run1"
    dir2 = tmp_path / "run2"

    generate_sample_data(dir1, records_per_file=10, reviews_per_file=15, seed=42)
    generate_sample_data(dir2, records_per_file=10, reviews_per_file=15, seed=42)

    for filename in META_FILES + REVIEW_FILES:
        content1 = (dir1 / filename).read_bytes()
        content2 = (dir2 / filename).read_bytes()
        assert content1 == content2, f"{filename} differs between runs"


def test_generate_sample_data_different_seeds_produce_different_output(tmp_path: Path) -> None:
    """Different seeds should produce different output."""
    dir1 = tmp_path / "seed1"
    dir2 = tmp_path / "seed2"

    generate_sample_data(dir1, records_per_file=10, reviews_per_file=15, seed=42)
    generate_sample_data(dir2, records_per_file=10, reviews_per_file=15, seed=99)

    # At least one file should differ
    any_different = False
    for filename in META_FILES:
        if (dir1 / filename).read_bytes() != (dir2 / filename).read_bytes():
            any_different = True
            break
    assert any_different


def test_generated_metadata_is_valid_for_pipeline(tmp_path: Path) -> None:
    """Generated metadata records should be parseable by iter_jsonl_gz."""
    generate_sample_data(tmp_path, records_per_file=5, reviews_per_file=5, seed=42)

    from pipeline.build_catalog import iter_jsonl_gz

    records = list(iter_jsonl_gz(tmp_path / "meta_Electronics.jsonl.gz"))
    assert len(records) == 5
    for record in records:
        assert "parent_asin" in record
        assert "title" in record
        assert "images" in record
        assert isinstance(record["images"], list)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_main_runs_without_error(tmp_path: Path) -> None:
    """main() should succeed with minimal arguments."""
    with patch("pipeline.sample_data.load_config") as mock_config:
        mock_cfg = MagicMock()
        mock_cfg.raw_dir = tmp_path
        mock_config.return_value = mock_cfg
        result = main(["--records", "5", "--reviews", "5"])
        assert result == 0

    # Verify files were created
    for filename in META_FILES:
        assert (tmp_path / filename).is_file()


def test_main_respects_output_dir(tmp_path: Path) -> None:
    """main() should use --output-dir when provided."""
    custom_dir = tmp_path / "custom"
    result = main(["--output-dir", str(custom_dir), "--records", "5", "--reviews", "5"])
    assert result == 0
    for filename in META_FILES:
        assert (custom_dir / filename).is_file()


def test_generate_sample_data_refuses_to_write_into_immutable_datasets_dir() -> None:
    """Safety guard: generate_sample_data must refuse to write into immutable datasets directory."""
    import pytest

    from pipeline.config import DEFAULT_RAW_DIR

    with pytest.raises(ValueError, match="Safety guard violation"):
        generate_sample_data(DEFAULT_RAW_DIR)

    with pytest.raises(ValueError, match="Safety guard violation"):
        generate_sample_data(DEFAULT_RAW_DIR / "nested")
