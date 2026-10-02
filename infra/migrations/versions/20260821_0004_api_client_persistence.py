"""Persist the columns the API key mapping needs, and index the console listing.

Revision ID: 20260821_0004
Revises: 20260821_0003

Why a schema change after all
-----------------------------
The previous draft of this revision was empty, on the reasoning that
``api_client`` already existed. It does -- but with five columns, and the mapping
in ``services/connectors/models.py`` declares nine. The three that were missing
are not optional:

* ``label`` -- the operator-visible name. Without it the console lists keys by
  digest, so "which agent is this?" is unanswerable.
* ``role`` and ``buyer_id`` -- what the key is *for*. A buyer client is
  meaningless without the buyer it acts as, and a role is the ceiling on what the
  issued token can do.

So this revision adds them rather than pretending the table was already right.
``label``, ``role``, and ``buyer_id`` are all nullable or defaulted, so existing
rows stay valid and no data is rewritten; only the default for a *new* row is
meaningful, and that comes from the mint path.

Alembic stamps a revision id, so this cannot be folded into ``0003`` -- an
environment that already ran ``0003`` would keep the old five-column table
forever, invisibly, and fail every mint with a 503 from an undefined column.
That is the same trap ``20260821_0002_idempotency_key_scope`` documents.

The CHECK on ``role`` is deliberately absent. Roles are a Python enum
(``packages.security.principals.Role``) and a database constraint would have to
be edited every time one is added, which is how the two drift apart. A row
carrying an unrecognised role is refused at read time by the repository, and
logged, rather than being silently dropped by the database.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0004"
down_revision: str | None = "20260821_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> set[str]:
    import sqlalchemy as sa

    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_client")}


def _add_if_missing(name: str, ddl: str) -> None:
    """Add a column only when it is absent.

    Idempotent on purpose: a database whose ``api_client`` was created by a later
    revision, or by hand, must not fail this one.
    """
    if name not in _columns():
        op.execute(ddl)


def upgrade() -> None:
    _add_if_missing(
        "label",
        "ALTER TABLE api_client ADD COLUMN label TEXT NOT NULL DEFAULT ''",
    )
    # Nullable, not defaulted to 'buyer': a merchant admin key is not a buyer
    # key, and guessing 'buyer' would silently produce a client whose role
    # ceiling is the narrowest one. The mint path always supplies the real role,
    # so only pre-existing rows land on NULL -- and those are refused at read
    # time with a log line naming them.
    _add_if_missing("role", "ALTER TABLE api_client ADD COLUMN role TEXT")
    _add_if_missing("buyer_id", "ALTER TABLE api_client ADD COLUMN buyer_id TEXT")

    # The console's "which agents does this merchant have" listing, filtered by
    # status. The unique constraint on `key_hash` already serves the token
    # exchange's lookup; this one serves the administrative read.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_client_merchant_status "
        "ON api_client (merchant_id, status)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_api_client_merchant ON api_client (merchant_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_api_client_merchant")
    op.execute("DROP INDEX IF EXISTS ix_api_client_merchant_status")
    for name in ("buyer_id", "role", "label"):
        if name in _columns():
            op.execute(f"ALTER TABLE api_client DROP COLUMN {name}")
