"""Create the six tables the ORM declares but no migration ever built.

Revision ID: 20260821_0006
Revises: 20260821_0005

How this was found
------------------
While fixing ``provider_event`` (0005) the live webhook failed again, this time with::

    UndefinedTable: relation "failed_webhook" does not exist

``failed_webhook`` is the dead-letter queue every webhook is written to on failure, and
it is imported by ``services/payments/models.py`` alongside the tables that do exist.
It was never created by any revision. Comparing ORM metadata against the live schema
found the other five:

    failed_webhook        staging_catalog_raw    catalog_import
    ingestion_runs        staging_rejections     catalog_import_row

All six are referenced by live code paths -- the webhook dead-letter writer, the console
CSV import, and the two operator scripts -- so each was a latent runtime failure, not
dead weight. ``failed_webhook`` was the one on the payment path.

The root cause
--------------
``infra/migrations/env.py`` set ``target_metadata = Base.metadata`` but never imported a
single model module. ``Base`` is only a registry, so it was empty, and
``--autogenerate`` was comparing nothing against the database. The first symptom was
that autogenerate proposed *dropping all 36 tables*; with the env fixed, it correctly
proposed these six additions. That fix is in the same commit as this revision.

So the DDL below is machine-generated from the ORM rather than hand-written, which is
the point: it cannot disagree with the models. It was extracted from an autogenerate
run, and everything that run also proposed was deliberately discarded:

* ``TEXT`` -> ``String()`` type changes on every text column. PostgreSQL reports
  ``text`` and SQLAlchemy's ``String()`` both render as TEXT, so these are reflection
  noise, not real changes. Applying them would rewrite the type of every column in the
  schema.
* "removed table" entries for ``agent_run``, ``audit_event``, ``evidence``,
  ``negotiation_round``, ``product_embedding``, ``recommendation``, ``research_session``,
  ``tool_call``. These eight tables exist in the database and are read by raw SQL, but
  have no ORM mapping. Autogenerate therefore sees them as surplus and would drop them.
  They are left alone.

Revision ID: 20260821_0006
Revises: 20260821_0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260821_0006"
down_revision: str | None = "20260821_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Created by this revision, in dependency order. ``downgrade`` iterates this reversed,
#: so a table is always dropped after the ones that reference it.
_NEW_TABLES: tuple[str, ...] = (
    "failed_webhook",
    "ingestion_runs",
    "staging_catalog_raw",
    "staging_rejections",
    "catalog_import",
    "catalog_import_row",
)


def _table_exists(name: str) -> bool:
    import sqlalchemy as sa_inspect

    return sa_inspect.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    # The dead-letter queue. Every webhook that fails after a valid signature lands
    # here, so its absence turned "we could not apply this payment event" into a 500
    # instead of a recorded, replayable failure.
    #
    # next_retry_at is indexed because the worker polls on it constantly; without the
    # index each tick scans the whole table.
    if not _table_exists("failed_webhook"):
        op.create_table(
            "failed_webhook",
            sa.Column("failed_webhook_id", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("signature", sa.String(), nullable=True),
            sa.Column("raw_body_hash", sa.String(), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_by", sa.String(), nullable=True),
            sa.Column("resolution_note", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("failed_webhook_id"),
        )
        op.create_index(
            "ix_failed_webhook_retry",
            "failed_webhook",
            ["status", "next_retry_at"],
        )
        op.create_index(
            "ix_failed_webhook_raw_body_hash",
            "failed_webhook",
            ["raw_body_hash"],
        )

    # Ingestion bookkeeping for `scripts/import_catalog_staging.py`.
    if not _table_exists("ingestion_runs"):
        op.create_table(
            "ingestion_runs",
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("source_name", sa.String(), nullable=False),
            sa.Column("source_file", sa.String(), nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("records_seen", sa.Integer(), nullable=False),
            sa.Column("records_parsed", sa.Integer(), nullable=False),
            sa.Column("records_failed", sa.Integer(), nullable=False),
            sa.Column("records_valid", sa.Integer(), nullable=False),
            sa.Column("records_rejected", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.PrimaryKeyConstraint("run_id"),
        )

    # Quarantine landing zone. payload_hash is unique in spirit -- it is what makes an
    # import idempotent -- but it is indexed rather than unique because the operator
    # script re-runs on the same file and relies on being able to store the duplicate
    # for inspection.
    if not _table_exists("staging_catalog_raw"):
        op.create_table(
            "staging_catalog_raw",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("source_category", sa.String(), nullable=False),
            sa.Column("source_file", sa.String(), nullable=False),
            sa.Column("source_row_number", sa.Integer(), nullable=False),
            sa.Column("source_record_id", sa.String(), nullable=True),
            sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("ingestion_run_id", sa.String(), nullable=False),
            sa.Column("payload_hash", sa.String(), nullable=False),
            sa.Column("parse_status", sa.String(), nullable=False),
            sa.Column("validation_status", sa.String(), nullable=False),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_staging_catalog_raw_ingestion_run_id",
            "staging_catalog_raw",
            ["ingestion_run_id"],
        )
        op.create_index(
            "ix_staging_catalog_raw_payload_hash",
            "staging_catalog_raw",
            ["payload_hash"],
        )
        op.create_index(
            "ix_staging_catalog_raw_source_record_id",
            "staging_catalog_raw",
            ["source_record_id"],
        )

    if not _table_exists("staging_rejections"):
        op.create_table(
            "staging_rejections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ingestion_run_id", sa.String(), nullable=False),
            sa.Column("source_row_number", sa.Integer(), nullable=False),
            sa.Column("reason_code", sa.String(), nullable=False),
            sa.Column("reason_details", sa.Text(), nullable=True),
            sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_staging_rejections_ingestion_run_id",
            "staging_rejections",
            ["ingestion_run_id"],
        )

    # Console CSV import: a header row, then per-row validation results.
    if not _table_exists("catalog_import"):
        op.create_table(
            "catalog_import",
            sa.Column("import_id", sa.String(), nullable=False),
            sa.Column("merchant_id", sa.String(), nullable=False),
            sa.Column("filename", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("total_rows", sa.Integer(), nullable=False),
            sa.Column("valid_rows", sa.Integer(), nullable=False),
            sa.Column("invalid_rows", sa.Integer(), nullable=False),
            sa.Column("error_summary", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_catalog_version_id", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["merchant_id"], ["merchant.merchant_id"]),
            sa.ForeignKeyConstraint(
                ["published_catalog_version_id"],
                ["catalog_version.catalog_version_id"],
            ),
            sa.PrimaryKeyConstraint("import_id"),
        )
        # The console lists imports newest-first per merchant.
        op.create_index(
            "ix_catalog_import_merchant_created",
            "catalog_import",
            ["merchant_id", "created_at"],
        )

    if not _table_exists("catalog_import_row"):
        op.create_table(
            "catalog_import_row",
            sa.Column("row_id", sa.String(), nullable=False),
            sa.Column("import_id", sa.String(), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("sku", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("price_minor", sa.Integer(), nullable=True),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("inventory", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("image_url", sa.String(), nullable=True),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("is_valid", sa.Boolean(), nullable=False),
            sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("delivery_days", sa.Integer(), nullable=True),
            sa.Column("return_period_days", sa.Integer(), nullable=True),
            sa.Column("offer_id", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["import_id"], ["catalog_import.import_id"]),
            sa.PrimaryKeyConstraint("row_id"),
        )
        # Rows are always read as one ordered page, which is exactly what this
        # composite index serves; a limit/offset over an unordered scan would sort the
        # whole table per request.
        op.create_index(
            "ix_catalog_import_row_import_row",
            "catalog_import_row",
            ["import_id", "row_number"],
        )


def downgrade() -> None:
    # Reverse dependency order, and drop indexes with their tables so a partially
    # populated database does not leave an orphan index behind a dropped table.
    op.execute("DROP INDEX IF EXISTS ix_catalog_import_row_import_row")
    op.execute("DROP INDEX IF EXISTS ix_catalog_import_merchant_created")
    op.execute("DROP INDEX IF EXISTS ix_staging_rejections_ingestion_run_id")
    op.execute("DROP INDEX IF EXISTS ix_staging_catalog_raw_source_record_id")
    op.execute("DROP INDEX IF EXISTS ix_staging_catalog_raw_payload_hash")
    op.execute("DROP INDEX IF EXISTS ix_staging_catalog_raw_ingestion_run_id")
    op.execute("DROP INDEX IF EXISTS ix_failed_webhook_raw_body_hash")
    op.execute("DROP INDEX IF EXISTS ix_failed_webhook_retry")

    for table in reversed(_NEW_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
