"""AgentPay catalog pipeline.

Six resumable stages behind one command. Stage boundaries are durable, so an
interrupted run resumes without repeating completed work (Requirement 1.16)::

    python -m pipeline.build_catalog products   # stage 1  (Task 6)
    python -m pipeline.build_catalog select     # stage 2  (Task 7)
    python -m pipeline.build_catalog images     # stage 3  (Task 7)
    python -m pipeline.build_catalog offers     # stage 4  (Task 8)
    python -m pipeline.build_catalog reviews    # stage 5  (Task 8)
    python -m pipeline.build_catalog report     # stage 6  (Task 8)
    python -m pipeline.build_catalog all        # every implemented stage in order

All six stages are implemented. Each stage writes durable artifacts so ``all``
runs everything in order and a partial run resumes from the last checkpoint.

Stage 1 -- candidate extraction
------------------------------
Streams each ``meta_*.jsonl.gz`` source in place, one line at a time, normalizes
the loader-dependent shapes into one internal representation, classifies a
subcategory, scores completeness from 0 to 100, applies four hard rejects, and
writes survivors to ``candidates.sqlite`` in batches of 2,000.

Two constraints shape the whole module:

* **The raw data is immutable.** Sources are opened with ``gzip.open(..., "rt")``
  and never decompressed to disk, never written to (Requirement 1.1, 1.2).
* **The raw data does not fit in memory.** The Electronics metadata file alone is
  on the order of a million records. Nothing accumulates except one insert batch
  and the cross-file dedupe set of parent identifiers (Requirement 1.13).

Stage 2 -- quota-based selection
-------------------------------
Reads ``candidates.sqlite`` only -- never the ``.gz`` sources again -- and runs
one ``ORDER BY score DESC, rating_number DESC LIMIT quota`` query per
subcategory. Writes ``catalog/products.jsonl`` plus one verbatim
``catalog/raw_metadata/{product_id}.json`` per selected product.

The quota table is a set of **caps, not targets**. A subcategory the source
cannot fill is reported as a shortfall and left short; it is never padded from
another bucket (Requirement 2.2, 2.3). Those numbers reach a merchant dashboard,
so an inflated one would be a lie told by the pipeline.

Stage 3 -- image manifest
------------------------
Reads ``products.jsonl`` only, resolves each image to its best available
resolution, deduplicates URLs within a product, and writes
``catalog/images_manifest.jsonl``. It **downloads nothing** (Requirement 2.11):
the point of locking the selection first is to avoid spending bandwidth on
products that were never going to be in the catalog.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pipeline.config import PipelineConfig, load_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Rows per SQLite transaction. Requirement 1.13 fixes this at 2,000, and the
#: writer never holds more than one batch.
BATCH_SIZE = 2_000

#: Title bounds. Below 8 characters a title carries no product identity; above
#: 300 it is a keyword-stuffed listing rather than a name (Requirement 1.5).
TITLE_MIN_LENGTH = 8
TITLE_MAX_LENGTH = 300

#: Stage 1 reads metadata only. The plain-named files are reviews (stage 5).
META_SOURCES: tuple[str, ...] = (
    "meta_Electronics.jsonl.gz",
    "meta_Cell_Phones_and_Accessories.jsonl.gz",
    "meta_Appliances.jsonl.gz",
    "meta_Home_and_Kitchen.jsonl.gz",
)

#: The fixed subcategory set (design.md "Subcategory quotas", D-1, D-2).
UNCATEGORIZED = "uncategorized_review"
SUBCATEGORIES: tuple[str, ...] = (
    "laptop",
    "smartphone",
    "monitor",
    "audio",
    "camera",
    "computer_accessory",
    "phone_accessory",
    "home_electronics",
    "appliance",
    UNCATEGORIZED,
)

#: Buckets that name a device rather than an add-on for one. Only these can be
#: assigned from a record's features; see :func:`classify_subcategory`.
DEVICE_SUBCATEGORIES: tuple[str, ...] = tuple(
    name for name in SUBCATEGORIES if name != UNCATEGORIZED and not name.endswith("_accessory")
)

#: Reject reasons. Stable strings: they are counted and printed, and stage 6
#: reports them.
REJECT_NO_PARENT = "missing_parent_asin"
REJECT_TITLE = "title_length"
REJECT_NO_IMAGE = "no_usable_image"
REJECT_DUPLICATE = "duplicate_parent_asin"
REJECT_REASONS: tuple[str, ...] = (
    REJECT_NO_PARENT,
    REJECT_TITLE,
    REJECT_NO_IMAGE,
    REJECT_DUPLICATE,
)

#: Resolution preference for a single image entry (Requirement 2.9).
IMAGE_RESOLUTION_ORDER: tuple[str, ...] = ("hi_res", "large", "thumb")
IMAGE_KEYS: tuple[str, ...] = ("hi_res", "large", "thumb", "variant")

#: Per-subcategory selection caps (design.md "Subcategory quotas", D-1, D-2).
#:
#: These are **caps, not targets**. ``kitchen_appliance`` is absent because its
#: source file is absent, and its share was redistributed rather than left to
#: under-fill silently. A bucket the source cannot fill stays short and is
#: reported (Requirement 2.2, 2.3).
SUBCATEGORY_QUOTAS: dict[str, int] = {
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

#: The configured catalog size. Stage 6 reports this alongside what was actually
#: achieved, so the two are never conflated (Requirement 5.x, 2.3).
CATALOG_TARGET_TOTAL = sum(SUBCATEGORY_QUOTAS.values())

if set(SUBCATEGORY_QUOTAS) != set(SUBCATEGORIES):  # pragma: no cover - import-time guard
    raise RuntimeError("SUBCATEGORY_QUOTAS and SUBCATEGORIES must cover the same labels")

STAGE_PRODUCTS = "products"
STAGE_SELECT = "select"
STAGE_IMAGES = "images"
STAGE_OFFERS = "offers"
STAGE_REVIEWS = "reviews"
STAGE_REPORT = "report"
STAGE_ORDER: tuple[str, ...] = (
    STAGE_PRODUCTS,
    STAGE_SELECT,
    STAGE_IMAGES,
    STAGE_OFFERS,
    STAGE_REVIEWS,
    STAGE_REPORT,
)

# Generated amounts are deliberately in INR paise, not inherited from the
# source data's USD reference price. The bands are a merchant demo fixture and
# every generated offer declares that fact through ``pricing_source``.
OFFER_PRICE_BANDS_MINOR: dict[str, tuple[int, int]] = {
    "laptop": (39_999_00, 119_999_00),
    "smartphone": (14_999_00, 89_999_00),
    "monitor": (12_999_00, 54_999_00),
    "audio": (1_499_00, 34_999_00),
    "camera": (24_999_00, 149_999_00),
    "computer_accessory": (499_00, 19_999_00),
    "phone_accessory": (299_00, 9_999_00),
    "home_electronics": (1_999_00, 59_999_00),
    "appliance": (7_999_00, 99_999_00),
    UNCATEGORIZED: (999_00, 24_999_00),
}
REVIEW_SOURCES: tuple[str, ...] = (
    "Electronics.jsonl.gz",
    "Cell_Phones_and_Accessories.jsonl.gz",
    "Appliances.jsonl.gz",
    "Home_and_Kitchen.jsonl.gz",
)
DEMO_MERCHANT_ID = "merchant_demo"

NormalizedImage = dict[str, str | None]
JsonDict = dict[str, Any]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@dataclass
class ScanStats:
    """Line-level counters for one source file.

    Separate from record-level outcomes because a malformed line never becomes a
    record, and conflating the two hides how dirty a source actually is.
    """

    lines_read: int = 0
    malformed: int = 0


def iter_jsonl_gz(
    path: Path,
    max_lines: int | None = None,
    stats: ScanStats | None = None,
) -> Iterator[JsonDict]:
    """Yield one parsed JSON object per line from a gzipped JSONL file.

    The file is read as gzip in text mode, one line at a time, and is never
    decompressed to disk (Requirement 1.1). A line that is not valid JSON, or
    that decodes to something other than an object, is skipped and the run
    continues (Requirement 1.3) -- a single corrupt line in a million-line
    source must not cost the whole pass.

    ``max_lines`` caps how many lines are *read*, malformed ones included, so a
    capped run is bounded by input size rather than by yield count
    (Requirement 1.15). ``stats`` is an optional out-parameter for callers that
    want the line-level counters.
    """
    counters = stats if stats is not None else ScanStats()

    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        lines: Iterable[str] = handle if max_lines is None else itertools.islice(handle, max_lines)
        for raw_line in lines:
            counters.lines_read += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                counters.malformed += 1
                continue
            if not isinstance(record, dict):
                counters.malformed += 1
                continue
            yield record


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def is_usable_url(value: object) -> bool:
    """Whether a value is a URL worth keeping.

    Deliberately strict about the scheme: the dataset carries empty strings,
    nulls, and the occasional fragment, and a half-URL that fails at download
    time is worse than a record rejected here.
    """
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def normalize_images(raw: object) -> list[NormalizedImage]:
    """Normalize either image shape to one internal list (Requirement 1.8).

    Two loaders produce two shapes for the same data:

    * the native JSONL shape, a list of ``{thumb, large, hi_res, variant}``
      objects;
    * the Hugging Face columnar shape, one object holding parallel lists under
      ``hi_res`` / ``large`` / ``thumb`` / ``variant``.

    Both collapse to the same list of per-image dicts. Entries with no usable URL
    in any resolution are dropped here, which is what makes the "no usable image"
    hard reject a simple emptiness check downstream.
    """
    if isinstance(raw, Mapping):
        entries = _columnar_images(raw)
    elif isinstance(raw, list):
        entries = [_image_entry(item) for item in raw]
    else:
        return []

    return [
        entry
        for entry in entries
        if any(is_usable_url(entry[key]) for key in ("hi_res", "large", "thumb"))
    ]


def _columnar_images(raw: Mapping[str, Any]) -> list[NormalizedImage]:
    """Transpose parallel columnar lists into per-image dicts."""
    columns: dict[str, list[Any]] = {}
    for key in IMAGE_KEYS:
        value = raw.get(key)
        if isinstance(value, list):
            columns[key] = value
        elif value is None:
            columns[key] = []
        else:
            # A scalar under a columnar key describes a single image.
            columns[key] = [value]

    height = max((len(column) for column in columns.values()), default=0)
    return [
        _image_entry({key: _at(columns[key], index) for key in IMAGE_KEYS})
        for index in range(height)
    ]


def _at(column: Sequence[Any], index: int) -> Any:
    """Index a possibly-short parallel column without raising."""
    return column[index] if index < len(column) else None


def _image_entry(item: object) -> NormalizedImage:
    if isinstance(item, Mapping):
        return {key: _as_optional_str(item.get(key)) for key in IMAGE_KEYS}
    if isinstance(item, str):
        # Some exports flatten an image to a bare URL string.
        return {"hi_res": None, "large": _as_optional_str(item), "thumb": None, "variant": None}
    return dict.fromkeys(IMAGE_KEYS)


def best_image_url(image: Mapping[str, Any]) -> str | None:
    """Highest available resolution for one image entry (Requirement 2.9)."""
    for key in IMAGE_RESOLUTION_ORDER:
        value = image.get(key)
        if is_usable_url(value):
            return str(value).strip()
    return None


def normalize_details(raw: object) -> JsonDict:
    """Normalize ``details``, which arrives as an object or as JSON text.

    A parse failure yields an empty object rather than raising: a malformed
    details blob is a degraded record, not a reason to abandon the run
    (Requirement 1.9).
    """
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {}
        if isinstance(parsed, Mapping):
            return {str(key): value for key, value in parsed.items()}
        return {}
    return {}


def normalize_text_list(raw: object) -> list[str]:
    """Normalize ``features`` / ``description`` to a list of non-empty strings.

    The source gives a list, a bare string, or nothing at all; all three become a
    list (Requirement 1.10).
    """
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            text = item.strip() if isinstance(item, str) else str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else str(value).strip()
    return text or None


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip().replace(",", "")))
        except ValueError:
            return default
    return default


def parse_price_usd(value: object) -> float | None:
    """Parse the dataset's own USD price.

    Carried through as reference metadata only. It is explicitly not
    authoritative: AgentPay prices are generated in stage 4 (Requirement 2.8,
    D-5).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        text = value.strip().lstrip("$").replace(",", "")
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


# ---------------------------------------------------------------------------
# Subcategory classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubcategoryRule:
    """One keyword rule.

    A record matches when it contains any ``keywords``, also contains any
    ``also_requires`` (when that set is non-empty), and contains none of
    ``excludes``. The ``also_requires`` form is what separates a device from an
    accessory for the same device: ``iphone`` plus ``case`` is a phone accessory,
    ``iphone`` alone is a phone.
    """

    subcategory: str
    keywords: tuple[str, ...]
    also_requires: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


#: Words that turn a device mention into an accessory for that device.
ACCESSORY_TOKENS: tuple[str, ...] = (
    "case",
    "cases",
    "cover",
    "sleeve",
    "pouch",
    "bag",
    "backpack",
    "holster",
    "skin",
    "decal",
    "protector",
    "tempered glass",
    "stand",
    "mount",
    "holder",
    "cradle",
    "dock",
    "docking station",
    "charger",
    "charging cable",
    "cable",
    "adapter",
    "power bank",
    "battery pack",
    "strap",
    "lanyard",
    "grip",
    "stylus",
    "cleaning kit",
    "replacement",
    "selfie stick",
    "arm",
)

PHONE_TOKENS: tuple[str, ...] = (
    "phone",
    "phones",
    "iphone",
    "smartphone",
    "cellphone",
    "cell phone",
    "galaxy",
    "android",
    "pixel",
)

COMPUTER_TOKENS: tuple[str, ...] = (
    "laptop",
    "laptops",
    "notebook",
    "computer",
    "pc",
    "macbook",
    "imac",
    "chromebook",
    "desktop",
    "tablet",
    "ipad",
    "monitor",
    "keyboard",
    "printer",
    "hard drive",
)

#: Ordered, first match wins. Accessory rules come first, because "laptop bag"
#: is an accessory that happens to name a laptop, and the reverse mistake fills
#: the laptop bucket with luggage.
CLASSIFIER_RULES: tuple[SubcategoryRule, ...] = (
    SubcategoryRule(
        "phone_accessory",
        (
            "screen protector",
            "phone case",
            "phone cover",
            "phone grip",
            "popsocket",
            "otterbox",
            "lifeproof",
            "car phone mount",
            "phone holder",
            "sim card tray",
            "sim ejector",
        ),
    ),
    SubcategoryRule("phone_accessory", PHONE_TOKENS, also_requires=ACCESSORY_TOKENS),
    SubcategoryRule(
        "computer_accessory",
        (
            "mouse",
            "mice",
            "keyboard",
            "mouse pad",
            "mousepad",
            "usb hub",
            "docking station",
            "port replicator",
            "webcam",
            "hard drive",
            "hard disk",
            "ssd",
            "solid state drive",
            "flash drive",
            "thumb drive",
            "memory card",
            "sd card",
            "microsd",
            "printer",
            "toner",
            "ink cartridge",
            "scanner",
            "router",
            "modem",
            "ethernet cable",
            "hdmi cable",
            "usb cable",
            "displayport",
            "vga",
            "dvi",
            "graphics card",
            "video card",
            "motherboard",
            "cpu",
            "processor",
            "ram",
            "memory module",
            "power supply",
            "surge protector",
            "kvm",
            "cooling pad",
            "laptop bag",
            "laptop sleeve",
            "laptop stand",
            "ac adapter",
            "power adapter",
            "usb adapter",
            "wireless adapter",
            "bluetooth adapter",
        ),
    ),
    SubcategoryRule("computer_accessory", COMPUTER_TOKENS, also_requires=ACCESSORY_TOKENS),
    SubcategoryRule(
        "laptop",
        (
            "laptop",
            "laptops",
            "macbook",
            "chromebook",
            "ultrabook",
            "notebook computer",
            "notebook pc",
            "gaming laptop",
        ),
    ),
    SubcategoryRule(
        "smartphone",
        (
            "smartphone",
            "cell phone",
            "cellphone",
            "mobile phone",
            "iphone",
            "galaxy s",
            "galaxy note",
            "unlocked phone",
            "android phone",
            "flip phone",
            "phone",
        ),
        excludes=(
            "cordless",
            "landline",
            "voip",
            "phone system",
            "speakerphone",
            "phone line",
        ),
    ),
    SubcategoryRule(
        "monitor",
        ("monitor", "monitors", "computer display", "display panel"),
        excludes=(
            "baby monitor",
            "blood pressure",
            "heart rate",
            "glucose",
            "fetal",
            "pet monitor",
            "air quality",
            "temperature monitor",
            "video monitor",
        ),
    ),
    SubcategoryRule(
        "camera",
        (
            "camera",
            "cameras",
            "dslr",
            "camcorder",
            "gopro",
            "mirrorless",
            "camera lens",
            "telephoto",
            "zoom lens",
            "tripod",
            "speedlight",
        ),
        excludes=(
            "security camera",
            "surveillance camera",
            "dash camera",
            "dash cam",
            "backup camera",
            "webcam",
            "ip camera",
            "baby monitor",
        ),
    ),
    SubcategoryRule(
        "audio",
        (
            "headphone",
            "headphones",
            "earphone",
            "earphones",
            "earbud",
            "earbuds",
            "headset",
            "speaker",
            "speakers",
            "soundbar",
            "sound bar",
            "subwoofer",
            "amplifier",
            "stereo receiver",
            "av receiver",
            "microphone",
            "turntable",
            "record player",
            "cd player",
            "mp3 player",
            "ipod",
            "boombox",
            "karaoke",
            "audio interface",
            "preamp",
            "audio mixer",
            "dj mixer",
            "equalizer",
            "in-ear",
            "over-ear",
        ),
    ),
    SubcategoryRule(
        "appliance",
        (
            "refrigerator",
            "fridge",
            "freezer",
            "washer",
            "washing machine",
            "dryer",
            "dishwasher",
            "air conditioner",
            "air conditioning",
            "vacuum",
            "microwave",
            "oven",
            "range hood",
            "cooktop",
            "stove",
            "water heater",
            "furnace",
            "humidifier",
            "dehumidifier",
            "air purifier",
            "blender",
            "coffee maker",
            "espresso machine",
            "toaster",
            "food processor",
            "stand mixer",
            "ice maker",
            "garbage disposal",
            "water filter",
            "appliance",
            "appliances",
            "kettle",
            "slow cooker",
            "pressure cooker",
            "rice cooker",
            "juicer",
            "deep fryer",
            "trash compactor",
            "water dispenser",
            "wine cooler",
            "dryer vent",
        ),
    ),
    SubcategoryRule(
        "home_electronics",
        (
            "television",
            "televisions",
            "tv",
            "hdtv",
            "projector",
            "blu-ray",
            "dvd player",
            "vcr",
            "remote control",
            "universal remote",
            "roku",
            "chromecast",
            "streaming stick",
            "antenna",
            "satellite receiver",
            "cable box",
            "home theater",
            "smart home",
            "thermostat",
            "doorbell",
            "security camera",
            "surveillance",
            "baby monitor",
            "light bulb",
            "led strip",
            "smart plug",
            "alexa",
            "echo dot",
            "google home",
            "cordless phone",
            "answering machine",
            "intercom",
            "alarm clock",
            "clock radio",
            "weather station",
            "e-reader",
            "kindle",
            "tablet",
            "ipad",
            "smartwatch",
            "fitness tracker",
            "drone",
            "playstation",
            "xbox",
            "nintendo",
            "game console",
            "calculator",
            "label maker",
            "shredder",
        ),
    ),
)


def _compile_keywords(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    """Compile keywords into one word-boundary alternation.

    Word boundaries matter more than they look: a substring match would classify
    every product containing "tv" (as in "tvs", "atv") as a television, and every
    "headphone" as a phone.
    """
    if not keywords:
        return None
    alternation = "|".join(
        re.escape(keyword) for keyword in sorted(keywords, key=len, reverse=True)
    )
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


_COMPILED_RULES: tuple[
    tuple[str, re.Pattern[str] | None, re.Pattern[str] | None, re.Pattern[str] | None], ...
] = tuple(
    (
        rule.subcategory,
        _compile_keywords(rule.keywords),
        _compile_keywords(rule.also_requires),
        _compile_keywords(rule.excludes),
    )
    for rule in CLASSIFIER_RULES
)


def classify_subcategory(title: str, features: Sequence[str] = ()) -> str:
    """Assign exactly one subcategory label from the fixed set.

    The rules run over the title first, and only fall back to title plus features
    when the title places nothing. The order is not cosmetic: a laptop lists
    "16 GB RAM" among its features, and a single pass over the combined text
    classifies that laptop as a memory module. The title is what names the
    product; features describe it.

    A record the rules cannot place is labelled ``uncategorized_review`` rather
    than discarded, so the catalog stays honest about classifier coverage and
    merchant review has real records to act on (Requirement 1.11, D-2).
    """
    from_title = _first_matching_rule(title)
    if from_title is not None:
        return from_title

    # Fallback pass over the features, restricted to device buckets. An accessory
    # label has to be earned by the title: features list what a device contains
    # ("16 GB RAM", "USB-C cable included"), and letting those decide turns
    # laptops into memory modules and phones into charging cables.
    from_features = _first_matching_rule(" ".join([title, *features]), allowed=DEVICE_SUBCATEGORIES)
    return from_features if from_features is not None else UNCATEGORIZED


def _first_matching_rule(haystack: str, allowed: Sequence[str] | None = None) -> str | None:
    if not haystack.strip():
        return None
    for subcategory, keywords, also_requires, excludes in _COMPILED_RULES:
        if allowed is not None and subcategory not in allowed:
            continue
        if keywords is None or not keywords.search(haystack):
            continue
        if also_requires is not None and not also_requires.search(haystack):
            continue
        if excludes is not None and excludes.search(haystack):
            continue
        return subcategory
    return None


# ---------------------------------------------------------------------------
# Completeness scoring
# ---------------------------------------------------------------------------

#: Per-factor ceilings. Declared as data so the rubric is auditable and the
#: total is provably 100 (Requirement 1.12).
SCORE_WEIGHTS: dict[str, int] = {
    "title": 20,
    "features": 15,
    "description": 15,
    "images": 15,
    "details": 10,
    "rating_volume": 15,
    "rating_quality": 10,
}
MAX_SCORE = sum(SCORE_WEIGHTS.values())


def _tiered(value: float, tiers: Sequence[tuple[float, int]]) -> int:
    """Sum the points of every threshold the value reaches."""
    return sum(points for threshold, points in tiers if value >= threshold)


def completeness_score(
    *,
    title: str,
    features: Sequence[str],
    description: Sequence[str],
    images: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any],
    rating_number: int,
    average_rating: float,
) -> int:
    """Score record completeness from 0 to 100 over seven declared factors.

    Transparent and tiered rather than learned, so any number in the catalog
    health report can be explained from the record it came from. The factors are
    title quality, feature presence, description presence, usable image presence,
    detail richness, rating volume, and rating quality (Requirement 1.12).
    """
    title_length = len(title.strip())
    description_chars = sum(len(part) for part in description)
    usable_images = sum(1 for image in images if best_image_url(image) is not None)

    score = (
        _tiered(title_length, ((TITLE_MIN_LENGTH, 6), (25, 7), (60, 7)))
        + _tiered(len(features), ((1, 7), (3, 4), (5, 4)))
        + _tiered(description_chars, ((1, 8), (200, 7)))
        + _tiered(usable_images, ((1, 5), (3, 5), (5, 5)))
        + _tiered(len(details), ((1, 4), (5, 3), (10, 3)))
        + _tiered(rating_number, ((1, 5), (10, 4), (50, 3), (200, 3)))
        + _tiered(average_rating, ((3.0, 4), (4.0, 3), (4.5, 3)))
    )
    return max(0, min(MAX_SCORE, score))


# ---------------------------------------------------------------------------
# Candidate records
# ---------------------------------------------------------------------------

CANDIDATE_COLUMNS: tuple[str, ...] = (
    "parent_asin",
    "source_file",
    "main_category",
    "subcategory",
    "score",
    "status",
    "title",
    "store",
    "average_rating",
    "rating_number",
    "price_usd",
    "features_json",
    "description_json",
    "images_json",
    "details_json",
    "categories_json",
    "raw_json",
)


@dataclass(frozen=True)
class Candidate:
    """A record that survived every hard reject, ready to be written."""

    parent_asin: str
    source_file: str
    main_category: str | None
    subcategory: str
    score: int
    title: str
    store: str | None
    average_rating: float
    rating_number: int
    price_usd: float | None
    features: list[str]
    description: list[str]
    images: list[NormalizedImage]
    details: JsonDict
    categories: list[str]
    raw: JsonDict

    #: Stage 1 never flags a record for review. Validation and the
    #: ``needs_review`` transition belong to stage 2's draft-version flow.
    status: str = "valid"

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.parent_asin,
            self.source_file,
            self.main_category,
            self.subcategory,
            self.score,
            self.status,
            self.title,
            self.store,
            self.average_rating,
            self.rating_number,
            self.price_usd,
            _dump(self.features),
            _dump(self.description),
            _dump(self.images),
            _dump(self.details),
            _dump(self.categories),
            _dump(self.raw),
        )


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def evaluate_record(
    record: JsonDict,
    source_file: str,
    seen_parent_asins: set[str] | None = None,
) -> tuple[Candidate | None, str | None]:
    """Normalize, classify, and score one record, or explain why it was rejected.

    Returns ``(candidate, None)`` on acceptance and ``(None, reason)`` on a hard
    reject. The four rejects, in order, are: missing parent identifier, title
    absent or outside 8-300 characters, no usable image in any resolution, and a
    parent identifier already seen in this run including across source files
    (Requirement 1.4 - 1.7).

    ``seen_parent_asins`` is read, never mutated: the caller decides when a
    record is committed, so a write failure cannot poison the dedupe set.
    """
    parent_asin = _as_optional_str(record.get("parent_asin"))
    if not parent_asin:
        return None, REJECT_NO_PARENT

    title = _as_optional_str(record.get("title")) or ""
    if not (TITLE_MIN_LENGTH <= len(title) <= TITLE_MAX_LENGTH):
        return None, REJECT_TITLE

    images = normalize_images(record.get("images"))
    if not images:
        return None, REJECT_NO_IMAGE

    if seen_parent_asins is not None and parent_asin in seen_parent_asins:
        return None, REJECT_DUPLICATE

    features = normalize_text_list(record.get("features"))
    description = normalize_text_list(record.get("description"))
    details = normalize_details(record.get("details"))
    categories = normalize_text_list(record.get("categories"))
    average_rating = _as_float(record.get("average_rating"))
    rating_number = _as_int(record.get("rating_number"))

    candidate = Candidate(
        parent_asin=parent_asin,
        source_file=source_file,
        main_category=_as_optional_str(record.get("main_category")),
        subcategory=classify_subcategory(title, features),
        score=completeness_score(
            title=title,
            features=features,
            description=description,
            images=images,
            details=details,
            rating_number=rating_number,
            average_rating=average_rating,
        ),
        title=title,
        store=_as_optional_str(record.get("store")),
        average_rating=average_rating,
        rating_number=rating_number,
        price_usd=parse_price_usd(record.get("price")),
        features=features,
        description=description,
        images=images,
        details=details,
        categories=categories,
        raw=record,
    )
    return candidate, None


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS candidates (
        parent_asin      TEXT    PRIMARY KEY,
        source_file      TEXT    NOT NULL,
        main_category    TEXT,
        subcategory      TEXT    NOT NULL,
        score            INTEGER NOT NULL,
        status           TEXT    NOT NULL DEFAULT 'valid',
        title            TEXT    NOT NULL,
        store            TEXT,
        average_rating   REAL    NOT NULL DEFAULT 0,
        rating_number    INTEGER NOT NULL DEFAULT 0,
        price_usd        REAL,
        features_json    TEXT    NOT NULL,
        description_json TEXT    NOT NULL,
        images_json      TEXT    NOT NULL,
        details_json     TEXT    NOT NULL,
        categories_json  TEXT    NOT NULL,
        raw_json         TEXT    NOT NULL
    )
    """,
    # Stage 2 reads exactly this order: top-N per subcategory by score.
    "CREATE INDEX IF NOT EXISTS idx_candidates_subcategory_score "
    "ON candidates (subcategory, score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates (score DESC)",
    # Durable stage boundaries: what makes an interrupted run resumable
    # (Requirement 1.16).
    """
    CREATE TABLE IF NOT EXISTS stage_state (
        stage           TEXT PRIMARY KEY,
        status          TEXT NOT NULL,
        max_lines_debug INTEGER,
        records         INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT NOT NULL
    )
    """,
    # Per-file progress, so an interrupt mid-run resumes at the next source file
    # rather than at the start of the stage.
    """
    CREATE TABLE IF NOT EXISTS source_progress (
        source_file     TEXT PRIMARY KEY,
        lines_read      INTEGER NOT NULL,
        malformed       INTEGER NOT NULL DEFAULT 0,
        kept            INTEGER NOT NULL,
        max_lines_debug INTEGER,
        completed_at    TEXT NOT NULL
    )
    """,
    # Stage 2's honesty record. Written per subcategory so stage 6 can report a
    # shortfall as a measured fact instead of inferring it (Requirement 2.3).
    """
    CREATE TABLE IF NOT EXISTS selection_quota (
        subcategory TEXT PRIMARY KEY,
        quota       INTEGER NOT NULL,
        available   INTEGER NOT NULL,
        selected    INTEGER NOT NULL,
        shortfall   INTEGER NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
)

# Column names are module constants, never caller input; the values are bound.
_INSERT_SQL = (  # noqa: S608
    f"INSERT OR IGNORE INTO candidates ({', '.join(CANDIDATE_COLUMNS)}) "  # noqa: S608
    f"VALUES ({', '.join('?' * len(CANDIDATE_COLUMNS))})"
)


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the candidates database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.commit()


class CandidateWriter:
    """Batched insert buffer.

    Holds at most ``batch_size`` rows and flushes on the boundary, so peak memory
    is one batch regardless of source size (Requirement 1.13).
    ``peak_pending`` exists so a test can assert that rather than trust it.
    """

    def __init__(self, connection: sqlite3.Connection, batch_size: int = BATCH_SIZE) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._connection = connection
        self._pending: list[tuple[Any, ...]] = []
        self.batch_size = batch_size
        self.peak_pending = 0
        self.written = 0
        self.flushes = 0

    @property
    def pending(self) -> int:
        return len(self._pending)

    def add(self, candidate: Candidate) -> None:
        self._pending.append(candidate.to_row())
        self.peak_pending = max(self.peak_pending, len(self._pending))
        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self._connection.executemany(_INSERT_SQL, self._pending)
        self._connection.commit()
        self.written += len(self._pending)
        self.flushes += 1
        self._pending.clear()


def load_seen_parent_asins(connection: sqlite3.Connection) -> set[str]:
    """Seed the dedupe set from rows already stored.

    This is the one structure that legitimately grows with the input, and it is
    also what makes a resumed run behave like an uninterrupted one: identifiers
    written before the interrupt are still recognized as duplicates.
    """
    return {str(row[0]) for row in connection.execute("SELECT parent_asin FROM candidates")}


def subcategory_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Stored row count per subcategory, in the fixed subcategory order."""
    stored = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT subcategory, COUNT(*) FROM candidates GROUP BY subcategory"
        )
    }
    ordered = {name: stored.pop(name, 0) for name in SUBCATEGORIES}
    ordered.update(sorted(stored.items()))
    return ordered


def read_stage_state(connection: sqlite3.Connection, stage: str) -> sqlite3.Row | None:
    cursor = connection.execute("SELECT * FROM stage_state WHERE stage = ?", (stage,))
    return cursor.fetchone()


def write_stage_state(
    connection: sqlite3.Connection,
    stage: str,
    status: str,
    max_lines_debug: int | None,
    records: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO stage_state (stage, status, max_lines_debug, records, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(stage) DO UPDATE SET
            status = excluded.status,
            max_lines_debug = excluded.max_lines_debug,
            records = excluded.records,
            updated_at = excluded.updated_at
        """,
        (stage, status, max_lines_debug, records, _now()),
    )
    connection.commit()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Stage 1
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """What one stage-1 invocation actually did."""

    lines_read: int = 0
    malformed: int = 0
    kept: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    by_subcategory: Counter[str] = field(default_factory=Counter)
    files_processed: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    files_missing: list[str] = field(default_factory=list)
    peak_pending: int = 0
    already_complete: bool = False

    @property
    def rejected_total(self) -> int:
        return sum(self.rejected.values())


def _assert_outputs_outside_raw(config: PipelineConfig) -> None:
    """Refuse to run if output would land inside the immutable source directory.

    Requirement 1.2 is absolute, and a mis-set ``AGENTPAY_OUT_DIR`` is the only
    plausible way this pipeline could ever write into ``datasets/``.
    """
    raw = config.raw_dir.resolve()
    out = config.out_dir.resolve()
    if out == raw or raw in out.parents:
        raise ValueError(
            f"AGENTPAY_OUT_DIR ({out}) is inside the immutable raw directory ({raw}); "
            "the pipeline never writes into the source tree"
        )


def stage_products(
    config: PipelineConfig,
    *,
    force: bool = False,
    batch_size: int = BATCH_SIZE,
    sources: Sequence[str] = META_SOURCES,
    max_lines: int | None = None,
    progress_every: int = 200_000,
) -> StageResult:
    """Stage 1: stream the metadata sources into ``candidates.sqlite``.

    ``max_lines`` overrides the configured ``MAX_LINES_DEBUG``. Re-running is
    safe: completed source files are skipped, and inserts ignore identifiers
    already stored, so the stage is idempotent at its boundary
    (Requirement 1.15, 1.16).
    """
    _assert_outputs_outside_raw(config)
    cap = config.max_lines_debug if max_lines is None else max_lines
    result = StageResult()

    connection = connect(config.candidates_db)
    try:
        ensure_schema(connection)

        state = read_stage_state(connection, STAGE_PRODUCTS)
        completed = {} if force else _completed_sources(connection, cap)

        if state is not None and state["status"] == "complete" and not force:
            if state["max_lines_debug"] != cap:
                print(
                    f"stage 1 (products) was completed at "
                    f"cap={_fmt_cap(state['max_lines_debug'])}, "
                    f"now running at cap={_fmt_cap(cap)}"
                )
            elif _all_sources_done(config, sources, completed):
                # The boundary is "every requested source streamed at this cap",
                # not merely "the stage ran once" -- otherwise adding a source
                # file would be silently ignored.
                print(
                    f"stage 1 (products) already complete at cap={_fmt_cap(cap)}; "
                    "nothing to do (use --force to rescan)"
                )
                result.already_complete = True
                result.kept = int(state["records"])
                result.by_subcategory.update(subcategory_counts(connection))
                return result

        write_stage_state(connection, STAGE_PRODUCTS, "running", cap)
        seen = load_seen_parent_asins(connection)
        if seen:
            print(f"resuming with {len(seen):,} parent identifiers already stored")

        writer = CandidateWriter(connection, batch_size=batch_size)

        for source_name in sources:
            path = config.raw_dir / source_name
            if not path.is_file():
                print(f"  ! missing source, skipping: {path}")
                result.files_missing.append(source_name)
                continue
            if source_name in completed:
                print(f"  = {source_name}: already complete, skipping")
                result.files_skipped.append(source_name)
                continue

            _scan_source(
                path=path,
                source_name=source_name,
                cap=cap,
                seen=seen,
                writer=writer,
                result=result,
                connection=connection,
                progress_every=progress_every,
            )

        writer.flush()
        result.peak_pending = writer.peak_pending

        if len(result.files_missing) == len(sources):
            write_stage_state(connection, STAGE_PRODUCTS, "failed", cap)
            raise FileNotFoundError(
                f"no metadata source found in {config.raw_dir}; expected one of {list(sources)}"
            )

        total = _candidate_count(connection)
        write_stage_state(connection, STAGE_PRODUCTS, "complete", cap, records=total)
        _print_summary(result, connection, config, cap)
        return result
    finally:
        connection.close()


def _scan_source(
    *,
    path: Path,
    source_name: str,
    cap: int | None,
    seen: set[str],
    writer: CandidateWriter,
    result: StageResult,
    connection: sqlite3.Connection,
    progress_every: int,
) -> None:
    """Stream one source file to completion, then flush and record progress."""
    print(f"  > {source_name}: streaming (cap={_fmt_cap(cap)})")
    stats = ScanStats()
    kept_before = result.kept

    for record in iter_jsonl_gz(path, max_lines=cap, stats=stats):
        candidate, reason = evaluate_record(record, source_name, seen)
        if candidate is None:
            result.rejected[reason or "unknown"] += 1
            continue
        seen.add(candidate.parent_asin)
        writer.add(candidate)
        result.kept += 1
        result.by_subcategory[candidate.subcategory] += 1

        if progress_every and result.kept % progress_every == 0:
            print(f"    ... {stats.lines_read:,} lines read, {result.kept:,} kept")

    writer.flush()
    result.lines_read += stats.lines_read
    result.malformed += stats.malformed
    kept_here = result.kept - kept_before
    connection.execute(
        """
        INSERT INTO source_progress
            (source_file, lines_read, malformed, kept, max_lines_debug, completed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_file) DO UPDATE SET
            lines_read = excluded.lines_read,
            malformed = excluded.malformed,
            kept = excluded.kept,
            max_lines_debug = excluded.max_lines_debug,
            completed_at = excluded.completed_at
        """,
        (source_name, stats.lines_read, stats.malformed, kept_here, cap, _now()),
    )
    connection.commit()
    result.files_processed.append(source_name)
    print(
        f"  < {source_name}: {stats.lines_read:,} lines, "
        f"{stats.malformed:,} malformed, {kept_here:,} kept"
    )


def _all_sources_done(
    config: PipelineConfig, sources: Sequence[str], completed: Mapping[str, int]
) -> bool:
    """Whether every source present on disk has already been streamed at this cap."""
    return all(name in completed for name in sources if (config.raw_dir / name).is_file())


def _completed_sources(connection: sqlite3.Connection, cap: int | None) -> dict[str, int]:
    """Source files already streamed to completion under the same cap."""
    rows = connection.execute(
        "SELECT source_file, kept, max_lines_debug FROM source_progress"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows if row[2] == cap}


def _candidate_count(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) FROM candidates").fetchone()
    return int(row[0]) if row else 0


def _fmt_cap(cap: int | None) -> str:
    """Render the debug line cap for the operator summary."""
    return "none" if cap is None else f"{cap:,}"


def _print_summary(
    result: StageResult,
    connection: sqlite3.Connection,
    config: PipelineConfig,
    cap: int | None,
) -> None:
    print("")
    print(f"stage 1 (products) -> {config.candidates_db}")
    print(f"  lines read      {result.lines_read:,}")
    print(f"  malformed       {result.malformed:,}")
    print(f"  kept this run   {result.kept:,}")
    print(f"  rejected        {result.rejected_total:,}")
    for reason in REJECT_REASONS:
        print(f"    {reason:<24} {result.rejected.get(reason, 0):,}")
    print(f"  peak insert batch {result.peak_pending:,} (limit {BATCH_SIZE:,})")
    print("")
    print("  stored rows by subcategory:")
    counts = subcategory_counts(connection)
    for subcategory, count in counts.items():
        print(f"    {subcategory:<22} {count:,}")
    print(f"    {'TOTAL':<22} {sum(counts.values()):,}")
    if cap is not None:
        print("")
        print(f"  note: capped run (MAX_LINES_DEBUG={cap:,}); counts are not the full dataset")


# ---------------------------------------------------------------------------
# Unit normalization (Requirement 2.7)
# ---------------------------------------------------------------------------
#
# The source states the same fact a dozen ways: "16 GB", "16GB", "16 Gb RAM",
# "4 lbs", "1.81 kg", "13.3 x 9.1 x 0.7 inches", "$1,299.99". Downstream filters
# compare numbers, so every quantity is converted once, here, into one unit per
# dimension -- and a value that cannot be parsed becomes ``None`` rather than a
# guess. A wrong number in a specification filter is worse than a missing one.

#: Minor units per major unit. USD because that is what the dataset carries, INR
#: because stage 4 prices in paise.
CURRENCY_MINOR_UNITS: dict[str, int] = {"USD": 100, "INR": 100}

#: A decimal number, optionally thousands-separated.
_NUMBER = r"(\d+(?:,\d{3})*(?:\.\d+)?)"

#: Binary multiples throughout, for memory and storage alike. Manufacturers mean
#: decimal GB on a drive label and binary GiB in a memory module; picking one
#: convention and stating it beats being silently inconsistent per field.
_DATA_UNIT_TO_GB: dict[str, float] = {
    "kb": 1.0 / 1_048_576,
    "mb": 1.0 / 1024,
    "gb": 1.0,
    "tb": 1024.0,
    "pb": 1024.0 * 1024.0,
}

_WEIGHT_UNIT_TO_GRAMS: dict[str, float] = {
    "mg": 0.001,
    "g": 1.0,
    "gm": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "lb": 453.592_37,
    "lbs": 453.592_37,
    "pound": 453.592_37,
    "pounds": 453.592_37,
    "oz": 28.349_523_125,
    "ounce": 28.349_523_125,
    "ounces": 28.349_523_125,
}

_LENGTH_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "millimetre": 1.0,
    "millimetres": 1.0,
    "cm": 10.0,
    "centimeter": 10.0,
    "centimeters": 10.0,
    "centimetre": 10.0,
    "centimetres": 10.0,
    "m": 1000.0,
    "meter": 1000.0,
    "meters": 1000.0,
    "metre": 1000.0,
    "metres": 1000.0,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    '"': 25.4,
    "ft": 304.8,
    "foot": 304.8,
    "feet": 304.8,
}

_DELIVERY_UNIT_TO_DAYS: dict[str, float] = {
    "hour": 1.0 / 24.0,
    "hours": 1.0 / 24.0,
    "hr": 1.0 / 24.0,
    "hrs": 1.0 / 24.0,
    "day": 1.0,
    "days": 1.0,
    "week": 7.0,
    "weeks": 7.0,
    "month": 30.0,
    "months": 30.0,
}

_TRUE_TOKENS = frozenset({"true", "yes", "y", "1", "on", "enabled", "included", "available"})
_FALSE_TOKENS = frozenset(
    {"false", "no", "n", "0", "off", "disabled", "not included", "unavailable", "none"}
)


def _alternation(units: Iterable[str]) -> str:
    """Longest-first alternation, so ``inches`` never matches as ``in``."""
    return "|".join(re.escape(unit) for unit in sorted(units, key=len, reverse=True))


_DATA_RE = re.compile(rf"{_NUMBER}\s*({_alternation(_DATA_UNIT_TO_GB)})(?!\w)", re.IGNORECASE)
_WEIGHT_RE = re.compile(
    rf"{_NUMBER}\s*({_alternation(_WEIGHT_UNIT_TO_GRAMS)})(?!\w)", re.IGNORECASE
)
_LENGTH_RE = re.compile(rf"{_NUMBER}\s*({_alternation(_LENGTH_UNIT_TO_MM)})(?!\w)", re.IGNORECASE)
_DELIVERY_RE = re.compile(
    rf"{_NUMBER}\s*(?:business\s+|working\s+|calendar\s+)?"
    rf"({_alternation(_DELIVERY_UNIT_TO_DAYS)})(?!\w)",
    re.IGNORECASE,
)
#: The ``x`` between two dimension terms. The lookaround is what stops the ``x``
#: inside a word ("Approx 5 x 3") from being read as a separator.
_DIMENSION_SPLIT_RE = re.compile(r"(?<=[\s\d])\s*[x\u00d7]\s*(?=[\s\d])", re.IGNORECASE)

#: One dimension term: a number and, optionally, its own unit.
_DIMENSION_TERM_RE = re.compile(rf"{_NUMBER}\s*([a-z\"]*)", re.IGNORECASE)


def _as_decimal(value: object) -> Decimal | None:
    """Parse a money-ish value exactly, without ever touching a float.

    ``float("899.99") * 100`` is ``89998.999...``, and ``int()`` of that is
    ``89998``. One cent lost per product is the kind of bug that is invisible
    until someone reconciles a total, so the currency path is Decimal only.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    text = re.sub(r"[^0-9.\-]", "", value.strip())
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_currency_minor(value: object, currency: str = "USD") -> int | None:
    """Convert a currency amount to integer minor units (Requirement 2.7).

    Returns ``None`` for anything missing, unparseable, or non-positive: a zero
    price in this dataset means "no price given", not "free".
    """
    factor = CURRENCY_MINOR_UNITS.get(currency.upper())
    if factor is None:
        raise ValueError(f"unknown currency {currency!r}; known: {sorted(CURRENCY_MINOR_UNITS)}")
    amount = _as_decimal(value)
    if amount is None or amount <= 0:
        return None
    return int((amount * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _first_quantity(
    value: object,
    pattern: re.Pattern[str],
    factors: Mapping[str, float],
) -> float | None:
    """First ``number unit`` pair in a string, scaled by its unit factor."""
    if not isinstance(value, str):
        return None
    match = pattern.search(value)
    if match is None:
        return None
    number = float(match.group(1).replace(",", ""))
    return number * factors[match.group(2).lower()]


def parse_data_size_gb(value: object) -> float | None:
    """Convert a memory or storage capacity to GB (Requirement 2.7).

    A bare number is read as GB, which is what the dataset means when a ``RAM``
    detail says ``16``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(float(value), 3) if value > 0 else None
    scaled = _first_quantity(value, _DATA_RE, _DATA_UNIT_TO_GB)
    if scaled is None and isinstance(value, str):
        bare = _as_decimal(value)
        scaled = float(bare) if bare is not None and bare > 0 else None
    if scaled is None or scaled <= 0:
        return None
    return round(scaled, 3)


#: Requirement 2.7 names memory and storage separately. They share one converter
#: because they share one unit; the aliases exist so call sites read correctly.
parse_memory_gb = parse_data_size_gb
parse_storage_gb = parse_data_size_gb


def parse_weight_grams(value: object) -> int | None:
    """Convert a weight to whole grams (Requirement 2.7).

    A bare number is read as grams. The unit alternation is word-bounded, so
    ``16 GB`` is not read as 16 grams.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(value) if value > 0 else None
    scaled = _first_quantity(value, _WEIGHT_RE, _WEIGHT_UNIT_TO_GRAMS)
    if scaled is None or scaled <= 0:
        return None
    return round(scaled)


def parse_length_mm(value: object) -> float | None:
    """Convert a single length to millimetres (Requirement 2.7)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(float(value), 1) if value > 0 else None
    scaled = _first_quantity(value, _LENGTH_RE, _LENGTH_UNIT_TO_MM)
    if scaled is None or scaled <= 0:
        return None
    return round(scaled, 1)


DIMENSION_FIELDS: tuple[str, ...] = ("length_mm", "width_mm", "height_mm")


def parse_dimensions_mm(value: object) -> JsonDict | None:
    """Convert an ``L x W x H`` dimension string to millimetres (Requirement 2.7).

    The dataset writes dimensions as ``"12.8 x 8.9 x 0.6 inches; 3.5 pounds"``,
    sometimes with the unit on every term and sometimes only on the last. A unit
    given anywhere in the triple applies to every term that lacks one; with no
    unit at all the value is unparseable rather than assumed.
    """
    if not isinstance(value, str) or not value.strip():
        return None

    for segment in re.split(r"[;|]", value):
        parts = _DIMENSION_SPLIT_RE.split(segment.strip())
        if len(parts) < 2:
            continue

        terms: list[tuple[str, float | None]] = []
        for part in parts[: len(DIMENSION_FIELDS)]:
            match = _DIMENSION_TERM_RE.search(part)
            if match is None:
                break
            terms.append((match.group(1), _LENGTH_UNIT_TO_MM.get(match.group(2).lower())))
        if len(terms) < 2:
            continue

        fallback = next((factor for _, factor in reversed(terms) if factor is not None), None)
        if fallback is None:
            continue

        out: JsonDict = {}
        for field_name, (number, factor) in zip(DIMENSION_FIELDS, terms, strict=False):
            millimetres = float(number.replace(",", "")) * (fallback if factor is None else factor)
            if millimetres > 0:
                out[field_name] = round(millimetres, 1)
        if out:
            return out
    return None


def parse_delivery_days(value: object) -> int | None:
    """Convert a delivery window to whole days (Requirement 2.7).

    Rounds **up** and takes the slowest bound of a range, so ``"3-5 business
    days"`` is 5 and ``"24 hours"`` is 1. A promise a buyer sees should be the
    pessimistic reading of the source, never the flattering one.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return math.ceil(value) if value >= 0 else None
    if not isinstance(value, str):
        return None

    text = value.strip().lower()
    if not text:
        return None
    if "same day" in text or "same-day" in text:
        return 0
    if "next day" in text or "next-day" in text or "overnight" in text:
        return 1

    matches = _DELIVERY_RE.findall(text)
    if not matches:
        return None
    slowest = max(
        float(number.replace(",", "")) * _DELIVERY_UNIT_TO_DAYS[unit.lower()]
        for number, unit in matches
    )
    return math.ceil(slowest) if slowest >= 0 else None


def parse_boolean(value: object) -> bool | None:
    """Normalize a truthy or falsy source value to ``True`` or ``False``.

    Returns ``None`` for anything not recognizably boolean, so an unparsed value
    is never silently reported as ``False`` (Requirement 2.7).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


# ---------------------------------------------------------------------------
# Specifications
# ---------------------------------------------------------------------------

#: Detail keys per normalized field, matched case-insensitively, first hit wins.
SPEC_KEY_SOURCES: dict[str, tuple[str, ...]] = {
    "brand": ("brand", "brand name", "manufacturer"),
    "model_number": ("item model number", "model number", "model name", "part number"),
    "color": ("color", "colour"),
    "memory_gb": (
        "ram",
        "installed ram",
        "ram memory installed size",
        "computer memory size",
        "memory storage capacity",
    ),
    "storage_gb": (
        "hard drive",
        "hard drive size",
        "digital storage capacity",
        "flash memory size",
        "total storage capacity",
    ),
    "weight_grams": ("item weight", "product weight", "weight", "package weight"),
    "dimensions_mm": (
        "product dimensions",
        "item dimensions lxwxh",
        "item dimensions",
        "package dimensions",
    ),
    "delivery_days": ("shipping", "delivery", "estimated delivery", "shipping time"),
}

#: ``Product Dimensions`` in this dataset often carries the weight after a
#: semicolon: ``"12.8 x 8.9 x 0.6 inches; 3.5 pounds"``.
_WEIGHT_FALLBACK_KEYS: tuple[str, ...] = SPEC_KEY_SOURCES["dimensions_mm"]

_CAPACITY_KEYWORDS: dict[str, str] = {
    "memory_gb": r"ram|memory|ddr\d?",
    "storage_gb": r"ssd|hdd|hard\s+drive|storage|emmc|flash\s+memory",
}


def _slug(value: object) -> str:
    """Lowercase, underscore-joined identifier fragment. Empty when unusable."""
    text = value if isinstance(value, str) else str(value or "")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text.strip().lower())).strip("_")


def _lookup(details: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """First present, non-empty value among ``keys``, matched case-insensitively."""
    lowered = {str(key).strip().lower(): value for key, value in details.items()}
    for key in keys:
        value = lowered.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _capacity_from_texts(texts: Sequence[str], keywords: str) -> float | None:
    """Pull a capacity out of feature bullets like ``"16 GB DDR4 RAM"``.

    The details map is empty on a large share of this dataset, and the capacity
    is usually sitting in a feature bullet instead. Both word orders occur, so
    both are matched -- but only next to a keyword, because an unqualified
    ``"512 GB"`` could be either memory or storage and guessing would be worse
    than leaving the field out.
    """
    after = re.compile(
        rf"{_NUMBER}\s*({_alternation(_DATA_UNIT_TO_GB)})(?!\w)[^,.;]{{0,20}}?(?:{keywords})(?!\w)",
        re.IGNORECASE,
    )
    before = re.compile(
        rf"(?:{keywords})(?!\w)[^0-9]{{0,20}}?{_NUMBER}\s*({_alternation(_DATA_UNIT_TO_GB)})(?!\w)",
        re.IGNORECASE,
    )
    for text in texts:
        for pattern in (after, before):
            match = pattern.search(text)
            if match is not None:
                gigabytes = (
                    float(match.group(1).replace(",", ""))
                    * _DATA_UNIT_TO_GB[match.group(2).lower()]
                )
                if gigabytes > 0:
                    return round(gigabytes, 3)
    return None


def build_specifications(
    details: Mapping[str, Any],
    features: Sequence[str] = (),
) -> JsonDict:
    """Normalized specification map for one product (Requirement 2.7).

    Every quantity is in one unit per dimension: GB for memory and storage,
    grams for weight, millimetres for dimensions, whole days for delivery.
    Unparseable fields are omitted rather than defaulted, and every value in
    ``details`` that reads as a boolean is collected under ``flags`` as a real
    ``true``/``false``.
    """
    specs: JsonDict = {}

    for name in ("brand", "model_number", "color"):
        text = _as_optional_str(_lookup(details, SPEC_KEY_SOURCES[name]))
        if text:
            specs[name] = text

    for name in ("memory_gb", "storage_gb"):
        capacity = parse_data_size_gb(_lookup(details, SPEC_KEY_SOURCES[name]))
        if capacity is None:
            capacity = _capacity_from_texts(features, _CAPACITY_KEYWORDS[name])
        if capacity is not None:
            specs[name] = capacity

    weight = parse_weight_grams(_lookup(details, SPEC_KEY_SOURCES["weight_grams"]))
    if weight is None:
        weight = parse_weight_grams(_lookup(details, _WEIGHT_FALLBACK_KEYS))
    if weight is not None:
        specs["weight_grams"] = weight

    dimensions = parse_dimensions_mm(_lookup(details, SPEC_KEY_SOURCES["dimensions_mm"]))
    if dimensions is not None:
        specs["dimensions_mm"] = dimensions

    delivery = parse_delivery_days(_lookup(details, SPEC_KEY_SOURCES["delivery_days"]))
    if delivery is not None:
        specs["delivery_days"] = delivery

    flags = {
        slug: flag
        for slug, flag in ((_slug(key), parse_boolean(value)) for key, value in details.items())
        if slug and flag is not None
    }
    if flags:
        specs["flags"] = flags

    return specs


# ---------------------------------------------------------------------------
# Product identity
# ---------------------------------------------------------------------------

#: Namespace folded into the identifier hash. Stage 4 hashes the same
#: ``parent_asin`` for prices and stage 5 for review identifiers; namespacing
#: keeps those three derivations from ever being the same number.
PRODUCT_ID_NAMESPACE = "agentpay:product:"
PRODUCT_ID_PREFIX = "prod_"

#: 24 hex characters is 96 bits. Over a 20,000-product catalog the chance of a
#: collision is on the order of 1e-20, and the identifier still fits on a line.
PRODUCT_ID_HEX_LENGTH = 24


def derive_product_id(parent_asin: str) -> str:
    """Derive the product identifier from the source parent identifier.

    Pure and total: the same ``parent_asin`` yields the same identifier in every
    run, process, and interpreter, which is what makes re-running the pipeline
    produce the same catalog rather than a parallel one (Requirement 2.4,
    Property 24).

    SHA-1 here is a *naming* function, not a security control -- it is never used
    to authenticate anything, and the input is a public catalog identifier.
    """
    key = _as_optional_str(parent_asin)
    if not key:
        raise ValueError("parent_asin is required to derive a product identifier")
    digest = hashlib.sha1(f"{PRODUCT_ID_NAMESPACE}{key}".encode()).hexdigest()
    return f"{PRODUCT_ID_PREFIX}{digest[:PRODUCT_ID_HEX_LENGTH]}"


def raw_metadata_relative_path(product_id: str) -> str:
    """Path of a product's provenance file, relative to the catalog directory."""
    return f"raw_metadata/{product_id}.json"


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def resolve_product_images(images: Sequence[Mapping[str, Any]]) -> list[JsonDict]:
    """Resolve each image entry to its best available URL (Requirement 2.9, 2.10).

    Applies the ``hi_res -> large -> thumb`` fallback per entry, drops entries
    with no usable URL, and deduplicates by URL within the product while keeping
    source order. Recording which resolution won means a later quality question
    ("how much of the catalog is thumbnails?") is answerable from the artifact.
    """
    resolved: list[JsonDict] = []
    seen: set[str] = set()
    for image in images:
        if not isinstance(image, Mapping):
            continue
        url = best_image_url(image)
        if url is None or url in seen:
            continue
        seen.add(url)
        resolved.append(
            {
                "position": len(resolved),
                "variant": _as_optional_str(image.get("variant")),
                "resolution": _resolution_of(image, url),
                "url": url,
            }
        )
    return resolved


def _resolution_of(image: Mapping[str, Any], url: str) -> str:
    for key in IMAGE_RESOLUTION_ORDER:
        value = image.get(key)
        if is_usable_url(value) and str(value).strip() == url:
            return key
    return IMAGE_RESOLUTION_ORDER[-1]


def image_storage_key(
    product_id: str,
    variant: object,
    position: int,
    used: set[str] | None = None,
) -> str:
    """Proposed object-storage key for one image: ``{product_id}/{variant}.jpg``.

    ``variant`` is not unique in the source -- several entries can be ``MAIN``,
    and many are missing entirely -- so a key already taken within this product
    gets the position appended. Requirement 2.10 forbids duplicate storage keys,
    and one product overwriting its own image is exactly the failure that would
    cause.
    """
    slug = _slug(variant) or f"image_{position:02d}"
    key = f"{product_id}/{slug}.jpg"
    if used is None:
        return key
    if key in used:
        key = f"{product_id}/{slug}_{position:02d}.jpg"
        suffix = 1
        while key in used:
            key = f"{product_id}/{slug}_{position:02d}_{suffix}.jpg"
            suffix += 1
    used.add(key)
    return key


def image_manifest_rows(product: Mapping[str, Any]) -> list[JsonDict]:
    """Manifest rows for one product: URLs only, deduplicated, no download.

    Stage 3's whole payload. ``downloaded`` starts ``false`` on every row so the
    downloader in a later phase has a resumability flag and the artifact states
    plainly that nothing has been fetched (Requirement 2.11).
    """
    product_id = str(product["product_id"])
    external_id = product.get("external_product_id")
    rows: list[JsonDict] = []
    seen_urls: set[str] = set()
    used_keys: set[str] = set()

    for image in product.get("images") or []:
        if not isinstance(image, Mapping):
            continue
        url = image.get("url")
        if not is_usable_url(url) or url in seen_urls:
            continue
        seen_urls.add(str(url))
        position = len(rows)
        rows.append(
            {
                "product_id": product_id,
                "external_product_id": external_id,
                "position": position,
                "variant": _as_optional_str(image.get("variant")),
                "resolution": _as_optional_str(image.get("resolution")),
                "source_url": str(url).strip(),
                "storage_key": image_storage_key(
                    product_id, image.get("variant"), position, used_keys
                ),
                "downloaded": False,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Product objects
# ---------------------------------------------------------------------------

SOURCE_PRICE_NOTE = (
    "Recorded USD price from the source dataset. It is historical dataset metadata, "
    "not a guarantee of a current market price."
)

# Deliberately fixed for the demo catalog: one recorded USD is represented as
# 100 INR. Both currencies have two decimal places, so this is also the exact
# conversion factor from source cents to offer paise.
DATASET_USD_TO_INR_RATE = 100

RowLike = sqlite3.Row | Mapping[str, Any]


def source_price_reference(value: object, currency: str = "USD") -> JsonDict | None:
    """Return the dataset's recorded price in exact minor units.

    This is historical dataset metadata, not a live market-price promise. Stage
    4 copies it to the USD offer so the checkout price remains traceable.
    """
    minor = parse_currency_minor(value, currency)
    if minor is None:
        return None
    return {
        "amount_minor": minor,
        "currency": currency.upper(),
        "is_authoritative": True,
        "note": SOURCE_PRICE_NOTE,
    }


def build_product(row: RowLike) -> JsonDict:
    """Build the normalized product object for one selected candidate row.

    Pure: the same row always produces the same dict, in the same key order, so
    ``products.jsonl`` is byte-identical across runs.
    """
    parent_asin = str(row["parent_asin"])
    product_id = derive_product_id(parent_asin)
    features = _load_list(row["features_json"])
    description = _load_list(row["description_json"])
    details = _load_dict(row["details_json"])

    return {
        "product_id": product_id,
        "external_product_id": parent_asin,
        "category_id": row["subcategory"],
        "main_category": row["main_category"],
        "title": row["title"],
        "store": row["store"],
        "status": row["status"],
        "normalized_features": features,
        "description": description,
        "specifications": build_specifications(details, features),
        "images": resolve_product_images(_load_list(row["images_json"])),
        "average_rating": round(float(row["average_rating"] or 0.0), 3),
        "rating_number": int(row["rating_number"] or 0),
        "completeness_score": int(row["score"]),
        "source_price": source_price_reference(row["price_usd"]),
        "provenance": {
            "parent_asin": parent_asin,
            "source_file": row["source_file"],
            "source_categories": _load_list(row["categories_json"]),
            "raw_metadata_path": raw_metadata_relative_path(product_id),
        },
    }


def _load_list(value: object) -> list[Any]:
    parsed = json.loads(value) if isinstance(value, str) and value else []
    return parsed if isinstance(parsed, list) else []


def _load_dict(value: object) -> JsonDict:
    parsed = json.loads(value) if isinstance(value, str) and value else {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Stage 2 -- quota-based selection
# ---------------------------------------------------------------------------

#: One query per subcategory, ranked in the database rather than in Python: the
#: candidate table is far larger than the quota, and sorting it in process would
#: mean loading it (Requirement 2.1).
#:
#: ``parent_asin`` is a third sort key the requirement does not ask for. Without
#: it, two candidates with the same score and rating count are ordered by
#: whatever the query planner happens to return, and "repeated runs produce
#: identical output" would be luck rather than a property.
_SELECT_SQL = """
    SELECT * FROM candidates
    WHERE subcategory = ?
    ORDER BY score DESC, rating_number DESC, parent_asin ASC
    LIMIT ?
"""


@dataclass(frozen=True)
class QuotaOutcome:
    """What one subcategory's quota actually yielded."""

    subcategory: str
    quota: int
    available: int
    selected: int

    @property
    def shortfall(self) -> int:
        """How far under its cap this bucket landed. Never padded, only reported."""
        return max(0, self.quota - self.selected)

    @property
    def is_short(self) -> bool:
        return self.shortfall > 0


@dataclass
class SelectionResult:
    """What one stage-2 invocation actually did."""

    outcomes: list[QuotaOutcome] = field(default_factory=list)
    products_written: int = 0
    raw_metadata_written: int = 0
    already_complete: bool = False

    @property
    def total_quota(self) -> int:
        return sum(outcome.quota for outcome in self.outcomes)

    @property
    def total_selected(self) -> int:
        return sum(outcome.selected for outcome in self.outcomes)

    @property
    def total_shortfall(self) -> int:
        return sum(outcome.shortfall for outcome in self.outcomes)

    @property
    def shortfalls(self) -> dict[str, int]:
        """Subcategory to shortfall, for buckets the source could not fill."""
        return {
            outcome.subcategory: outcome.shortfall for outcome in self.outcomes if outcome.is_short
        }

    @property
    def selected_by_subcategory(self) -> dict[str, int]:
        return {outcome.subcategory: outcome.selected for outcome in self.outcomes}


def available_count(connection: sqlite3.Connection, subcategory: str) -> int:
    """Candidates stored for one subcategory, regardless of quota."""
    row = connection.execute(
        "SELECT COUNT(*) FROM candidates WHERE subcategory = ?", (subcategory,)
    ).fetchone()
    return int(row[0]) if row else 0


def select_for_subcategory(
    connection: sqlite3.Connection,
    subcategory: str,
    quota: int,
) -> Iterator[sqlite3.Row]:
    """Top-``quota`` candidates for one subcategory, best first (Requirement 2.1).

    A cursor, not a list: the caller writes each row out and moves on, so peak
    memory is one row rather than one quota.
    """
    if quota < 0:
        raise ValueError(f"quota must be non-negative, got {quota}")
    if quota == 0:
        return iter(())
    return connection.execute(_SELECT_SQL, (subcategory, quota))


def write_selection_quotas(
    connection: sqlite3.Connection, outcomes: Sequence[QuotaOutcome]
) -> None:
    """Persist the per-subcategory quota outcome for stage 6 (Requirement 2.3)."""
    connection.executemany(
        """
        INSERT INTO selection_quota
            (subcategory, quota, available, selected, shortfall, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(subcategory) DO UPDATE SET
            quota = excluded.quota,
            available = excluded.available,
            selected = excluded.selected,
            shortfall = excluded.shortfall,
            updated_at = excluded.updated_at
        """,
        [
            (
                outcome.subcategory,
                outcome.quota,
                outcome.available,
                outcome.selected,
                outcome.shortfall,
                _now(),
            )
            for outcome in outcomes
        ],
    )
    connection.commit()


def read_selection_quotas(connection: sqlite3.Connection) -> list[QuotaOutcome]:
    """Read back what stage 2 recorded, in quota-table order."""
    stored = {
        str(row["subcategory"]): row for row in connection.execute("SELECT * FROM selection_quota")
    }
    return [
        QuotaOutcome(
            subcategory=name,
            quota=int(stored[name]["quota"]),
            available=int(stored[name]["available"]),
            selected=int(stored[name]["selected"]),
        )
        for name in SUBCATEGORY_QUOTAS
        if name in stored
    ]


def stage_select(
    config: PipelineConfig,
    *,
    force: bool = False,
    quotas: Mapping[str, int] = SUBCATEGORY_QUOTAS,
    max_lines: int | None = None,
) -> SelectionResult:
    """Stage 2: apply the per-subcategory quota and write the catalog artifacts.

    Reads ``candidates.sqlite`` only. Writes ``catalog/products.jsonl`` and one
    ``catalog/raw_metadata/{product_id}.json`` per selected product, the latter
    being the retained source record copied out verbatim rather than
    re-serialized from the normalized fields (Requirement 2.6).
    """
    _assert_outputs_outside_raw(config)
    cap = config.max_lines_debug if max_lines is None else max_lines
    result = SelectionResult()

    if not config.candidates_db.is_file():
        raise FileNotFoundError(
            f"{config.candidates_db} does not exist; run 'products' (stage 1) first"
        )

    connection = connect(config.candidates_db)
    try:
        ensure_schema(connection)
        if not force and _stage_is_complete(connection, STAGE_SELECT, cap, config.products_jsonl):
            print(
                f"stage 2 (select) already complete at cap={_fmt_cap(cap)}; "
                "nothing to do (use --force to reselect)"
            )
            result.already_complete = True
            result.outcomes = read_selection_quotas(connection)
            result.products_written = result.total_selected
            result.raw_metadata_written = result.total_selected
            return result

        write_stage_state(connection, STAGE_SELECT, "running", cap)
        config.raw_metadata_dir.mkdir(parents=True, exist_ok=True)
        # A reselection with a different candidate pool must not leave provenance
        # files for products that are no longer in the catalog.
        removed = _clear_raw_metadata(config.raw_metadata_dir)
        if removed:
            print(f"  cleared {removed:,} stale provenance file(s)")

        with config.products_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
            for subcategory, quota in quotas.items():
                selected = 0
                for row in select_for_subcategory(connection, subcategory, quota):
                    product = build_product(row)
                    handle.write(_dump(product) + "\n")
                    _write_raw_metadata(config, str(product["product_id"]), str(row["raw_json"]))
                    result.raw_metadata_written += 1
                    selected += 1
                result.outcomes.append(
                    QuotaOutcome(
                        subcategory=subcategory,
                        quota=quota,
                        available=available_count(connection, subcategory),
                        selected=selected,
                    )
                )
                result.products_written += selected
                print(f"  {subcategory:<22} {selected:>7,} / {quota:,}")

        _assert_quotas_respected(result, quotas)
        write_selection_quotas(connection, result.outcomes)
        write_stage_state(
            connection, STAGE_SELECT, "complete", cap, records=result.products_written
        )
        _print_selection_summary(result, config, cap)
        return result
    finally:
        connection.close()


def _write_raw_metadata(config: PipelineConfig, product_id: str, raw_json: str) -> None:
    """Copy the retained source record out byte for byte (Requirement 2.6).

    Written from the stored ``raw_json`` string, not re-serialized from the
    normalized product, so the provenance file is the source record and not the
    pipeline's opinion of it.
    """
    path = config.raw_metadata_dir / f"{product_id}.json"
    path.write_bytes(raw_json.encode("utf-8"))


def _clear_raw_metadata(directory: Path) -> int:
    """Remove provenance files this pipeline wrote. Touches nothing else."""
    removed = 0
    for path in directory.glob(f"{PRODUCT_ID_PREFIX}*.json"):
        path.unlink()
        removed += 1
    return removed


def _assert_quotas_respected(result: SelectionResult, quotas: Mapping[str, int]) -> None:
    """Fail loudly rather than publish a catalog that broke its own caps.

    Requirement 2.2 and Property 25. The ``LIMIT`` makes this true by
    construction; the check exists so a future change to the query cannot quietly
    make it false.
    """
    for outcome in result.outcomes:
        if outcome.selected > outcome.quota:
            raise ValueError(
                f"quota exceeded for {outcome.subcategory}: "
                f"selected {outcome.selected} > quota {outcome.quota}"
            )
    if result.products_written != result.total_selected:
        raise ValueError(
            f"products written ({result.products_written}) does not match the sum of "
            f"per-subcategory selections ({result.total_selected})"
        )
    unknown = set(result.selected_by_subcategory) - set(quotas)
    if unknown:
        raise ValueError(f"selection produced subcategories with no quota: {sorted(unknown)}")


def _stage_is_complete(
    connection: sqlite3.Connection,
    stage: str,
    cap: int | None,
    artifact: Path,
) -> bool:
    """Whether a stage's durable boundary is intact (Requirement 1.16).

    All three conditions matter: the state row says complete, it was completed at
    the *same* debug cap (a different cap means a different candidate pool), and
    the artifact is still on disk.
    """
    state = read_stage_state(connection, stage)
    if state is None or state["status"] != "complete":
        return False
    if state["max_lines_debug"] != cap:
        print(
            f"stage '{stage}' was completed at cap={_fmt_cap(state['max_lines_debug'])}, "
            f"now running at cap={_fmt_cap(cap)}"
        )
        return False
    return artifact.is_file()


def _print_selection_summary(
    result: SelectionResult, config: PipelineConfig, cap: int | None
) -> None:
    print("")
    print(f"stage 2 (select) -> {config.products_jsonl}")
    print(f"  {'subcategory':<22} {'quota':>8} {'available':>10} {'selected':>9} {'short':>7}")
    for outcome in result.outcomes:
        print(
            f"  {outcome.subcategory:<22} {outcome.quota:>8,} {outcome.available:>10,} "
            f"{outcome.selected:>9,} {outcome.shortfall:>7,}"
        )
    print(
        f"  {'TOTAL':<22} {result.total_quota:>8,} {'':>10} "
        f"{result.total_selected:>9,} {result.total_shortfall:>7,}"
    )
    print(f"  provenance files  {result.raw_metadata_written:,} in {config.raw_metadata_dir}")
    if result.total_shortfall:
        print("")
        print("  under-filled buckets (reported, never padded from another subcategory):")
        for subcategory, shortfall in result.shortfalls.items():
            print(f"    {subcategory:<22} short by {shortfall:,}")
    if cap is not None:
        print("")
        print(
            f"  note: the candidate pool came from a capped stage-1 run "
            f"(MAX_LINES_DEBUG={cap:,}); shortfalls here reflect the cap, not the dataset"
        )


# ---------------------------------------------------------------------------
# Stage 3 -- image manifest
# ---------------------------------------------------------------------------


@dataclass
class ImageManifestResult:
    """What one stage-3 invocation actually did."""

    products_read: int = 0
    images_written: int = 0
    #: Image entries present in ``products.jsonl`` that produced no manifest row,
    #: because the URL was a duplicate within the product or was not usable.
    #: Normally zero: stage 2 already resolved and deduplicated.
    images_skipped: int = 0
    products_without_images: int = 0
    malformed_lines: int = 0
    by_resolution: Counter[str] = field(default_factory=Counter)
    already_complete: bool = False


def stage_images(
    config: PipelineConfig,
    *,
    force: bool = False,
    max_lines: int | None = None,
) -> ImageManifestResult:
    """Stage 3: write the image manifest from ``products.jsonl``.

    URLs only. No socket is opened and no byte is fetched (Requirement 2.11):
    locking the selection before spending bandwidth is the entire reason this is
    a separate stage from the downloader.
    """
    _assert_outputs_outside_raw(config)
    cap = config.max_lines_debug if max_lines is None else max_lines
    result = ImageManifestResult()

    if not config.products_jsonl.is_file():
        raise FileNotFoundError(
            f"{config.products_jsonl} does not exist; run 'select' (stage 2) first"
        )

    connection = connect(config.candidates_db)
    try:
        ensure_schema(connection)
        if not force and _stage_is_complete(
            connection, STAGE_IMAGES, cap, config.images_manifest_jsonl
        ):
            state = read_stage_state(connection, STAGE_IMAGES)
            print(
                f"stage 3 (images) already complete at cap={_fmt_cap(cap)}; "
                "nothing to do (use --force to rebuild)"
            )
            result.already_complete = True
            result.images_written = int(state["records"]) if state is not None else 0
            return result

        write_stage_state(connection, STAGE_IMAGES, "running", cap)
        config.catalog_dir.mkdir(parents=True, exist_ok=True)
        seen_keys: set[str] = set()

        with (
            config.products_jsonl.open("r", encoding="utf-8") as source,
            config.images_manifest_jsonl.open("w", encoding="utf-8", newline="\n") as handle,
        ):
            for raw_line in source:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    product = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    result.malformed_lines += 1
                    continue
                if not isinstance(product, dict) or "product_id" not in product:
                    result.malformed_lines += 1
                    continue

                result.products_read += 1
                candidates = product.get("images") or []
                rows = image_manifest_rows(product)
                result.images_skipped += max(0, len(candidates) - len(rows))
                if not rows:
                    result.products_without_images += 1
                    continue

                for row in rows:
                    key = str(row["storage_key"])
                    if key in seen_keys:
                        raise ValueError(
                            f"duplicate storage key {key!r}; product identifiers must be unique"
                        )
                    seen_keys.add(key)
                    handle.write(_dump(row) + "\n")
                    result.images_written += 1
                    result.by_resolution[str(row["resolution"])] += 1

        write_stage_state(connection, STAGE_IMAGES, "complete", cap, records=result.images_written)
        _print_image_summary(result, config)
        return result
    finally:
        connection.close()


def _print_image_summary(result: ImageManifestResult, config: PipelineConfig) -> None:
    print("")
    print(f"stage 3 (images) -> {config.images_manifest_jsonl}")
    print(f"  products read           {result.products_read:,}")
    print(f"  image URLs written      {result.images_written:,}")
    print(f"  images skipped          {result.images_skipped:,}")
    print(f"  products with no image  {result.products_without_images:,}")
    print(f"  malformed lines         {result.malformed_lines:,}")
    for resolution in IMAGE_RESOLUTION_ORDER:
        print(f"    {resolution:<20} {result.by_resolution.get(resolution, 0):,}")
    print("  downloaded              0 (this stage never fetches; see the downloader task)")


# ---------------------------------------------------------------------------
# Stage 4 -- deterministic synthetic offers
# ---------------------------------------------------------------------------


def _seeded_int(parent_asin: str, salt: str, minimum: int, maximum: int) -> int:
    """Return a stable integer in an inclusive range from a salted SHA-256 seed."""
    if minimum > maximum:
        raise ValueError("minimum cannot exceed maximum")
    digest = hashlib.sha256(f"{parent_asin}\0{salt}".encode()).digest()
    return minimum + int.from_bytes(digest[:8], "big") % (maximum - minimum + 1)


def build_offer(product: Mapping[str, Any]) -> JsonDict:
    """Create an INR offer from the recorded USD price at the fixed demo rate."""
    provenance = product.get("provenance")
    if not isinstance(provenance, Mapping) or not isinstance(provenance.get("parent_asin"), str):
        raise ValueError("product lacks provenance.parent_asin")
    parent_asin = provenance["parent_asin"]
    subcategory = str(product.get("category_id", UNCATEGORIZED))
    source_price = product.get("source_price")
    if not isinstance(source_price, Mapping):
        raise ValueError("product lacks a usable source_price")
    if source_price.get("currency") != "USD":
        raise ValueError("product source_price currency must be USD")
    amount_minor = source_price.get("amount_minor")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor <= 0:
        raise ValueError("product source_price.amount_minor must be a positive integer")
    product_id = str(product["product_id"])
    return {
        "offer_id": "off_" + hashlib.sha256(parent_asin.encode("utf-8")).hexdigest()[:20],
        "product_id": product_id,
        "external_product_id": parent_asin,
        "merchant_id": DEMO_MERCHANT_ID,
        "category_id": subcategory,
        "status": "active",
        "unit_price_minor": amount_minor * DATASET_USD_TO_INR_RATE,
        "currency": "INR",
        "delivery_days": _seeded_int(parent_asin, "delivery_days", 1, 8),
        "return_period_days": _seeded_int(parent_asin, "return_period_days", 7, 30),
        "available_quantity": _seeded_int(parent_asin, "available_quantity", 1, 25),
        "reserved_quantity": 0,
        "offer_version": 1,
        "pricing_source": "amazon_reviews_2023_usd_fx_100",
        # It is fixed rather than generated from wall time so a rerun has the
        # same bytes. Task 12 turns this artifact into versioned live offers.
        "expires_at": "2030-12-31T23:59:59Z",
    }


@dataclass
class OfferGenerationResult:
    products_read: int = 0
    offers_written: int = 0
    malformed_lines: int = 0
    already_complete: bool = False


def stage_offers(
    config: PipelineConfig, *, force: bool = False, max_lines: int | None = None
) -> OfferGenerationResult:
    """Stage 4: write byte-reproducible generated offers from selected products."""
    _assert_outputs_outside_raw(config)
    cap = config.max_lines_debug if max_lines is None else max_lines
    result = OfferGenerationResult()
    if not config.products_jsonl.is_file():
        raise FileNotFoundError(f"{config.products_jsonl} does not exist; run 'select' first")

    connection = connect(config.candidates_db)
    try:
        ensure_schema(connection)
        if not force and _stage_is_complete(connection, STAGE_OFFERS, cap, config.offers_jsonl):
            result.already_complete = True
            state = read_stage_state(connection, STAGE_OFFERS)
            result.offers_written = int(state["records"]) if state is not None else 0
            return result
        write_stage_state(connection, STAGE_OFFERS, "running", cap)
        with (
            config.products_jsonl.open("r", encoding="utf-8") as source,
            config.offers_jsonl.open("w", encoding="utf-8", newline="\n") as destination,
        ):
            for raw_line in source:
                try:
                    product = json.loads(raw_line)
                    if not isinstance(product, dict):
                        raise ValueError("product must be an object")
                    offer = build_offer(product)
                except (json.JSONDecodeError, ValueError):
                    result.malformed_lines += 1
                    continue
                result.products_read += 1
                destination.write(_dump(offer) + "\n")
                result.offers_written += 1
        write_stage_state(connection, STAGE_OFFERS, "complete", cap, records=result.offers_written)
        return result
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Stage 5 -- selected-review linking
# ---------------------------------------------------------------------------


_REVIEW_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS review (
        review_id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL,
        parent_asin TEXT NOT NULL,
        rating INTEGER,
        title TEXT,
        body TEXT,
        verified_purchase INTEGER,
        reviewed_at TEXT,
        source_file TEXT NOT NULL,
        raw_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_review_parent_asin ON review (parent_asin)",
)


def _review_identifier(parent_asin: str, record: Mapping[str, Any]) -> str:
    """Stable ID even when a source does not expose a review identifier."""
    source_id = record.get("review_id") or record.get("asin") or ""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(f"{parent_asin}\0{source_id}\0{canonical}".encode()).hexdigest()
    return "rev_" + digest[:24]


def _selected_products(path: Path) -> dict[str, str]:
    """Load the bounded selected parent-id set before streaming raw reviews."""
    selected: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as source:
        for raw_line in source:
            try:
                product = json.loads(raw_line)
                provenance = product.get("provenance", {})
                parent_asin = provenance.get("parent_asin")
                product_id = product.get("product_id")
            except (AttributeError, json.JSONDecodeError):
                continue
            if isinstance(parent_asin, str) and isinstance(product_id, str):
                selected[parent_asin] = product_id
    return selected


@dataclass
class ReviewLinkResult:
    reviews_seen: int = 0
    reviews_linked: int = 0
    reviews_discarded: int = 0
    malformed_lines: int = 0
    already_complete: bool = False


def stage_reviews(
    config: PipelineConfig, *, force: bool = False, max_lines: int | None = None
) -> ReviewLinkResult:
    """Stage 5: stream only reviews belonging to the selected product set."""
    _assert_outputs_outside_raw(config)
    cap = config.max_lines_debug if max_lines is None else max_lines
    if not config.products_jsonl.is_file():
        raise FileNotFoundError(f"{config.products_jsonl} does not exist; run 'select' first")
    selected = _selected_products(config.products_jsonl)
    result = ReviewLinkResult()
    state_connection = connect(config.candidates_db)
    connection = sqlite3.connect(config.reviews_db)
    try:
        ensure_schema(state_connection)
        for statement in _REVIEW_SCHEMA:
            connection.execute(statement)
        if not force and _stage_is_complete(
            state_connection, STAGE_REVIEWS, cap, config.reviews_db
        ):
            result.already_complete = True
            result.reviews_linked = int(
                connection.execute("SELECT COUNT(*) FROM review").fetchone()[0]
            )
            return result
        write_stage_state(state_connection, STAGE_REVIEWS, "running", cap)
        # There is no source-progress table for reviews: a second pass is cheap
        # compared with retaining hundreds of millions of raw records. Clear a
        # partial attempt and rebuild only the bounded selected subset.
        if force or connection.execute("SELECT COUNT(*) FROM review").fetchone()[0] > 0:
            connection.execute("DELETE FROM review")
            connection.commit()

        for source_name in REVIEW_SOURCES:
            source_path = config.raw_dir / source_name
            if not source_path.is_file():
                continue
            stats = ScanStats()
            for record in iter_jsonl_gz(source_path, max_lines=cap, stats=stats):
                result.reviews_seen += 1
                parent_asin = record.get("parent_asin")
                if not isinstance(parent_asin, str) or parent_asin not in selected:
                    result.reviews_discarded += 1
                    continue
                review_id = _review_identifier(parent_asin, record)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO review
                        (review_id, product_id, parent_asin, rating, title, body,
                         verified_purchase, reviewed_at, source_file, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        review_id,
                        selected[parent_asin],
                        parent_asin,
                        _as_int(record.get("rating")) if record.get("rating") is not None else None,
                        _as_optional_str(record.get("title")),
                        _as_optional_str(record.get("text")),
                        int(bool(record.get("verified_purchase"))),
                        _as_optional_str(record.get("timestamp")),
                        source_name,
                        _dump(record),
                    ),
                )
                result.reviews_linked += cursor.rowcount
            result.malformed_lines += stats.malformed
        connection.commit()
        write_stage_state(
            state_connection, STAGE_REVIEWS, "complete", cap, records=result.reviews_linked
        )
        return result
    finally:
        connection.close()
        state_connection.close()


# ---------------------------------------------------------------------------
# Stage 6 -- computed catalog health report
# ---------------------------------------------------------------------------


def _jsonl_records(path: Path) -> Iterator[JsonDict]:
    with path.open("r", encoding="utf-8") as source:
        for raw_line in source:
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def build_quality_report(config: PipelineConfig) -> JsonDict:
    """Compute all reported health figures directly from produced artifacts."""
    products = list(_jsonl_records(config.products_jsonl))
    offers = list(_jsonl_records(config.offers_jsonl))
    category_counts = Counter(str(product.get("category_id", "unknown")) for product in products)
    status_counts = Counter(str(product.get("status", "unknown")) for product in products)
    missing_description = sum(not product.get("description") for product in products)
    review_count = 0
    if config.reviews_db.is_file():
        connection = sqlite3.connect(config.reviews_db)
        try:
            review_count = int(connection.execute("SELECT COUNT(*) FROM review").fetchone()[0])
        finally:
            connection.close()
    return {
        "configured_target_total": CATALOG_TARGET_TOTAL,
        "achieved_product_total": len(products),
        "product_count_by_subcategory": dict(sorted(category_counts.items())),
        "product_count_by_status": dict(sorted(status_counts.items())),
        "missing_description_count": missing_description,
        "generated_offer_count": len(offers),
        "linked_review_count": review_count,
    }


def stage_report(
    config: PipelineConfig, *, force: bool = False, max_lines: int | None = None
) -> JsonDict:
    """Stage 6: write the health report from real artifacts, never user input."""
    _assert_outputs_outside_raw(config)
    cap = config.max_lines_debug if max_lines is None else max_lines
    if not config.products_jsonl.is_file() or not config.offers_jsonl.is_file():
        raise FileNotFoundError("products.jsonl and offers.jsonl are required before report")
    connection = connect(config.candidates_db)
    try:
        ensure_schema(connection)
        if not force and _stage_is_complete(
            connection, STAGE_REPORT, cap, config.quality_report_json
        ):
            return json.loads(config.quality_report_json.read_text(encoding="utf-8"))
        write_stage_state(connection, STAGE_REPORT, "running", cap)
        report = build_quality_report(config)
        config.quality_report_json.write_text(_dump(report) + "\n", encoding="utf-8")
        write_stage_state(
            connection, STAGE_REPORT, "complete", cap, records=report["achieved_product_total"]
        )
        return report
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

StageHandler = Callable[[PipelineConfig, argparse.Namespace], int]


def _run_products(config: PipelineConfig, args: argparse.Namespace) -> int:
    stage_products(
        config,
        force=bool(getattr(args, "force", False)),
        max_lines=getattr(args, "max_lines", None),
    )
    return 0


def _run_select(config: PipelineConfig, args: argparse.Namespace) -> int:
    stage_select(
        config,
        force=bool(getattr(args, "force", False)),
        max_lines=getattr(args, "max_lines", None),
    )
    return 0


def _run_images(config: PipelineConfig, args: argparse.Namespace) -> int:
    stage_images(
        config,
        force=bool(getattr(args, "force", False)),
        max_lines=getattr(args, "max_lines", None),
    )
    return 0


def _run_offers(config: PipelineConfig, args: argparse.Namespace) -> int:
    stage_offers(
        config,
        force=bool(getattr(args, "force", False)),
        max_lines=getattr(args, "max_lines", None),
    )
    return 0


def _run_reviews(config: PipelineConfig, args: argparse.Namespace) -> int:
    stage_reviews(
        config,
        force=bool(getattr(args, "force", False)),
        max_lines=getattr(args, "max_lines", None),
    )
    return 0


def _run_report(config: PipelineConfig, args: argparse.Namespace) -> int:
    stage_report(
        config,
        force=bool(getattr(args, "force", False)),
        max_lines=getattr(args, "max_lines", None),
    )
    return 0


#: All six stage handlers.
STAGE_HANDLERS: dict[str, StageHandler] = {
    STAGE_PRODUCTS: _run_products,
    STAGE_SELECT: _run_select,
    STAGE_IMAGES: _run_images,
    STAGE_OFFERS: _run_offers,
    STAGE_REVIEWS: _run_reviews,
    STAGE_REPORT: _run_report,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.build_catalog",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    subparsers = parser.add_subparsers(dest="stage", required=True)

    for stage in STAGE_ORDER:
        stage_parser = subparsers.add_parser(stage, help=f"run stage {stage}")
        _add_common_arguments(stage_parser)

    all_parser = subparsers.add_parser("all", help="run every implemented stage in order")
    _add_common_arguments(all_parser)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force",
        action="store_true",
        help="rescan even if the stage is already marked complete",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=None,
        metavar="N",
        help="cap lines read per source file, overriding MAX_LINES_DEBUG",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    stages = STAGE_ORDER if args.stage == "all" else (args.stage,)

    exit_code = 0
    for stage in stages:
        handler = STAGE_HANDLERS.get(stage)
        if handler is None:
            message = f"stage '{stage}' is not implemented"
            if args.stage == "all":
                print(f"  - {message}")
                continue
            print(message, file=sys.stderr)
            return 2
        exit_code = handler(config, args) or exit_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
