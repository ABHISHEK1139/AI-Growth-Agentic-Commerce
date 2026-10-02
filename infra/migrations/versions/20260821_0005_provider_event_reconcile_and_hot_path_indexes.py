"""Reconcile ``provider_event`` and index the measured hot paths.

Revision ID: 20260821_0005
Revises: 20260821_0004

What was actually wrong
-----------------------
``provider_event`` existed in the deployed database with seven columns. The ORM
declares twelve, and :class:`~services.payments.webhooks.WebhookProcessor` reads all
twelve. Every signed webhook delivery therefore failed:

    UndefinedColumn: column provider_event.provider does not exist

That is the *whole* payment-capture path. A correctly signed Razorpay callback could
never be recorded, so no payment was ever confirmed from the provider side.

The missing five -- ``provider``, ``signature``, ``payload``, ``status``,
``created_at`` -- are all declared in ``20260821_0001``, and a database built purely
from the migration chain has them. The deployed table had them absent while
``alembic_version`` still read ``20260821_0004``, so no migration had ever run against
it in a way that would notice. The drift is not a missing migration; it is a schema
that was created out of band and then stamped as current.

Why it survived everything
-------------------------
* ``20260821_0001`` uses a bare ``CREATE TABLE``, not ``IF NOT EXISTS``, so re-running
  it would have failed loudly rather than silently "fixing" anything.
* The unit suite builds tables from the ORM metadata, so the ORM and itself always
  agree and the test can never see the drift.
* No test posted a *correctly signed* webhook to a real database. Unsigned deliveries
  are rejected before the query, so the suite exercised the signature check and never
  the database access behind it.
* The OpenAPI schema, the route listing, and ``/health`` all reported healthy.

This revision therefore does three things: bring the table up to what the ORM already
declares, add the indexes the six measured ``Seq Scan`` hot paths need, and add the
regression test that compares ORM metadata to the live schema on every table.

On adding NOT NULL columns to a table that may hold rows
--------------------------------------------------------
``provider``, ``payload``, ``status``, and ``created_at`` are NOT NULL in the ORM and
each gets a default, so the backfill is written to give pre-existing rows a value that
satisfies the constraint:

* ``provider`` -> ``'unknown'``. The ORM's own ``provider_name`` defaults to ``"fake"``
  in the router, but a row that predates this revision has no truthful provider. It is
  better to record that honestly than to assert a provider that may be wrong.
* ``payload`` -> ``'{}'``. The dedup query never reads it; an empty object is honest
  about a row whose body was not retained.
* ``status`` -> ``'processed'``. Matches the DDL default in ``0001``.
* ``created_at`` -> ``now()`` (via the ``received_at`` value where available, which is
  the same instant in practice).

The defaults then remain on the column so a new insert from the ORM succeeds.

Why the indexes are shaped this way
-----------------------------------
Each one is derived from an ``EXPLAIN`` of the query that actually runs, not from a
guess about what looks reasonable. Composite column order follows the query's equality
predicates first and its range/ordering columns last, because a btree only uses later
columns usefully after the leading ones have been constrained by equality.

Revision ID: 20260821_0005
Revises: 20260821_0004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0005"
down_revision: str | None = "20260821_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    import sqlalchemy as sa

    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    """Add a column only when it is absent.

    Idempotent by design, for the same reason ``20260821_0004`` is: an environment
    whose ``provider_event`` was built correctly by the migration chain already has
    these, and must not fail here.
    """
    if column not in _columns(table):
        op.execute(ddl)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Reconcile provider_event with what the ORM already declares.
    # ------------------------------------------------------------------
    _add_column_if_missing(
        "provider_event",
        "provider",
        "ALTER TABLE provider_event ADD COLUMN provider TEXT",
    )
    _add_column_if_missing(
        "provider_event",
        "signature",
        "ALTER TABLE provider_event ADD COLUMN signature TEXT",
    )
    _add_column_if_missing(
        "provider_event",
        "payload",
        "ALTER TABLE provider_event ADD COLUMN payload JSONB",
    )
    _add_column_if_missing(
        "provider_event",
        "status",
        "ALTER TABLE provider_event ADD COLUMN status TEXT",
    )
    _add_column_if_missing(
        "provider_event",
        "created_at",
        "ALTER TABLE provider_event ADD COLUMN created_at TIMESTAMPTZ",
    )

    # Backfill before the constraints, so the constraint is added against data that
    # already satisfies it and the table is never locked for a long rewrite.
    op.execute("UPDATE provider_event SET provider = 'unknown' WHERE provider IS NULL")
    op.execute("UPDATE provider_event SET payload = '{}'::jsonb WHERE payload IS NULL")
    op.execute("UPDATE provider_event SET status = 'processed' WHERE status IS NULL")
    op.execute(
        "UPDATE provider_event SET created_at = COALESCE(received_at, now()) "
        "WHERE created_at IS NULL"
    )

    # Now enforce what the ORM declares. Using NOT VALID would avoid the full-table
    # scan, but a provider_event table is tiny (one row per payment event) and a
    # validated constraint is worth more than the microseconds.
    op.execute("ALTER TABLE provider_event ALTER COLUMN provider SET NOT NULL")
    op.execute("ALTER TABLE provider_event ALTER COLUMN payload SET NOT NULL")
    op.execute("ALTER TABLE provider_event ALTER COLUMN status SET NOT NULL")
    op.execute("ALTER TABLE provider_event ALTER COLUMN created_at SET NOT NULL")

    # Defaults, so the ORM's own insert path works without supplying them. These match
    # the values in 20260821_0001 exactly, which is what makes the two paths converge.
    op.execute("ALTER TABLE provider_event ALTER COLUMN provider SET DEFAULT 'fake'")
    op.execute("ALTER TABLE provider_event ALTER COLUMN payload SET DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE provider_event ALTER COLUMN status SET DEFAULT 'processed'")
    op.execute("ALTER TABLE provider_event ALTER COLUMN created_at SET DEFAULT now()")

    # `raw_body_hash` is NOT NULL in the ORM but was nullable in the deployed table.
    # Backfill rather than drop the constraint: a null hash cannot be used for the
    # deduplication lookup, so it is a row that can never dedupe.
    op.execute("UPDATE provider_event SET raw_body_hash = '' WHERE raw_body_hash IS NULL")
    op.execute("ALTER TABLE provider_event ALTER COLUMN raw_body_hash SET NOT NULL")

    # ------------------------------------------------------------------
    # 2. Indexes, one per measured Seq Scan.
    # ------------------------------------------------------------------

    # The webhook deduplication check (services/payments/webhooks.py:106). Measured as
    # a full table scan: "have I already processed this event?" reads every payment
    # event ever received. This is the hottest correctness path in the system, and it
    # was O(total events).
    #
    # Two predicates, OR'd together, so both branches get an index:
    #   - provider_event_id  is the primary key, already covered.
    #   - (raw_body_hash = ? AND signature = ?) is the replay check, which has no
    #     index at all. Leading column is raw_body_hash because that is the highly
    #     selective one; signature alone is a 64-char hex string, so leading with it
    #     would work too, but hash-first matches the selectivity argument and lets
    #     Postgres reuse this index for a hash-only lookup if the OR is ever simplified.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provider_event_raw_body_hash "
        "ON provider_event (raw_body_hash, signature)"
    )

    # The dead-letter worker scans by processing state. Not on the webhook request
    # path, but it grows with every failure and is queried on every worker tick.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provider_event_status_received "
        "ON provider_event (status, received_at)"
    )

    # checkout: no indexes whatsoever, including on its own primary key's companion
    # lookups. `get_checkout` filters by (checkout_id, merchant_id, buyer_id) and the
    # buyer's order history filters by (buyer_id, created_at). Both measured as full
    # scans.
    #
    # checkout_id is already the primary key, so it needs no index of its own. The
    # merchant listing (idempotency/ledger views filter by merchant_id + status) and
    # the buyer history are the ones that do.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_checkout_merchant_status "
        "ON checkout (merchant_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_checkout_buyer_created "
        "ON checkout (buyer_id, created_at DESC)"
    )
    # The price-hash lookup: "is the price the buyer was quoted still the price in the
    # catalogue?" is a read on the hot checkout path.
    op.execute("CREATE INDEX IF NOT EXISTS ix_checkout_price_hash ON checkout (price_hash)")

    # authorization: also had zero indexes despite being read by id *and* filtered by
    # (buyer_id, status) when deciding whether a buyer has standing approval.
    #
    # The table name is double-quoted because AUTHORIZATION is a reserved word in
    # PostgreSQL (pg_get_keywords: catcode 'T', "reserved (can be function or type
    # name)"). Unquoted, the statement is a syntax error -- which is exactly how this
    # migration failed the first time it ran, which is how it was found.
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_authorization_buyer_status '
        'ON "authorization" (buyer_id, status)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_authorization_checkout '
        'ON "authorization" (checkout_id)'
    )

    # audit_event: has one index on (aggregate_type, aggregate_id). The merchant
    # console's ledger lists by merchant_id + created_at, which measured as a full
    # scan and is the query a support engineer runs most.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_event_merchant_created "
        "ON audit_event (merchant_id, created_at DESC)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_event_agent_run ON audit_event (agent_run_id)")

    # agent_run: every agent conversation filters by buyer_id, and the timeline reads
    # one run by primary key (already indexed).
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_run_buyer_started ON agent_run (buyer_id, started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_agent_run_checkout ON agent_run (checkout_id)")

    # tool_call and evidence are read by agent_run_id for the run timeline and the
    # evidence bundle that supports an agent's answer. Both grew with no index.
    op.execute("CREATE INDEX IF NOT EXISTS ix_tool_call_run_created ON tool_call (agent_run_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_evidence_run ON evidence (agent_run_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_evidence_research_session ON evidence (research_session_id)"
    )


def downgrade() -> None:
    # Indexes first: they are pure additions and dropping them cannot lose data.
    for index in (
        "ix_evidence_research_session",
        "ix_evidence_run",
        "ix_tool_call_run_created",
        "ix_agent_run_checkout",
        "ix_agent_run_buyer_started",
        "ix_audit_event_agent_run",
        "ix_audit_event_merchant_created",
        "ix_authorization_checkout",
        "ix_authorization_buyer_status",
        "ix_checkout_price_hash",
        "ix_checkout_buyer_created",
        "ix_checkout_merchant_status",
        "ix_provider_event_status_received",
        "ix_provider_event_raw_body_hash",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index}")

    # Then the columns, with the constraints relaxed first: a NOT NULL constraint
    # depends on its column, and dropping the column while it is still enforced fails
    # with a dependency error rather than anything actionable.
    #
    # The backfilled values are discarded with the columns. That is only acceptable
    # because a downgraded provider_event no longer holds rows the ORM can read -- the
    # ORM would fail on the missing columns. See the module docstring.
    for column in ("raw_body_hash", "created_at", "status", "payload", "signature", "provider"):
        if column in _columns("provider_event"):
            op.execute(f"ALTER TABLE provider_event ALTER COLUMN {column} DROP NOT NULL")
            op.execute(f"ALTER TABLE provider_event ALTER COLUMN {column} DROP DEFAULT")

    for column in ("created_at", "status", "payload", "signature", "provider"):
        if column in _columns("provider_event"):
            op.execute(f"ALTER TABLE provider_event DROP COLUMN {column}")
