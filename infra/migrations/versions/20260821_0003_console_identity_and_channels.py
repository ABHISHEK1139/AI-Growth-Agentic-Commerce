"""Add console operator accounts and persisted commerce-channel connections.

Revision ID: 20260821_0003
Revises: 20260821_0002

Four tables, two concerns.

**Console identity.** The project had no human authentication: ``POST
/api/v1/auth/login`` read a ``role`` from the request body and issued a session
for it, so any anonymous caller could become ``PLATFORM_ADMIN``. There was no
table to hold a password because there was no password. ``operator_account``
supplies one, storing an Argon2id PHC string and never a plaintext.

The lockout columns (``failed_login_count``, ``last_failed_login_at``,
``locked_until``) are the reason the table is not just a credential store. The
Argon2 cost makes *offline* cracking expensive and does nothing about online
guessing, so without a lockout a ``MERCHANT_ADMIN`` is reachable at whatever rate
the endpoint will accept requests.

**Channel connections.** The connector registry was in-memory and lost every
registration on restart, so a merchant's Shopify connection existed only until
the process recycled. ``channel_connection`` persists it, and the two append-only
logs give the console something true to display: a "connected" badge is not
evidence that a sync ever succeeded.

The access token is stored encrypted, not hashed, because it must be replayed to
the store's API on every sync. That is a different threat model from a password
and the difference is why these are separate tables rather than one credential
table with a flag.

These statements are portable to SQLite as well as PostgreSQL -- no JSONB, no
partial indexes, no server-side defaults the ORM does not also declare -- so the
local development datastore gets the same schema as the compose stack.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0003"
down_revision: str | None = "20260821_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _execute(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    _execute(
        (
            """
            CREATE TABLE operator_account (
                operator_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
                failed_login_count INTEGER NOT NULL DEFAULT 0,
                last_failed_login_at TIMESTAMP,
                locked_until TIMESTAMP,
                last_login_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (role IN ('buyer', 'merchant_admin', 'merchant_operator', 'platform_admin')),
                CHECK (status IN ('active', 'disabled')),
                CHECK (failed_login_count >= 0)
            )
            """,
            # Login resolves by email, so this is the index that matters on a
            # table read by every unauthenticated-looking request. Declared
            # separately from the UNIQUE above so the name is the same on both
            # dialects: SQLite derives a different implicit index name for a
            # UNIQUE column, and a name-dependent test would pass on Postgres
            # and fail locally.
            "CREATE INDEX ix_operator_account_email ON operator_account (email)",
            "CREATE INDEX ix_operator_account_merchant ON operator_account (merchant_id)",
            """
            CREATE TABLE channel_connection (
                connection_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                platform_type TEXT NOT NULL,
                store_domain TEXT NOT NULL,
                access_token_encrypted TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                product_count INTEGER NOT NULL DEFAULT 0,
                offer_count INTEGER NOT NULL DEFAULT 0,
                last_sync_status TEXT,
                last_synced_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (status IN ('active', 'disabled')),
                CHECK (product_count >= 0 AND offer_count >= 0)
            )
            """,
            # One live connection per platform per store. Without it a merchant
            # who reconnects the same Shopify store gets every product synced
            # twice, and the catalog import reports a duplication bug rather than
            # a duplicate row.
            """
            CREATE UNIQUE INDEX uq_channel_connection_store
            ON channel_connection (merchant_id, platform_type, store_domain)
            """,
            "CREATE INDEX ix_channel_connection_merchant ON channel_connection (merchant_id)",
            """
            CREATE TABLE channel_sync_run (
                sync_run_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL REFERENCES channel_connection(connection_id),
                merchant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                product_count INTEGER NOT NULL DEFAULT 0,
                offer_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                CHECK (status IN ('running', 'success', 'partial', 'failed'))
            )
            """,
            """
            CREATE INDEX ix_channel_sync_run_connection
            ON channel_sync_run (connection_id, started_at)
            """,
            """
            CREATE TABLE channel_order_push (
                push_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL REFERENCES channel_connection(connection_id),
                merchant_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                remote_reference TEXT,
                error_message TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (status IN ('pending', 'success', 'failed'))
            )
            """,
            """
            CREATE INDEX ix_channel_order_push_connection
            ON channel_order_push (connection_id, created_at)
            """,
            # One push per order per connection. An order must reach the store
            # exactly once: a retry after a *successful* push that timed out on
            # the response would otherwise create a duplicate order in Shopify
            # for a buyer who was already charged for the first one.
            """
            CREATE UNIQUE INDEX uq_channel_order_push_once
            ON channel_order_push (connection_id, order_id)
            """,
        )
    )


def downgrade() -> None:
    _execute(
        (
            "DROP INDEX IF EXISTS uq_channel_order_push_once",
            "DROP TABLE IF EXISTS channel_order_push",
            "DROP INDEX IF EXISTS ix_channel_sync_run_connection",
            "DROP TABLE IF EXISTS channel_sync_run",
            "DROP INDEX IF EXISTS ix_channel_connection_merchant",
            "DROP INDEX IF EXISTS uq_channel_connection_store",
            "DROP TABLE IF EXISTS channel_connection",
            "DROP INDEX IF EXISTS ix_operator_account_merchant",
            "DROP INDEX IF EXISTS ix_operator_account_email",
            "DROP TABLE IF EXISTS operator_account",
        )
    )
