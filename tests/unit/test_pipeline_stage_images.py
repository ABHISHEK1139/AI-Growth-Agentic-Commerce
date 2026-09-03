"""Stage 3 end to end: best-resolution fallback, unique storage keys, no download.

The stage reads ``products.jsonl`` and nothing else. The test that matters most
here is the negative one: no network call happens, asserted by making a socket
impossible rather than by reading the code and trusting it.
"""

from __future__ import annotations

import json
import socket

import pytest

from pipeline.build_catalog import (
    IMAGE_RESOLUTION_ORDER,
    STAGE_IMAGES,
    connect,
    read_stage_state,
    stage_images,
    stage_select,
)
from pipeline.config import PipelineConfig
from tests.unit.test_pipeline_normalization import IMAGE_HI, IMAGE_LARGE, IMAGE_THUMB
from tests.unit.test_pipeline_stage_select import candidate, seed

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


def images(*entries) -> dict:
    """A ``record`` override carrying exactly the given image entries."""
    return {
        "images": [
            {"hi_res": hi, "large": large, "thumb": thumb, "variant": variant}
            for hi, large, thumb, variant in entries
        ]
    }


def manifest_of(config: PipelineConfig) -> list[dict]:
    with config.images_manifest_jsonl.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def select(config: PipelineConfig, candidates, quotas=None):
    seed(config, candidates)
    return stage_select(config, quotas=quotas or {"laptop": len(candidates)})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_manifest_has_one_line_per_image_with_a_storage_key(config):
    select(
        config,
        [
            candidate(
                "P1",
                record=images(
                    (IMAGE_HI, IMAGE_LARGE, IMAGE_THUMB, "MAIN"),
                    (None, "https://x.test/l2.jpg", None, "PT01"),
                ),
            )
        ],
    )

    result = stage_images(config)

    assert result.products_read == 1
    assert result.images_written == 2
    rows = manifest_of(config)
    product_id = rows[0]["product_id"]
    assert [row["storage_key"] for row in rows] == [
        f"{product_id}/main.jpg",
        f"{product_id}/pt01.jpg",
    ]
    assert rows[0]["source_url"] == IMAGE_HI
    assert rows[0]["resolution"] == "hi_res"
    assert rows[0]["external_product_id"] == "P1"
    assert all(row["downloaded"] is False for row in rows)


def test_storage_key_is_product_id_slash_variant(config):
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])

    stage_images(config)

    row = manifest_of(config)[0]
    assert row["storage_key"] == f"{row['product_id']}/main.jpg"
    assert row["storage_key"].endswith(".jpg")


# ---------------------------------------------------------------------------
# Best-resolution fallback (Requirement 2.9)
# ---------------------------------------------------------------------------


def test_all_three_fallback_cases_resolve_correctly(config):
    """hi_res present, only large, only thumb -- each reaches the manifest once."""
    select(
        config,
        [
            candidate("HI", record=images((IMAGE_HI, IMAGE_LARGE, IMAGE_THUMB, "MAIN"))),
            candidate("LG", record=images((None, IMAGE_LARGE, IMAGE_THUMB, "MAIN"))),
            candidate("TH", record=images((None, None, IMAGE_THUMB, "MAIN"))),
        ],
        quotas={"laptop": 3},
    )

    result = stage_images(config)

    by_external = {row["external_product_id"]: row for row in manifest_of(config)}
    assert (by_external["HI"]["resolution"], by_external["HI"]["source_url"]) == (
        "hi_res",
        IMAGE_HI,
    )
    assert (by_external["LG"]["resolution"], by_external["LG"]["source_url"]) == (
        "large",
        IMAGE_LARGE,
    )
    assert (by_external["TH"]["resolution"], by_external["TH"]["source_url"]) == (
        "thumb",
        IMAGE_THUMB,
    )
    assert result.by_resolution == {"hi_res": 1, "large": 1, "thumb": 1}
    assert set(result.by_resolution) <= set(IMAGE_RESOLUTION_ORDER)


def test_a_product_whose_images_are_all_unusable_is_counted_not_crashed_over(config):
    """Stage 1 rejects imageless records, so this is a defence, not an expectation."""
    select(config, [candidate("NONE1", images=[])])

    result = stage_images(config)

    assert result.products_read == 1
    assert result.images_written == 0
    assert result.products_without_images == 1
    assert manifest_of(config) == []


# ---------------------------------------------------------------------------
# Dedupe and key uniqueness (Requirement 2.10)
# ---------------------------------------------------------------------------


def test_duplicate_urls_within_a_product_appear_once(config):
    select(
        config,
        [
            candidate(
                "DUP",
                record=images(
                    (IMAGE_HI, None, None, "MAIN"),
                    (IMAGE_HI, None, None, "PT01"),  # same URL, different variant
                    (None, IMAGE_LARGE, None, "PT02"),
                ),
            )
        ],
    )

    result = stage_images(config)

    rows = manifest_of(config)
    assert [row["source_url"] for row in rows] == [IMAGE_HI, IMAGE_LARGE]
    assert result.images_written == 2
    assert result.images_skipped == 0  # stage 2 already collapsed the duplicate


def test_no_duplicate_storage_key_anywhere_in_the_manifest(config):
    """Requirement 2.10, across the whole artifact rather than within one product.

    The variants deliberately repeat inside each product, which is the case that
    would otherwise have one image silently overwrite another.
    """
    candidates = [
        candidate(
            f"P{index:02d}",
            score=index,
            record=images(
                (f"https://x.test/{index}/a.jpg", None, None, "MAIN"),
                (f"https://x.test/{index}/b.jpg", None, None, "MAIN"),
                (f"https://x.test/{index}/c.jpg", None, None, None),
                (f"https://x.test/{index}/d.jpg", None, None, "PT01"),
            ),
        )
        for index in range(25)
    ]
    select(config, candidates, quotas={"laptop": 25})

    result = stage_images(config)

    keys = [row["storage_key"] for row in manifest_of(config)]
    assert len(keys) == result.images_written == 100
    assert len(set(keys)) == len(keys)
    # Every key is scoped to its own product, so two products cannot collide.
    for row in manifest_of(config):
        assert row["storage_key"].startswith(f"{row['product_id']}/")


def test_repeated_variants_within_a_product_are_disambiguated(config):
    select(
        config,
        [
            candidate(
                "P1",
                record=images(
                    ("https://x.test/a.jpg", None, None, "MAIN"),
                    ("https://x.test/b.jpg", None, None, "MAIN"),
                ),
            )
        ],
    )

    stage_images(config)

    rows = manifest_of(config)
    product_id = rows[0]["product_id"]
    assert [row["storage_key"] for row in rows] == [
        f"{product_id}/main.jpg",
        f"{product_id}/main_01.jpg",
    ]


def test_a_missing_variant_uses_the_position(config):
    select(config, [candidate("P1", record=images(("https://x.test/a.jpg", None, None, None)))])

    stage_images(config)

    row = manifest_of(config)[0]
    assert row["storage_key"] == f"{row['product_id']}/image_00.jpg"
    assert row["variant"] is None


# ---------------------------------------------------------------------------
# Nothing is downloaded (Requirement 2.11)
# ---------------------------------------------------------------------------


def test_the_stage_cannot_have_downloaded_anything(config, monkeypatch):
    """Requirement 2.11, asserted by making a network call impossible."""
    select(
        config,
        [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))],
    )

    def _refuse(*args, **kwargs):
        raise AssertionError("stage 3 opened a socket; it must never download an image")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)

    result = stage_images(config)

    assert result.images_written == 1
    assert all(row["downloaded"] is False for row in manifest_of(config))


def test_no_image_bytes_land_on_disk(config):
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])
    before = {path.name for path in config.catalog_dir.iterdir()}

    stage_images(config)

    after = {path.name for path in config.catalog_dir.iterdir()}
    assert after - before == {"images_manifest.jsonl"}
    assert not list(config.catalog_dir.rglob("*.jpg"))


# ---------------------------------------------------------------------------
# Stage boundary and guard rails
# ---------------------------------------------------------------------------


def test_second_run_is_a_no_op_at_the_stage_boundary(config):
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])
    stage_images(config)
    stamp = config.images_manifest_jsonl.stat().st_mtime_ns

    second = stage_images(config)

    assert second.already_complete is True
    assert second.images_written == 1
    assert config.images_manifest_jsonl.stat().st_mtime_ns == stamp


def test_force_rebuilds_the_manifest(config):
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])
    stage_images(config)

    forced = stage_images(config, force=True)

    assert forced.already_complete is False
    assert forced.images_written == 1


def test_stage_state_records_the_boundary(config):
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])

    stage_images(config)

    connection = connect(config.candidates_db)
    try:
        state = read_stage_state(connection, STAGE_IMAGES)
    finally:
        connection.close()

    assert state["status"] == "complete"
    assert state["records"] == 1


def test_missing_products_file_says_which_stage_to_run(config):
    seed(config, [candidate("P1")])

    with pytest.raises(FileNotFoundError, match=r"run 'select' \(stage 2\) first"):
        stage_images(config)


def test_a_malformed_manifest_input_line_is_skipped_not_fatal(config):
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])
    with config.products_jsonl.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"product_id": "prod_truncated"\n')
        handle.write("[1, 2, 3]\n")
        handle.write('{"images": []}\n')  # no product_id

    result = stage_images(config, force=True)

    assert result.malformed_lines == 3
    assert result.products_read == 1
    assert result.images_written == 1


def test_stage_three_reads_only_the_products_artifact(config, raw_dir):
    (raw_dir / "meta_Electronics.jsonl.gz").write_bytes(b"not read by stage 3")
    select(config, [candidate("P1", record=images((IMAGE_HI, None, None, "MAIN")))])
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in raw_dir.iterdir()
    }

    stage_images(config)

    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in raw_dir.iterdir()
    }
    assert after == before


def test_output_inside_the_raw_directory_is_refused(raw_dir):
    config = PipelineConfig(raw_dir=raw_dir, out_dir=raw_dir / "out")

    with pytest.raises(ValueError, match="immutable raw directory"):
        stage_images(config)
