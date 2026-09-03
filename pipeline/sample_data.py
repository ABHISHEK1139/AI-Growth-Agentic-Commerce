"""Generate small synthetic .jsonl.gz fixture files for pipeline development.

Produces deterministic sample data in the same format that ``build_catalog``
expects, so developers can run the full pipeline without requiring the
multi-GB raw Amazon Reviews dataset.

Usage::

    python -m pipeline.sample_data                          # default output to data/raw/
    python -m pipeline.sample_data --output-dir /tmp/raw    # custom output directory
    python -m pipeline.sample_data --records 100            # records per file

Each run with the same seed produces identical output, making the sample data
suitable for reproducible testing and CI.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import string
from collections.abc import Sequence
from pathlib import Path

from pipeline.config import DEFAULT_RAW_DIR, REPO_ROOT, load_config

#: Default directory for synthetic sample data (inside data/raw/).
DEFAULT_SAMPLE_DIR = REPO_ROOT / "data" / "raw"

#: Default number of records per metadata file.
DEFAULT_RECORDS_PER_FILE = 80

#: Default number of records per review file.
DEFAULT_REVIEWS_PER_FILE = 120

#: Random seed for deterministic output.
DEFAULT_SEED = 42

#: Metadata files the pipeline reads in stage 1.
META_FILES: tuple[str, ...] = (
    "meta_Electronics.jsonl.gz",
    "meta_Cell_Phones_and_Accessories.jsonl.gz",
    "meta_Appliances.jsonl.gz",
)

#: Review files the pipeline reads in stage 5.
REVIEW_FILES: tuple[str, ...] = (
    "Electronics.jsonl.gz",
    "Cell_Phones_and_Accessories.jsonl.gz",
    "Appliances.jsonl.gz",
)

#: Product title templates per category.
ELECTRONICS_TITLES: tuple[str, ...] = (
    "Professional Gaming Laptop 15.6 inch Display",
    "Ultra HD 4K LED Monitor 27 inch",
    "Wireless Noise Cancelling Headphones",
    "Mirrorless Digital Camera Bundle",
    "Bluetooth Portable Speaker System",
    "Mechanical Gaming Keyboard RGB",
    "Wireless Optical Mouse Ergonomic",
    "USB-C Docking Station Hub",
    "External SSD Drive 1TB Portable",
    "Smart Home Security Camera Indoor",
    "HD Streaming Webcam with Microphone",
    "Laptop Cooling Pad with Fans",
    "4K Ultra HD Smart Television 55 inch",
    "Noise Cancelling Earbuds True Wireless",
    "Gaming Desktop Computer Tower",
)

PHONE_TITLES: tuple[str, ...] = (
    "Smartphone Unlocked 128GB Storage",
    "Cell Phone Protective Case Hybrid",
    "Phone Screen Protector Tempered Glass",
    "Wireless Phone Charger Stand Fast",
    "Cell Phone Car Mount Magnetic",
    "Smartphone Gimbal Stabilizer Handheld",
    "Phone Grip Ring Holder Kickstand",
    "USB-C Fast Charging Cable 6ft",
    "Bluetooth Earbuds for Phone Calls",
    "Cell Phone Signal Booster Kit",
    "Slim Phone Case Leather Wallet",
    "Phone Camera Lens Kit Wide Angle",
    "Portable Power Bank 20000mAh",
    "Phone Armband Running Workout Band",
    "SIM Card Adapter Kit Universal",
)

APPLIANCE_TITLES: tuple[str, ...] = (
    "Air Purifier HEPA Filter Large Room",
    "Programmable Coffee Maker 12 Cup",
    "Robot Vacuum Cleaner Smart Navigation",
    "Countertop Blender Smoothie Maker",
    "Portable Air Conditioner 10000 BTU",
    "Electric Pressure Cooker 6 Quart",
    "Dehumidifier for Basement 50 Pint",
    "Stand Mixer Professional Grade 500W",
    "Water Filter Pitcher Alkaline",
    "Electric Kettle Temperature Control",
    "Toaster Oven Air Fryer Combo",
    "Rice Cooker Multi-Function Digital",
    "Handheld Vacuum Cordless Lightweight",
    "Ice Maker Machine Countertop Portable",
    "Food Processor 13 Cup Work Bowl",
)

BRANDS: tuple[str, ...] = (
    "Acme",
    "TechPro",
    "SmartLife",
    "ProGear",
    "ElectraMax",
    "CoreTech",
    "VoltEdge",
    "NovaTech",
    "PrimeLine",
    "ZenithPro",
)

FEATURES_POOL: tuple[str, ...] = (
    "High-performance processor for demanding tasks",
    "Energy efficient design with low power consumption",
    "Compact and lightweight for easy portability",
    "Built-in safety features for worry-free operation",
    "Premium build quality with durable materials",
    "Easy setup with quick-start guide included",
    "Compatible with all major platforms and devices",
    "Advanced noise reduction technology",
    "Ergonomic design for comfortable extended use",
    "Backed by 2-year manufacturer warranty",
)

REVIEW_TEXTS: tuple[str, ...] = (
    "Great product, works exactly as described. Very happy with my purchase.",
    "Decent quality for the price. Shipping was fast.",
    "Not what I expected. The build quality could be better.",
    "Excellent value for money. Would recommend to anyone.",
    "Good product overall, minor issues with packaging.",
    "Works perfectly out of the box. No complaints.",
    "Stopped working after a month. Disappointing.",
    "Best purchase I have made this year. Highly recommend.",
    "Average product, nothing special but does the job.",
    "Fantastic quality and great customer service.",
)


def _random_asin(rng: random.Random) -> str:
    """Generate a realistic-looking ASIN (10 uppercase alphanumeric chars)."""
    return "B0" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=8))


def _random_price(rng: random.Random, low: float = 9.99, high: float = 999.99) -> str:
    """Generate a price string like $29.99."""
    value = round(rng.uniform(low, high), 2)
    return f"${value:.2f}"


def _random_image_set(rng: random.Random, asin: str) -> list[dict[str, str | None]]:
    """Generate 1-3 image entries for a product."""
    count = rng.randint(1, 3)
    images: list[dict[str, str | None]] = []
    for i in range(count):
        images.append(
            {
                "hi_res": f"https://example.test/images/{asin}/hi_res_{i}.jpg",
                "large": f"https://example.test/images/{asin}/large_{i}.jpg",
                "thumb": f"https://example.test/images/{asin}/thumb_{i}.jpg",
                "variant": "MAIN" if i == 0 else f"PT0{i}",
            }
        )
    return images


def _random_features(rng: random.Random, count: int = 3) -> list[str]:
    """Pick random features from the pool."""
    return rng.sample(FEATURES_POOL, min(count, len(FEATURES_POOL)))


def generate_metadata_record(
    rng: random.Random,
    titles: tuple[str, ...],
    category: str,
) -> dict[str, object]:
    """Generate a single metadata record matching the Amazon Reviews 2023 format."""
    asin = _random_asin(rng)
    title_base = rng.choice(titles)
    brand = rng.choice(BRANDS)
    title = f"{brand} {title_base}"

    return {
        "parent_asin": asin,
        "main_category": category,
        "title": title,
        "features": _random_features(rng, rng.randint(2, 5)),
        "description": [f"Premium {title_base.lower()} by {brand}. Designed for everyday use."],
        "price": _random_price(rng),
        "images": _random_image_set(rng, asin),
        "videos": [],
        "store": brand,
        "categories": [category, "Featured"],
        "details": {
            "Brand": brand,
            "Model Number": f"{brand[0:2].upper()}-{rng.randint(1000, 9999)}",
            "Item Weight": f"{rng.uniform(0.5, 15.0):.1f} pounds",
        },
        "average_rating": round(rng.uniform(2.5, 5.0), 1),
        "rating_number": rng.randint(5, 5000),
    }


def generate_review_record(
    rng: random.Random,
    parent_asin: str,
) -> dict[str, object]:
    """Generate a single review record matching the Amazon Reviews 2023 format."""
    return {
        "rating": rng.randint(1, 5),
        "title": rng.choice(REVIEW_TEXTS)[:50],
        "text": rng.choice(REVIEW_TEXTS),
        "asin": parent_asin,
        "parent_asin": parent_asin,
        "user_id": f"U{''.join(rng.choices(string.ascii_uppercase + string.digits, k=12))}",
        "timestamp": rng.randint(1600000000000, 1700000000000),
        "verified_purchase": rng.random() > 0.3,
        "helpful_vote": rng.randint(0, 50),
    }


def write_jsonl_gz(path: Path, records: list[dict[str, object]]) -> None:
    """Write records to a gzipped JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(filename="", mode="wb", fileobj=path.open("wb"), mtime=0.0) as handle:
        for record in records:
            handle.write((json.dumps(record) + "\n").encode("utf-8"))


def generate_sample_data(
    output_dir: Path,
    *,
    records_per_file: int = DEFAULT_RECORDS_PER_FILE,
    reviews_per_file: int = DEFAULT_REVIEWS_PER_FILE,
    seed: int = DEFAULT_SEED,
) -> dict[str, int]:
    """Generate all sample data files in *output_dir*.

    Guards against writing into the immutable source datasets directory (Requirement 1.2).
    Returns a mapping of filename to record count for reporting.
    """
    # Guard: Never write synthetic data into the immutable source datasets directory
    resolved_out = output_dir.resolve()
    resolved_raw = DEFAULT_RAW_DIR.resolve()
    if resolved_out == resolved_raw or resolved_raw in resolved_out.parents:
        raise ValueError(
            f"Safety guard violation: Cannot write synthetic sample data into immutable datasets directory '{output_dir}'. "
            "Specify an isolated sample directory like 'data/raw/'."
        )

    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_configs: list[tuple[str, tuple[str, ...], str]] = [
        ("meta_Electronics.jsonl.gz", ELECTRONICS_TITLES, "All Electronics"),
        ("meta_Cell_Phones_and_Accessories.jsonl.gz", PHONE_TITLES, "Cell Phones & Accessories"),
        ("meta_Appliances.jsonl.gz", APPLIANCE_TITLES, "Appliances"),
    ]

    counts: dict[str, int] = {}
    all_asins: dict[str, list[str]] = {}

    # Generate metadata files
    for filename, titles, category in file_configs:
        records = [generate_metadata_record(rng, titles, category) for _ in range(records_per_file)]
        write_jsonl_gz(output_dir / filename, records)
        counts[filename] = len(records)
        all_asins[filename] = [str(r["parent_asin"]) for r in records]
        print(f"  WROTE  {filename} ({len(records)} records)")

    # Generate review files
    review_configs: list[tuple[str, str]] = [
        ("Electronics.jsonl.gz", "meta_Electronics.jsonl.gz"),
        ("Cell_Phones_and_Accessories.jsonl.gz", "meta_Cell_Phones_and_Accessories.jsonl.gz"),
        ("Appliances.jsonl.gz", "meta_Appliances.jsonl.gz"),
    ]

    for review_filename, meta_filename in review_configs:
        asins = all_asins[meta_filename]
        records_list: list[dict[str, object]] = []
        for _ in range(reviews_per_file):
            parent_asin = rng.choice(asins)
            records_list.append(generate_review_record(rng, parent_asin))
        write_jsonl_gz(output_dir / review_filename, records_list)
        counts[review_filename] = len(records_list)
        print(f"  WROTE  {review_filename} ({len(records_list)} records)")

    return counts


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.sample_data",
        description="Generate synthetic sample data for the catalog pipeline.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Output directory for generated files (default: {DEFAULT_SAMPLE_DIR}).",
    )
    parser.add_argument(
        "--records",
        type=int,
        default=DEFAULT_RECORDS_PER_FILE,
        help=f"Number of metadata records per file (default: {DEFAULT_RECORDS_PER_FILE}).",
    )
    parser.add_argument(
        "--reviews",
        type=int,
        default=DEFAULT_REVIEWS_PER_FILE,
        help=f"Number of review records per file (default: {DEFAULT_REVIEWS_PER_FILE}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed for deterministic output (default: {DEFAULT_SEED}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    config = load_config()
    if args.output_dir is not None:
        output_dir = args.output_dir
    else:
        # Never default to the immutable datasets directory
        if config.raw_dir.resolve() == DEFAULT_RAW_DIR.resolve():
            output_dir = DEFAULT_SAMPLE_DIR
        else:
            output_dir = config.raw_dir

    print(f"Generating sample data in: {output_dir}")
    print(f"Records per metadata file: {args.records}")
    print(f"Reviews per review file: {args.reviews}")
    print(f"Seed: {args.seed}")
    print()

    counts = generate_sample_data(
        output_dir,
        records_per_file=args.records,
        reviews_per_file=args.reviews,
        seed=args.seed,
    )

    total = sum(counts.values())
    print(f"\nTotal: {total} records across {len(counts)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
