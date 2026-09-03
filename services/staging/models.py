"""Staging quarantine and ingestion run models (Requirement: Staging Tables)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class IngestionRun(Base):
    """Tracks every execution of the raw catalog ingestion pipeline."""

    __tablename__ = "ingestion_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False, default="amazon_reviews_2023")
    source_file: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="running"
    )  # running, completed, failed
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_parsed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_valid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class StagingCatalogRaw(Base):
    """Immutable quarantine staging table holding raw unmodified payloads."""

    __tablename__ = "staging_catalog_raw"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_category: Mapped[str] = mapped_column(String, nullable=False)
    source_file: Mapped[str] = mapped_column(String, nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ingestion_run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parse_status: Mapped[str] = mapped_column(
        String, nullable=False, default="parsed"
    )  # parsed, malformed
    validation_status: Mapped[str] = mapped_column(
        String, nullable=False, default="staged"
    )  # staged, valid, rejected, promoted
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class StagingRejection(Base):
    """Quarantine table for malformed or unprocessable raw records."""

    __tablename__ = "staging_rejections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ingestion_run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    reason_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
