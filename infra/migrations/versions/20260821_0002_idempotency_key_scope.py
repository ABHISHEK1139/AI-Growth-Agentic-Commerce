"""Scope the idempotency lock by actor *type* as well as actor id.

Revision ID: 20260821_0002
Revises: 20260821_0001

Why a second revision rather than an edit to the first
------------------------------------------------------
``20260821_0001`` was rewritten after it had already been applied: its
``idempotency_record`` unique constraint was widened from three columns to four
in place. Alembic stamps a revision id, so every environment that already ran the
old file stays stamped at the same version and silently keeps the old constraint
forever -- the drift is invisible to `alembic upgrade head` and only shows up as
a failing assertion against a real database.

The fix is a new revision that converges an existing database onto the declared
schema. The widened constraint is also the correct one: the lock key is
``(actor_type, actor_id, endpoint, idempotency_key)``, so a buyer and a merchant
operator who happened to share an id would otherwise collide on one another's
in-flight lock.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0002"
down_revision: str | None = "20260821_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "uq_idempotency_record_actor_type_id_endpoint_key"
_LEGACY_CONSTRAINT_NAME = "idempotency_record_actor_id_endpoint_idempotency_key_key"


def _columns() -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return [c["name"] for c in inspector.get_columns("idempotency_record")]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # `actor_type` is the column the four-column key needs. It exists in the
    # model and in the current first revision, but a database created from an
    # earlier copy of that file may predate it, so add it rather than assume.
    if "actor_type" not in _columns():
        op.add_column(
            "idempotency_record",
            sa.Column(
                "actor_type",
                sa.String(),
                nullable=False,
                server_default="buyer",
            ),
        )

    # Drop whichever form is present before naming the new one, so this revision
    # is safe to apply to both the drifted and the already-current schema.
    existing = {
        c["name"]
        for c in inspector.get_unique_constraints("idempotency_record")
        if tuple(c["column_names"])
        in {
            ("actor_id", "endpoint", "idempotency_key"),
            ("actor_type", "actor_id", "endpoint", "idempotency_key"),
        }
    }
    for name in existing:
        op.drop_constraint(name, "idempotency_record", type_="unique")

    op.create_unique_constraint(
        _CONSTRAINT_NAME,
        "idempotency_record",
        ["actor_type", "actor_id", "endpoint", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "idempotency_record", type_="unique")
    op.create_unique_constraint(
        _LEGACY_CONSTRAINT_NAME,
        "idempotency_record",
        ["actor_id", "endpoint", "idempotency_key"],
    )
