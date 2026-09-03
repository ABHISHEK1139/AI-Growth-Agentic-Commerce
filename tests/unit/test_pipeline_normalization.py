"""Stage 1 streaming, normalization, classification, scoring, and hard rejects.

Every test here works against a real gzipped fixture or a real record shape. The
source dataset is dirty in specific, known ways -- two image shapes, details as
JSON text, truncated lines -- and each of those ways gets a case.
"""

from __future__ import annotations

import gzip
import json

import pytest

from pipeline.build_catalog import (
    MAX_SCORE,
    REJECT_DUPLICATE,
    REJECT_NO_IMAGE,
    REJECT_NO_PARENT,
    REJECT_TITLE,
    SUBCATEGORIES,
    TITLE_MAX_LENGTH,
    TITLE_MIN_LENGTH,
    UNCATEGORIZED,
    ScanStats,
    best_image_url,
    classify_subcategory,
    completeness_score,
    evaluate_record,
    is_usable_url,
    iter_jsonl_gz,
    normalize_details,
    normalize_images,
    normalize_text_list,
    parse_price_usd,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

IMAGE_HI = "https://example.test/i/hi_res_1.jpg"
IMAGE_LARGE = "https://example.test/i/large_1.jpg"
IMAGE_THUMB = "https://example.test/i/thumb_1.jpg"


def write_jsonl_gz(path, entries) -> None:
    """Write a gzipped JSONL fixture.

    Dict entries are serialized; string entries are written verbatim, which is
    how a malformed line gets into a fixture at all.
    """
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for entry in entries:
            line = entry if isinstance(entry, str) else json.dumps(entry)
            handle.write(line + "\n")


def product(**overrides):
    """A minimally valid source record, before overrides."""
    record = {
        "parent_asin": "B000000001",
        "main_category": "All Electronics",
        "title": "Acme 15 inch Laptop Computer Model X",
        "features": ["16 GB RAM", "512 GB storage"],
        "description": ["A laptop for testing."],
        "price": 899.99,
        "images": [
            {"thumb": IMAGE_THUMB, "large": IMAGE_LARGE, "hi_res": IMAGE_HI, "variant": "MAIN"}
        ],
        "videos": [],
        "store": "Acme",
        "categories": ["Electronics", "Computers"],
        "details": {"Brand": "Acme", "Weight": "4 lbs"},
        "average_rating": 4.5,
        "rating_number": 120,
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------
# iter_jsonl_gz
# ---------------------------------------------------------------------------


def test_malformed_lines_are_skipped_without_aborting(tmp_path):
    """Requirement 1.3: one corrupt line must not cost the whole pass."""
    path = tmp_path / "meta_Test.jsonl.gz"
    write_jsonl_gz(
        path,
        [
            product(parent_asin="A1"),
            '{"parent_asin": "A2", "title": "truncated',  # malformed
            "",  # blank, not malformed
            "not json at all",  # malformed
            "[1, 2, 3]",  # valid JSON, wrong shape
            product(parent_asin="A3"),
        ],
    )
    stats = ScanStats()

    records = list(iter_jsonl_gz(path, stats=stats))

    assert [record["parent_asin"] for record in records] == ["A1", "A3"]
    assert stats.lines_read == 6
    assert stats.malformed == 3


def test_source_is_never_decompressed_to_disk(tmp_path):
    """Requirement 1.1: read in place, in text mode, nothing new on disk."""
    path = tmp_path / "meta_Test.jsonl.gz"
    write_jsonl_gz(path, [product()])
    before = sorted(p.name for p in tmp_path.iterdir())

    records = list(iter_jsonl_gz(path))

    assert len(records) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_max_lines_caps_lines_read_including_malformed(tmp_path):
    """Requirement 1.15: the cap bounds input read, not records yielded."""
    path = tmp_path / "meta_Test.jsonl.gz"
    write_jsonl_gz(path, ["broken {", product(parent_asin="A1"), product(parent_asin="A2")])
    stats = ScanStats()

    records = list(iter_jsonl_gz(path, max_lines=2, stats=stats))

    assert stats.lines_read == 2
    assert [record["parent_asin"] for record in records] == ["A1"]


def test_no_cap_reads_the_whole_file(tmp_path):
    path = tmp_path / "meta_Test.jsonl.gz"
    write_jsonl_gz(path, [product(parent_asin=f"A{i}") for i in range(25)])

    assert len(list(iter_jsonl_gz(path, max_lines=None))) == 25


# ---------------------------------------------------------------------------
# normalize_images
# ---------------------------------------------------------------------------


def test_both_image_shapes_normalize_identically():
    """Requirement 1.8: per-image objects and columnar lists are one shape."""
    per_image = [
        {"thumb": IMAGE_THUMB, "large": IMAGE_LARGE, "hi_res": IMAGE_HI, "variant": "MAIN"},
        {
            "thumb": "https://example.test/i/t2.jpg",
            "large": "https://example.test/i/l2.jpg",
            "hi_res": None,
            "variant": "PT01",
        },
    ]
    columnar = {
        "hi_res": [IMAGE_HI, None],
        "large": [IMAGE_LARGE, "https://example.test/i/l2.jpg"],
        "thumb": [IMAGE_THUMB, "https://example.test/i/t2.jpg"],
        "variant": ["MAIN", "PT01"],
    }

    assert normalize_images(per_image) == normalize_images(columnar)
    assert len(normalize_images(columnar)) == 2


def test_columnar_shape_tolerates_short_columns():
    columnar = {"large": [IMAGE_LARGE, "https://example.test/i/l2.jpg"], "variant": ["MAIN"]}

    normalized = normalize_images(columnar)

    assert len(normalized) == 2
    assert normalized[1]["variant"] is None
    assert normalized[1]["large"] == "https://example.test/i/l2.jpg"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        "not an image",
        [{"thumb": None, "large": None, "hi_res": None, "variant": "MAIN"}],
        [{"thumb": "", "large": "   ", "hi_res": None}],
        [{"large": "ftp://example.test/x.jpg"}],
        {"large": [None, ""], "variant": ["MAIN", "PT01"]},
    ],
)
def test_entries_without_a_usable_url_are_dropped(raw):
    assert normalize_images(raw) == []


def test_bare_url_strings_are_accepted():
    assert normalize_images([IMAGE_LARGE]) == [
        {"hi_res": None, "large": IMAGE_LARGE, "thumb": None, "variant": None}
    ]


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ({"hi_res": IMAGE_HI, "large": IMAGE_LARGE, "thumb": IMAGE_THUMB}, IMAGE_HI),
        ({"hi_res": None, "large": IMAGE_LARGE, "thumb": IMAGE_THUMB}, IMAGE_LARGE),
        ({"hi_res": None, "large": None, "thumb": IMAGE_THUMB}, IMAGE_THUMB),
        ({"hi_res": None, "large": None, "thumb": None}, None),
    ],
)
def test_best_image_url_prefers_highest_resolution(image, expected):
    assert best_image_url(image) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.test/a.jpg", True),
        ("http://example.test/a.jpg", True),
        ("HTTPS://EXAMPLE.TEST/A.JPG", True),
        ("//example.test/a.jpg", False),
        ("example.test/a.jpg", False),
        ("", False),
        (None, False),
        (7, False),
    ],
)
def test_is_usable_url(value, expected):
    assert is_usable_url(value) is expected


# ---------------------------------------------------------------------------
# normalize_details / normalize_text_list
# ---------------------------------------------------------------------------


def test_details_as_object_is_passed_through():
    assert normalize_details({"Brand": "Acme", "Weight": "4 lbs"}) == {
        "Brand": "Acme",
        "Weight": "4 lbs",
    }


def test_details_as_json_encoded_string_is_parsed():
    """Requirement 1.9, first half."""
    assert normalize_details('{"Brand": "Acme", "Colour": "Black"}') == {
        "Brand": "Acme",
        "Colour": "Black",
    }


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "{not: valid json",
        '["a", "b"]',  # valid JSON, wrong shape
        '"just a string"',
        42,
        ["a", "b"],
    ],
)
def test_details_falls_back_to_empty_object_rather_than_raising(raw):
    """Requirement 1.9, second half."""
    assert normalize_details(raw) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["a", "b"], ["a", "b"]),
        ("a single string", ["a single string"]),
        (None, []),
        ([], []),
        ("", []),
        ("   ", []),
        (["  padded  ", "", None, "  "], ["padded"]),
        ([1, 2.5], ["1", "2.5"]),
        ({"unexpected": "shape"}, []),
    ],
)
def test_normalize_text_list_handles_list_string_and_absent(raw, expected):
    """Requirement 1.10."""
    assert normalize_text_list(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (899.99, 899.99),
        (12, 12.0),
        ("$1,299.00", 1299.0),
        ("49.5", 49.5),
        (None, None),
        ("", None),
        ("N/A", None),
        (0, None),
        (-5, None),
        (True, None),
    ],
)
def test_parse_price_usd(raw, expected):
    assert parse_price_usd(raw) == expected


# ---------------------------------------------------------------------------
# classify_subcategory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Dell Inspiron 15 Laptop Computer 16GB", "laptop"),
        ("Apple MacBook Air 13 inch", "laptop"),
        ("Apple iPhone 12 Unlocked Smartphone 128GB", "smartphone"),
        ("Samsung Galaxy S21 Cell Phone", "smartphone"),
        ("ASUS 24 inch LED Monitor Full HD", "monitor"),
        ("Sony WH-1000XM4 Wireless Noise Cancelling Headphones", "audio"),
        ("JBL Flip 5 Portable Bluetooth Speaker", "audio"),
        ("Canon EOS Rebel T7 DSLR Camera Body", "camera"),
        ("Logitech M510 Wireless Mouse for Computers", "computer_accessory"),
        ("Laptop Sleeve Case for 15 inch Notebook", "computer_accessory"),
        ("Samsung 1TB Internal SSD Solid State Drive", "computer_accessory"),
        ("Tempered Glass Screen Protector 2 Pack", "phone_accessory"),
        ("Silicone Case for iPhone 12 Pro", "phone_accessory"),
        ("Cell Phone Charger Cable 6ft", "phone_accessory"),
        ("Samsung 55 inch 4K Smart TV", "home_electronics"),
        ("Ring Video Doorbell with Security Camera", "home_electronics"),
        ("Baby Monitor with Camera and Two Way Audio", "home_electronics"),
        ("Whirlpool Front Load Washing Machine", "appliance"),
        ("Refrigerator Water Filter Replacement", "appliance"),
        ("Mysterious Widget Assembly Kit", UNCATEGORIZED),
    ],
)
def test_classify_subcategory_keyword_rules(title, expected):
    """Requirement 1.11: exactly one label, from the fixed set."""
    assert classify_subcategory(title) == expected


def test_classifier_output_is_always_in_the_fixed_set():
    titles = [
        "",
        "   ",
        "Zzzz",
        "TV",
        "atv accessory",
        "Something entirely unrelated to electronics",
    ]

    for title in titles:
        assert classify_subcategory(title) in SUBCATEGORIES


def test_features_are_consulted_when_the_title_places_nothing():
    assert classify_subcategory("Acme Model 9000", ["Gaming laptop with 16GB RAM"]) == "laptop"


def test_the_title_outranks_the_features():
    """A laptop that lists "16 GB RAM" is a laptop, not a memory module."""
    assert (
        classify_subcategory(
            "Dell Inspiron 15 Laptop Computer", ["16 GB RAM", "512 GB SSD storage"]
        )
        == "laptop"
    )


def test_an_accessory_label_must_be_earned_by_the_title():
    """Features describe what a device contains; they cannot make it an accessory."""
    assert classify_subcategory("Acme Model 9000", ["Includes iPhone case", "USB cable"]) not in {
        "phone_accessory",
        "computer_accessory",
    }
    assert (
        classify_subcategory("Acme Widget 9000", ["16 GB RAM", "512 GB storage"]) == UNCATEGORIZED
    )


def test_uncategorized_is_a_fallback_not_a_discard():
    assert classify_subcategory("Unrecognizable Product Name") == UNCATEGORIZED
    assert UNCATEGORIZED in SUBCATEGORIES


# ---------------------------------------------------------------------------
# completeness_score
# ---------------------------------------------------------------------------


def _score(**overrides) -> int:
    kwargs = {
        "title": "",
        "features": [],
        "description": [],
        "images": [],
        "details": {},
        "rating_number": 0,
        "average_rating": 0.0,
    }
    kwargs.update(overrides)
    return completeness_score(**kwargs)  # type: ignore[arg-type]


def test_empty_record_scores_zero():
    assert _score() == 0


def test_barely_valid_record_scores_low():
    score = _score(title="12345678", images=[{"large": IMAGE_LARGE}])

    assert 0 < score <= 20


def test_rich_record_scores_near_the_maximum():
    score = _score(
        title="Acme Professional 15 inch Laptop Computer with 16GB RAM and 512GB SSD Storage",
        features=[f"feature {i}" for i in range(8)],
        description=["x" * 400],
        images=[{"hi_res": f"https://example.test/i/{i}.jpg"} for i in range(6)],
        details={f"k{i}": "v" for i in range(12)},
        rating_number=5_000,
        average_rating=4.8,
    )

    assert score >= 90


def test_score_reaches_exactly_one_hundred_when_every_factor_is_maximal():
    score = _score(
        title="A" * 120,
        features=[f"feature {i}" for i in range(10)],
        description=["y" * 1000],
        images=[{"hi_res": f"https://example.test/i/{i}.jpg"} for i in range(9)],
        details={f"k{i}": "v" for i in range(20)},
        rating_number=100_000,
        average_rating=5.0,
    )

    assert score == MAX_SCORE == 100


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"title": "x" * 5_000},
        {"rating_number": -100, "average_rating": -9.0},
        {"average_rating": 99.0, "rating_number": 10**9},
        {"features": ["f"] * 500, "details": {str(i): i for i in range(500)}},
        {"images": [{"large": IMAGE_LARGE}] * 200},
    ],
)
def test_score_is_always_within_bounds(kwargs):
    """Requirement 1.12: the range is 0 to 100, with no way out of it."""
    assert 0 <= _score(**kwargs) <= 100


def test_score_is_monotonic_in_added_evidence():
    base = _score(title="12345678", images=[{"large": IMAGE_LARGE}])
    with_features = _score(
        title="12345678", images=[{"large": IMAGE_LARGE}], features=["a", "b", "c"]
    )
    with_ratings = _score(
        title="12345678",
        images=[{"large": IMAGE_LARGE}],
        features=["a", "b", "c"],
        rating_number=500,
        average_rating=4.6,
    )

    assert base < with_features < with_ratings


def test_images_factor_counts_only_usable_urls():
    unusable = _score(title="12345678", images=[{"large": None, "hi_res": None, "thumb": None}])
    absent = _score(title="12345678", images=[])
    usable = _score(title="12345678", images=[{"large": IMAGE_LARGE}])

    assert unusable == absent < usable


# ---------------------------------------------------------------------------
# evaluate_record: the four hard rejects
# ---------------------------------------------------------------------------


def test_valid_record_is_accepted_and_fully_normalized():
    candidate, reason = evaluate_record(product(), "meta_Test.jsonl.gz", set())

    assert reason is None
    assert candidate is not None
    assert candidate.parent_asin == "B000000001"
    assert candidate.subcategory == "laptop"
    assert candidate.source_file == "meta_Test.jsonl.gz"
    assert candidate.features == ["16 GB RAM", "512 GB storage"]
    assert candidate.details == {"Brand": "Acme", "Weight": "4 lbs"}
    assert candidate.price_usd == 899.99
    assert candidate.status == "valid"
    assert 0 <= candidate.score <= 100


@pytest.mark.parametrize("value", [None, "", "   "])
def test_reject_missing_parent_identifier(value):
    """Requirement 1.4."""
    candidate, reason = evaluate_record(product(parent_asin=value), "f.gz", set())

    assert candidate is None
    assert reason == REJECT_NO_PARENT


def test_reject_missing_parent_identifier_when_key_absent():
    record = product()
    del record["parent_asin"]

    candidate, reason = evaluate_record(record, "f.gz", set())

    assert candidate is None
    assert reason == REJECT_NO_PARENT


@pytest.mark.parametrize(
    "title",
    [None, "", "short", "a" * (TITLE_MIN_LENGTH - 1), "a" * (TITLE_MAX_LENGTH + 1), "b" * 900],
)
def test_reject_title_outside_bounds(title):
    """Requirement 1.5."""
    candidate, reason = evaluate_record(product(title=title), "f.gz", set())

    assert candidate is None
    assert reason == REJECT_TITLE


@pytest.mark.parametrize("length", [TITLE_MIN_LENGTH, 50, TITLE_MAX_LENGTH])
def test_titles_on_the_boundary_are_accepted(length):
    candidate, reason = evaluate_record(product(title="t" * length), "f.gz", set())

    assert reason is None
    assert candidate is not None


@pytest.mark.parametrize(
    "images",
    [
        None,
        [],
        [{"thumb": None, "large": None, "hi_res": None}],
        [{"thumb": "", "large": "", "hi_res": ""}],
        {"hi_res": [], "large": [], "thumb": []},
    ],
)
def test_reject_records_without_a_usable_image(images):
    """Requirement 1.6."""
    candidate, reason = evaluate_record(product(images=images), "f.gz", set())

    assert candidate is None
    assert reason == REJECT_NO_IMAGE


def test_reject_duplicate_parent_identifier():
    """Requirement 1.7."""
    seen = {"B000000001"}

    candidate, reason = evaluate_record(product(), "f.gz", seen)

    assert candidate is None
    assert reason == REJECT_DUPLICATE


def test_evaluate_record_does_not_mutate_the_seen_set():
    seen: set[str] = set()

    evaluate_record(product(), "f.gz", seen)

    assert seen == set()


def test_reject_order_puts_the_parent_identifier_first():
    """A record failing several rules reports the earliest rule, for clean counts."""
    broken = product(parent_asin=None, title="x", images=[])

    _, reason = evaluate_record(broken, "f.gz", set())

    assert reason == REJECT_NO_PARENT


def test_columnar_and_per_image_records_produce_the_same_candidate():
    per_image = product(parent_asin="SAME")
    columnar = product(
        parent_asin="SAME",
        images={
            "hi_res": [IMAGE_HI],
            "large": [IMAGE_LARGE],
            "thumb": [IMAGE_THUMB],
            "variant": ["MAIN"],
        },
    )

    left, _ = evaluate_record(per_image, "f.gz", set())
    right, _ = evaluate_record(columnar, "f.gz", set())

    assert left is not None
    assert right is not None
    assert left.images == right.images
    assert left.score == right.score
    assert left.subcategory == right.subcategory


def test_details_as_string_survives_into_the_candidate():
    candidate, _ = evaluate_record(
        product(details='{"Brand": "Acme", "Model": "X9"}'), "f.gz", set()
    )

    assert candidate is not None
    assert candidate.details == {"Brand": "Acme", "Model": "X9"}


def test_candidate_retains_the_complete_original_record():
    """Requirement 1.14."""
    record = product(extra_source_field={"kept": True})

    candidate, _ = evaluate_record(record, "f.gz", set())

    assert candidate is not None
    assert candidate.raw == record
    assert candidate.raw["extra_source_field"] == {"kept": True}
