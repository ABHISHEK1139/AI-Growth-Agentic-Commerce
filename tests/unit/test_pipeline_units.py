"""Stage 2 and 3 pure helpers: unit normalization, identity, image resolution.

Every function here is total and side-effect free, so these tests are the cheapest
place to pin down the behaviour the stages depend on. The unit conversions get
real dataset strings, not tidy ones, because the dataset is not tidy.
"""

from __future__ import annotations

import random
import re

import pytest

from pipeline.build_catalog import (
    CATALOG_TARGET_TOTAL,
    PRODUCT_ID_HEX_LENGTH,
    PRODUCT_ID_PREFIX,
    SOURCE_PRICE_NOTE,
    SUBCATEGORIES,
    SUBCATEGORY_QUOTAS,
    UNCATEGORIZED,
    build_product,
    build_specifications,
    derive_product_id,
    image_manifest_rows,
    image_storage_key,
    parse_boolean,
    parse_currency_minor,
    parse_data_size_gb,
    parse_delivery_days,
    parse_dimensions_mm,
    parse_length_mm,
    parse_memory_gb,
    parse_storage_gb,
    parse_weight_grams,
    raw_metadata_relative_path,
    resolve_product_images,
    source_price_reference,
)
from tests.unit.test_pipeline_normalization import IMAGE_HI, IMAGE_LARGE, IMAGE_THUMB

PRODUCT_ID_RE = re.compile(rf"^{re.escape(PRODUCT_ID_PREFIX)}[0-9a-f]{{{PRODUCT_ID_HEX_LENGTH}}}$")


# ---------------------------------------------------------------------------
# Quota table (design.md "Subcategory quotas", D-1, D-2)
# ---------------------------------------------------------------------------


def test_quota_table_matches_the_design_exactly():
    assert SUBCATEGORY_QUOTAS == {
        "laptop": 3_000,
        "smartphone": 2_500,
        "monitor": 2_000,
        "audio": 2_500,
        "computer_accessory": 2_500,
        "phone_accessory": 2_000,
        "camera": 1_500,
        "home_electronics": 2_000,
        "appliance": 1_500,
        UNCATEGORIZED: 500,
    }
    assert CATALOG_TARGET_TOTAL == 20_000


def test_quota_table_covers_every_subcategory_and_nothing_else():
    """D-2: the removed bucket must not reappear through a stale quota entry."""
    assert set(SUBCATEGORY_QUOTAS) == set(SUBCATEGORIES)
    assert "kitchen_appliance" not in SUBCATEGORY_QUOTAS


# ---------------------------------------------------------------------------
# Currency to minor units (Requirement 2.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("$1,299.99", 129_999),
        ("1299.99", 129_999),
        ("$0.99", 99),
        (899.99, 89_999),  # the float trap: 899.99 * 100 == 89998.999...
        (1299, 129_900),
        ("  $49.00  ", 4_900),
        ("USD 12.5", 1_250),
    ],
)
def test_currency_converts_to_exact_minor_units(value, expected):
    assert parse_currency_minor(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "free", "n/a", 0, 0.0, -5, "-1.00", True])
def test_currency_returns_none_rather_than_guessing(value):
    """A zero or unparseable price means "no price given", never "free"."""
    assert parse_currency_minor(value) is None


def test_currency_never_loses_a_paisa_across_the_whole_cent_range():
    """Property-style: every two-decimal amount round-trips to exact minor units."""
    for cents in range(0, 200_000, 37):
        major = cents // 100
        remainder = cents % 100
        assert parse_currency_minor(f"{major}.{remainder:02d}") == (cents or None)


def test_inr_is_supported_because_stage_four_prices_in_paise():
    assert parse_currency_minor("2499.50", "INR") == 249_950


def test_unknown_currency_is_refused_not_assumed():
    with pytest.raises(ValueError, match="unknown currency"):
        parse_currency_minor("10.00", "XYZ")


# ---------------------------------------------------------------------------
# Memory and storage to GB (Requirement 2.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("16 GB", 16.0),
        ("16GB", 16.0),
        ("16 Gb RAM", 16.0),
        ("512 MB", 0.5),
        ("1 TB", 1024.0),
        ("2 TB", 2048.0),
        ("1024 KB", 0.001),
        ("16", 16.0),  # a bare number in a RAM detail means GB
        (32, 32.0),
    ],
)
def test_data_sizes_convert_to_gb(value, expected):
    assert parse_data_size_gb(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, "", "plenty", 0, -4, True, {"gb": 16}])
def test_unparseable_data_size_is_omitted(value):
    assert parse_data_size_gb(value) is None


def test_memory_and_storage_share_one_converter():
    """Requirement 2.7 names them separately; they share a unit, so also a parser."""
    assert parse_memory_gb is parse_data_size_gb
    assert parse_storage_gb is parse_data_size_gb


# ---------------------------------------------------------------------------
# Weight to grams (Requirement 2.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4 lbs", 1_814),
        ("4 pounds", 1_814),
        ("2.5 kg", 2_500),
        ("500 g", 500),
        ("16 ounces", 454),
        ("1.5 oz", 43),
        ("250 mg", 0),
        ("12.8 x 8.9 x 0.6 inches; 3.5 pounds", 1_588),
    ],
)
def test_weights_convert_to_whole_grams(value, expected):
    assert parse_weight_grams(value) == expected


@pytest.mark.parametrize("value", ["16 GB", "1 TB", "13.3 inches", "", None, "heavy", True])
def test_a_capacity_is_never_read_as_a_weight(value):
    """The trap: an unbounded ``g`` would turn "16 GB" into 16 grams."""
    assert parse_weight_grams(value) is None


# ---------------------------------------------------------------------------
# Dimensions to millimetres (Requirement 2.7)
# ---------------------------------------------------------------------------


def test_length_converts_to_millimetres():
    assert parse_length_mm("13.3 inches") == 337.8
    assert parse_length_mm("30 cm") == 300.0
    assert parse_length_mm("1 m") == 1000.0
    assert parse_length_mm("2 ft") == 609.6
    assert parse_length_mm("nope") is None


def test_dimension_triple_converts_every_term():
    assert parse_dimensions_mm("12.8 x 8.9 x 0.6 inches") == {
        "length_mm": 325.1,
        "width_mm": 226.1,
        "height_mm": 15.2,
    }


def test_dimension_unit_on_the_last_term_applies_to_all_of_them():
    assert parse_dimensions_mm("10 x 5 x 2 cm") == {
        "length_mm": 100.0,
        "width_mm": 50.0,
        "height_mm": 20.0,
    }


def test_dimension_unit_per_term_is_honoured_individually():
    assert parse_dimensions_mm("10 cm x 5 cm") == {"length_mm": 100.0, "width_mm": 50.0}


def test_dimensions_ignore_a_trailing_weight():
    assert parse_dimensions_mm("12.8 x 8.9 x 0.6 inches; 3.5 pounds") == {
        "length_mm": 325.1,
        "width_mm": 226.1,
        "height_mm": 15.2,
    }


@pytest.mark.parametrize("value", ["12 x 8 x 2", "", None, "large", 42, "x x x inches"])
def test_dimensions_with_no_usable_unit_are_omitted(value):
    """No unit means unknown scale. A guessed scale is worse than a missing field."""
    assert parse_dimensions_mm(value) is None


# ---------------------------------------------------------------------------
# Delivery to integer days (Requirement 2.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2 days", 2),
        ("3-5 business days", 5),  # slowest bound, not the flattering one
        ("2 to 4 days", 4),
        ("24 hours", 1),
        ("36 hours", 2),  # rounds up
        ("1 week", 7),
        ("2 weeks", 14),
        ("1 month", 30),
        ("same day", 0),
        ("Same-Day Delivery", 0),
        ("overnight", 1),
        ("next day", 1),
        (3, 3),
        (2.2, 3),
    ],
)
def test_delivery_windows_become_whole_days(value, expected):
    assert parse_delivery_days(value) == expected


@pytest.mark.parametrize("value", [None, "", "soon", "free shipping", -1, True])
def test_unparseable_delivery_is_omitted(value):
    assert parse_delivery_days(value) is None


# ---------------------------------------------------------------------------
# Booleans (Requirement 2.7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("Yes", True),
        ("no", False),
        ("TRUE", True),
        ("false", False),
        (1, True),
        (0, False),
        ("1", True),
        ("0", False),
        ("Included", True),
        ("Not Included", False),
    ],
)
def test_booleans_normalize_to_true_or_false(value, expected):
    assert parse_boolean(value) is expected


@pytest.mark.parametrize("value", ["maybe", "", None, 7, "sometimes", [], 2.5])
def test_a_non_boolean_stays_none_rather_than_becoming_false(value):
    """Silently reporting an unparsed value as ``false`` would be a fabricated fact."""
    assert parse_boolean(value) is None


# ---------------------------------------------------------------------------
# Specifications
# ---------------------------------------------------------------------------


def test_specifications_normalize_every_unit_from_a_realistic_details_map():
    specs = build_specifications(
        {
            "Brand": "Acme",
            "Item model number": "ACM-15-X",
            "Color": "Space Grey",
            "RAM": "16 GB",
            "Hard Drive": "512 GB",
            "Item Weight": "4 lbs",
            "Product Dimensions": "12.8 x 8.9 x 0.6 inches",
            "Shipping": "3-5 business days",
            "Is Discontinued By Manufacturer": "No",
        }
    )

    assert specs == {
        "brand": "Acme",
        "model_number": "ACM-15-X",
        "color": "Space Grey",
        "memory_gb": 16.0,
        "storage_gb": 512.0,
        "weight_grams": 1_814,
        "dimensions_mm": {"length_mm": 325.1, "width_mm": 226.1, "height_mm": 15.2},
        "delivery_days": 5,
        "flags": {"is_discontinued_by_manufacturer": False},
    }


def test_capacities_fall_back_to_the_feature_bullets():
    """Details are empty on much of this dataset; the capacity sits in a feature."""
    specs = build_specifications({}, ["16 GB DDR4 RAM", "512 GB PCIe SSD", "Backlit keyboard"])

    assert specs["memory_gb"] == 16.0
    assert specs["storage_gb"] == 512.0


def test_an_unqualified_capacity_in_a_feature_is_not_guessed_at():
    """ "512 GB" alone could be either memory or storage. Neither is claimed."""
    specs = build_specifications({}, ["512 GB", "Aluminium chassis"])

    assert "memory_gb" not in specs
    assert "storage_gb" not in specs


def test_weight_falls_back_to_the_dimension_string_tail():
    specs = build_specifications({"Product Dimensions": "12.8 x 8.9 x 0.6 inches; 3.5 pounds"})

    assert specs["weight_grams"] == 1_588
    assert specs["dimensions_mm"]["length_mm"] == 325.1


def test_unparseable_specification_fields_are_absent_never_null():
    specs = build_specifications({"Brand": "  ", "RAM": "lots", "Item Weight": "heavy"})

    assert specs == {}
    assert None not in specs.values()


def test_only_boolean_looking_details_become_flags():
    specs = build_specifications({"Batteries Required": "No", "Brand": "Acme", "RAM": "8 GB"})

    assert specs["flags"] == {"batteries_required": False}


# ---------------------------------------------------------------------------
# Product identity (Requirement 2.4, 2.5, Property 24)
# ---------------------------------------------------------------------------


def test_product_id_is_a_stable_function_of_the_parent_identifier():
    """Property 24: same source identifier, same product identifier, always."""
    first = derive_product_id("B000000001")
    second = derive_product_id("B000000001")

    assert first == second
    assert PRODUCT_ID_RE.match(first), first


def test_product_id_is_pinned_to_a_known_vector():
    """A refactor that changes the derivation must fail here, loudly.

    Identifiers already written to a catalog and a merchant database cannot be
    renamed silently, so the derivation is a compatibility surface.
    """
    assert derive_product_id("B000000001") == "prod_4f5cd356afb9de2ae99ffa1f"


def test_distinct_parent_identifiers_do_not_collide():
    """Property 24, the injective half: 5,000 identifiers, 5,000 distinct results."""
    rng = random.Random(20250607)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    asins = {"B" + "".join(rng.choices(alphabet, k=9)) for _ in range(5_000)}

    derived = {asin: derive_product_id(asin) for asin in asins}

    assert len(set(derived.values())) == len(asins)
    assert all(PRODUCT_ID_RE.match(value) for value in derived.values())
    # Determinism again, over the same broad sample rather than one example.
    assert derived == {asin: derive_product_id(asin) for asin in asins}


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_parent_identifier_is_refused(value):
    with pytest.raises(ValueError, match="parent_asin is required"):
        derive_product_id(value)


def test_provenance_path_is_derived_from_the_product_id():
    assert raw_metadata_relative_path("prod_abc") == "raw_metadata/prod_abc.json"


# ---------------------------------------------------------------------------
# Source price (Requirement 2.8)
# ---------------------------------------------------------------------------


def test_source_price_is_carried_as_the_recorded_dataset_price():
    reference = source_price_reference(899.99)

    assert reference == {
        "amount_minor": 89_999,
        "currency": "USD",
        "is_authoritative": True,
        "note": SOURCE_PRICE_NOTE,
    }
    assert "historical dataset metadata" in SOURCE_PRICE_NOTE


def test_a_missing_source_price_yields_no_reference_block():
    assert source_price_reference(None) is None
    assert source_price_reference(0) is None


# ---------------------------------------------------------------------------
# Image resolution (Requirement 2.9, 2.10)
# ---------------------------------------------------------------------------


def test_best_resolution_is_chosen_for_each_of_the_three_cases():
    """Requirement 2.9: hi_res, then large, then thumb."""
    resolved = resolve_product_images(
        [
            {"hi_res": IMAGE_HI, "large": IMAGE_LARGE, "thumb": IMAGE_THUMB, "variant": "MAIN"},
            {
                "hi_res": None,
                "large": "https://x.test/l.jpg",
                "thumb": "https://x.test/t.jpg",
                "variant": "PT01",
            },
            {
                "hi_res": None,
                "large": None,
                "thumb": "https://x.test/only-thumb.jpg",
                "variant": "PT02",
            },
        ]
    )

    assert [(image["resolution"], image["url"]) for image in resolved] == [
        ("hi_res", IMAGE_HI),
        ("large", "https://x.test/l.jpg"),
        ("thumb", "https://x.test/only-thumb.jpg"),
    ]
    assert [image["position"] for image in resolved] == [0, 1, 2]


def test_repeated_urls_are_deduplicated_within_a_product():
    resolved = resolve_product_images(
        [
            {"hi_res": IMAGE_HI, "large": None, "thumb": None, "variant": "MAIN"},
            {"hi_res": IMAGE_HI, "large": None, "thumb": None, "variant": "PT01"},
            {"hi_res": None, "large": IMAGE_LARGE, "thumb": None, "variant": "PT02"},
        ]
    )

    assert [image["url"] for image in resolved] == [IMAGE_HI, IMAGE_LARGE]


def test_entries_with_no_usable_url_are_dropped_and_positions_stay_dense():
    resolved = resolve_product_images(
        [
            {"hi_res": "", "large": None, "thumb": None, "variant": "PT01"},
            {"hi_res": "ftp://x.test/nope.jpg", "large": None, "thumb": None, "variant": "PT02"},
            "not a mapping",
            {"hi_res": IMAGE_HI, "large": None, "thumb": None, "variant": "MAIN"},
        ]
    )

    assert [(image["position"], image["variant"]) for image in resolved] == [(0, "MAIN")]


def test_no_images_yields_an_empty_list():
    assert resolve_product_images([]) == []


# ---------------------------------------------------------------------------
# Storage keys (Requirement 2.10)
# ---------------------------------------------------------------------------


def test_storage_key_is_product_id_slash_variant_dot_jpg():
    assert image_storage_key("prod_abc", "MAIN", 0) == "prod_abc/main.jpg"


def test_a_missing_variant_falls_back_to_the_position():
    assert image_storage_key("prod_abc", None, 3) == "prod_abc/image_03.jpg"
    assert image_storage_key("prod_abc", "  ", 1) == "prod_abc/image_01.jpg"


def test_a_repeated_variant_never_produces_the_same_key_twice():
    """The source reuses ``MAIN``; a collision would overwrite the first image."""
    used: set[str] = set()

    keys = [image_storage_key("prod_abc", "MAIN", position, used) for position in range(3)]

    assert keys == ["prod_abc/main.jpg", "prod_abc/main_01.jpg", "prod_abc/main_02.jpg"]
    assert len(set(keys)) == 3


def test_manifest_rows_dedupe_urls_and_keys_together():
    rows = image_manifest_rows(
        {
            "product_id": "prod_abc",
            "external_product_id": "B1",
            "images": [
                {"url": IMAGE_HI, "variant": "MAIN", "resolution": "hi_res"},
                {"url": IMAGE_HI, "variant": "MAIN", "resolution": "hi_res"},  # duplicate URL
                {"url": IMAGE_LARGE, "variant": "MAIN", "resolution": "large"},  # duplicate variant
                {"url": "", "variant": "PT09", "resolution": "large"},  # unusable
            ],
        }
    )

    assert [row["storage_key"] for row in rows] == ["prod_abc/main.jpg", "prod_abc/main_01.jpg"]
    assert [row["source_url"] for row in rows] == [IMAGE_HI, IMAGE_LARGE]
    assert all(row["downloaded"] is False for row in rows)
    assert [row["position"] for row in rows] == [0, 1]


def test_manifest_rows_for_a_product_with_no_usable_image_are_empty():
    assert image_manifest_rows({"product_id": "prod_abc", "images": []}) == []
    assert image_manifest_rows({"product_id": "prod_abc"}) == []


# ---------------------------------------------------------------------------
# build_product
# ---------------------------------------------------------------------------


def _row(**overrides):
    row = {
        "parent_asin": "B000000001",
        "source_file": "meta_Electronics.jsonl.gz",
        "main_category": "All Electronics",
        "subcategory": "laptop",
        "score": 87,
        "status": "valid",
        "title": "Acme 15 inch Laptop Computer Model X",
        "store": "Acme",
        "average_rating": 4.5,
        "rating_number": 120,
        "price_usd": 899.99,
        "features_json": '["16 GB DDR4 RAM","512 GB SSD"]',
        "description_json": '["A laptop for testing."]',
        "images_json": (
            '[{"hi_res":"' + IMAGE_HI + '","large":"' + IMAGE_LARGE + '","thumb":null,'
            '"variant":"MAIN"}]'
        ),
        "details_json": '{"Brand":"Acme","Item Weight":"4 lbs"}',
        "categories_json": '["Electronics","Computers"]',
        "raw_json": '{"parent_asin":"B000000001"}',
    }
    row.update(overrides)
    return row


def test_product_object_carries_identity_category_and_provenance():
    product = build_product(_row())

    assert product["product_id"] == derive_product_id("B000000001")
    assert product["external_product_id"] == "B000000001"  # Requirement 2.5
    assert product["category_id"] == "laptop"
    assert product["completeness_score"] == 87
    assert product["provenance"] == {
        "parent_asin": "B000000001",
        "source_file": "meta_Electronics.jsonl.gz",
        "source_categories": ["Electronics", "Computers"],
        "raw_metadata_path": "raw_metadata/" + derive_product_id("B000000001") + ".json",
    }


def test_product_object_normalizes_units_and_marks_the_source_price():
    product = build_product(_row())

    assert product["specifications"]["memory_gb"] == 16.0
    assert product["specifications"]["storage_gb"] == 512.0
    assert product["specifications"]["weight_grams"] == 1_814
    assert product["source_price"]["amount_minor"] == 89_999
    assert product["source_price"]["is_authoritative"] is True


def test_product_object_is_key_order_stable_so_output_is_byte_identical():
    assert list(build_product(_row())) == list(build_product(_row(parent_asin="B2")))


def test_product_object_tolerates_empty_json_columns():
    product = build_product(
        _row(
            features_json="",
            description_json="null",
            images_json="{}",
            details_json="[]",
            categories_json="",
            price_usd=None,
            average_rating=None,
            rating_number=None,
        )
    )

    assert product["normalized_features"] == []
    assert product["description"] == []
    assert product["images"] == []
    assert product["specifications"] == {}
    assert product["source_price"] is None
    assert product["average_rating"] == 0.0
    assert product["rating_number"] == 0
