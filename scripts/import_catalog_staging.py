"""Streaming raw catalog importer into staging quarantine (Section 7, 8, 9).

Streams compressed .jsonl.gz line by line, computes SHA-256 payload hashes,
executes batch inserts (500-2,000 per transaction), tracks ingestion runs,
and produces a comprehensive CSV quality report.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.observability.context import new_id
from services.db import Base, get_engine
from services.db.session import get_session_factory
from services.staging.models import IngestionRun, StagingCatalogRaw, StagingRejection


def compute_hash(raw_str: str) -> str:
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


class QualityTracker:
    """Accumulates data quality statistics during streaming ingestion."""

    def __init__(self, category: str, source_file: str) -> None:
        self.category = category
        self.source_file = source_file
        self.records_seen = 0
        self.records_parsed = 0
        self.records_failed = 0
        self.records_valid = 0
        self.records_rejected = 0

        self.seen_ids: set[str] = set()
        self.duplicate_ids = 0

        self.with_id_count = 0
        self.with_title_count = 0
        self.with_description_count = 0
        self.with_price_count = 0
        self.with_images_count = 0
        self.with_categories_count = 0

        self.fallback_id_count = 0
        self.fallback_title_count = 0

        # Field presence mapping
        self.field_counts: dict[str, int] = {}

    def observe(
        self, record: dict[str, Any], raw_line: str
    ) -> tuple[str | None, str, str | None, str | None]:
        self.records_seen += 1
        self.records_parsed += 1

        # Update field frequencies
        for k in record:
            self.field_counts[k] = self.field_counts.get(k, 0) + 1

        # Extract external ID (with fallback tracking)
        extracted_id = (
            record.get("parent_asin")
            or record.get("asin")
            or record.get("external_product_id")
            or record.get("id")
            or record.get("product_id")
            or record.get("item_id")
        )
        if record.get("parent_asin"):
            pass
        elif (
            record.get("asin")
            or record.get("external_product_id")
            or record.get("id")
            or record.get("product_id")
        ):
            self.fallback_id_count += 1

        if extracted_id:
            self.with_id_count += 1
            if extracted_id in self.seen_ids:
                self.duplicate_ids += 1
            else:
                self.seen_ids.add(extracted_id)

        # Extract title (with fallback tracking)
        title = record.get("title") or record.get("name") or record.get("product_title")
        if record.get("title"):
            pass
        elif record.get("name"):
            self.fallback_title_count += 1

        if title:
            self.with_title_count += 1

        # Check description
        desc = record.get("description") or record.get("feature") or record.get("details")
        if desc:
            self.with_description_count += 1

        # Check price
        price = record.get("price") or record.get("final_price") or record.get("list_price")
        if price is not None and str(price).strip():
            self.with_price_count += 1

        # Check images
        images = (
            record.get("images")
            or record.get("image_urls")
            or record.get("high_res_images")
            or record.get("large_images")
        )
        if images and isinstance(images, list) and len(images) > 0:
            self.with_images_count += 1

        # Check categories
        cats = record.get("categories") or record.get("category")
        if cats:
            self.with_categories_count += 1

        # Validation status decision
        if not extracted_id:
            self.records_rejected += 1
            return (
                None,
                "rejected",
                "MISSING_ID",
                "Record contains no ASIN, parent_asin, or product ID.",
            )
        if not title:
            self.records_rejected += 1
            return (
                extracted_id,
                "rejected",
                "MISSING_TITLE",
                "Record contains no title or product name.",
            )

        self.records_valid += 1
        return extracted_id, "valid", None, None

    def export_summary(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "source_file": self.source_file,
            "records_seen": self.records_seen,
            "records_parsed": self.records_parsed,
            "records_failed": self.records_failed,
            "records_valid": self.records_valid,
            "records_rejected": self.records_rejected,
            "records_with_id": self.with_id_count,
            "records_with_title": self.with_title_count,
            "records_with_description": self.with_description_count,
            "records_with_price": self.with_price_count,
            "records_with_images": self.with_images_count,
            "records_with_categories": self.with_categories_count,
            "duplicate_ids": self.duplicate_ids,
            "fallback_id_count": self.fallback_id_count,
            "fallback_title_count": self.fallback_title_count,
        }


def stream_import_file(
    file_path: Path,
    category: str,
    batch_size: int = 1000,
    max_records: int | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    engine = get_engine()
    Base.metadata.create_all(engine)
    SessionLocal = get_session_factory()

    run_id = new_id("run")
    start_time = time.perf_counter()
    start_dt = datetime.now(UTC)

    print(f"\n--- Ingesting {file_path.name} (Category: {category}, Run ID: {run_id}) ---")

    tracker = QualityTracker(category=category, source_file=file_path.name)

    with SessionLocal() as session:
        run_record = IngestionRun(
            run_id=run_id,
            source_file=file_path.name,
            category=category,
            started_at=start_dt,
            status="running",
        )
        session.add(run_record)
        session.commit()

    staging_batch: list[StagingCatalogRaw] = []
    rejection_batch: list[StagingRejection] = []
    row_number = 0

    open_fn = gzip.open if file_path.name.endswith(".gz") else open
    mode = "rt" if file_path.name.endswith(".gz") else "r"

    with open_fn(file_path, mode, encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue

            row_number += 1
            if max_records and row_number > max_records:
                break

            try:
                record = json.loads(stripped)
                extracted_id, v_status, err_code, err_msg = tracker.observe(record, stripped)

                payload_hash = compute_hash(stripped)
                stage_row = StagingCatalogRaw(
                    id=new_id("stg"),
                    source_category=category,
                    source_file=file_path.name,
                    source_row_number=row_number,
                    source_record_id=extracted_id,
                    raw_payload=record,
                    ingestion_run_id=run_id,
                    payload_hash=payload_hash,
                    parse_status="parsed",
                    validation_status=v_status,
                    error_code=err_code,
                    error_message=err_msg,
                    created_at=datetime.now(UTC),
                )
                staging_batch.append(stage_row)

                if v_status == "rejected":
                    rej = StagingRejection(
                        id=new_id("rej"),
                        ingestion_run_id=run_id,
                        source_row_number=row_number,
                        reason_code=err_code or "UNKNOWN",
                        reason_details=err_msg,
                        raw_payload=record,
                        created_at=datetime.now(UTC),
                    )
                    rejection_batch.append(rej)

            except Exception as parse_exc:
                tracker.records_seen += 1
                tracker.records_failed += 1
                rej = StagingRejection(
                    id=new_id("rej"),
                    ingestion_run_id=run_id,
                    source_row_number=row_number,
                    reason_code="MALFORMED_JSON",
                    reason_details=str(parse_exc),
                    raw_payload={"raw_line": stripped[:500]},
                    created_at=datetime.now(UTC),
                )
                rejection_batch.append(rej)

            # Flush batch
            if len(staging_batch) >= batch_size:
                with SessionLocal() as session:
                    session.bulk_save_objects(staging_batch)
                    if rejection_batch:
                        session.bulk_save_objects(rejection_batch)
                    session.commit()
                print(
                    f"   * Staged {row_number} rows (Valid: {tracker.records_valid}, Rejected: {tracker.records_rejected})..."
                )
                staging_batch.clear()
                rejection_batch.clear()

        # Flush remaining
        if staging_batch or rejection_batch:
            with SessionLocal() as session:
                if staging_batch:
                    session.bulk_save_objects(staging_batch)
                if rejection_batch:
                    session.bulk_save_objects(rejection_batch)
                session.commit()
            staging_batch.clear()
            rejection_batch.clear()

    duration_ms = int((time.perf_counter() - start_time) * 1000.0)
    finished_dt = datetime.now(UTC)

    # Finalize Ingestion Run record
    with SessionLocal() as session:
        run = session.query(IngestionRun).filter(IngestionRun.run_id == run_id).first()
        if run:
            run.finished_at = finished_dt
            run.status = "completed"
            run.records_seen = tracker.records_seen
            run.records_parsed = tracker.records_parsed
            run.records_failed = tracker.records_failed
            run.records_valid = tracker.records_valid
            run.records_rejected = tracker.records_rejected
            run.duration_ms = duration_ms
            session.commit()

    summary = tracker.export_summary()
    summary["duration_ms"] = duration_ms
    summary["run_id"] = run_id

    print(f"Finished {file_path.name} in {duration_ms}ms:")
    print(f"   * Total Seen: {tracker.records_seen}")
    print(
        f"   * Valid: {tracker.records_valid} ({tracker.records_valid/max(1, tracker.records_seen)*100:.1f}%)"
    )
    print(f"   * Rejected: {tracker.records_rejected}")
    print(f"   * Duplicates: {tracker.duplicate_ids}")

    # Generate CSV Quality Report
    if report_dir:
        report_dir.mkdir(parents=True, exist_ok=True)
        report_csv = report_dir / "ingestion_quality_report.csv"

        file_exists = report_csv.exists()
        with open(report_csv, "a", newline="", encoding="utf-8") as rf:
            writer = csv.DictWriter(rf, fieldnames=list(summary.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(summary)

        field_report_csv = report_dir / f"field_presence_{category}.csv"
        with open(field_report_csv, "w", newline="", encoding="utf-8") as ff:
            fwriter = csv.writer(ff)
            fwriter.writerow(["field_name", "present_count", "missing_count", "percentage_present"])
            for fname, pcount in sorted(
                tracker.field_counts.items(), key=lambda x: x[1], reverse=True
            ):
                mcount = tracker.records_seen - pcount
                pct = round((pcount / max(1, tracker.records_seen)) * 100.0, 2)
                fwriter.writerow([fname, pcount, mcount, pct])

        print(f"   * Quality Report written to {report_csv}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Streaming Catalog Staging Importer")
    parser.add_argument(
        "--input-file", type=Path, required=True, help="Path to .jsonl or .jsonl.gz file"
    )
    parser.add_argument("--category", type=str, default="electronics", help="Category name")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for DB inserts")
    parser.add_argument("--max-records", type=int, default=None, help="Cap max records to stage")
    parser.add_argument(
        "--report-dir", type=Path, default=Path("data/reports"), help="Report output directory"
    )
    args = parser.parse_args()

    stream_import_file(
        file_path=args.input_file,
        category=args.category,
        batch_size=args.batch_size,
        max_records=args.max_records,
        report_dir=args.report_dir,
    )


if __name__ == "__main__":
    main()
