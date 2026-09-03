"""Create the commerce schema and database-enforced financial invariants.

Revision ID: 20260821_0001
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _execute(statements: Sequence[str]) -> None:
    for statement in statements:
        op.execute(statement)


def upgrade() -> None:
    # IDs are text rather than database-generated UUIDs: deterministic pipeline
    # identifiers and provider identifiers must survive imports unchanged.
    _execute(
        (
            "CREATE EXTENSION IF NOT EXISTS vector",
            """
            CREATE TABLE merchant (
                merchant_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('active', 'inactive'))
            )
            """,
            """
            CREATE TABLE buyer (
                buyer_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, display_name TEXT,
                status TEXT NOT NULL DEFAULT 'active', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('active', 'inactive'))
            )
            """,
            """
            CREATE TABLE import_run (
                import_run_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                source_name TEXT NOT NULL, source_checksum TEXT NOT NULL, schema_version TEXT NOT NULL,
                licence_note TEXT NOT NULL, status TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL,
                completed_at TIMESTAMPTZ, UNIQUE (merchant_id, source_checksum),
                CHECK (status IN ('running', 'completed', 'failed'))
            )
            """,
            """
            CREATE TABLE catalog_version (
                catalog_version_id TEXT PRIMARY KEY,
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                import_run_id TEXT REFERENCES import_run(import_run_id), status TEXT NOT NULL,
                product_count INTEGER NOT NULL DEFAULT 0 CHECK (product_count >= 0),
                valid_count INTEGER NOT NULL DEFAULT 0 CHECK (valid_count >= 0),
                needs_review_count INTEGER NOT NULL DEFAULT 0 CHECK (needs_review_count >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), published_at TIMESTAMPTZ,
                CHECK (status IN ('draft', 'validating', 'published', 'superseded'))
            )
            """,
            "CREATE UNIQUE INDEX uq_catalog_version_one_published_per_merchant "
            "ON catalog_version (merchant_id) WHERE status = 'published'",
            """
            CREATE TABLE product (
                product_id TEXT PRIMARY KEY, catalog_version_id TEXT NOT NULL REFERENCES catalog_version(catalog_version_id),
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id), external_product_id TEXT NOT NULL,
                category_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
                description JSONB NOT NULL DEFAULT '[]'::jsonb, specifications JSONB NOT NULL DEFAULT '{}'::jsonb,
                average_rating DOUBLE PRECISION NOT NULL DEFAULT 0,
                rating_number INTEGER NOT NULL DEFAULT 0 CHECK (rating_number >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (catalog_version_id, external_product_id),
                CHECK (status IN ('valid', 'needs_review', 'inactive'))
            )
            """,
            "CREATE INDEX ix_product_category_status ON product (merchant_id, category_id, status)",
            "CREATE INDEX ix_product_specifications_gin ON product USING GIN (specifications)",
            """
            CREATE TABLE variant (
                variant_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES product(product_id),
                external_variant_id TEXT, title TEXT NOT NULL, specifications JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (product_id, external_variant_id)
            )
            """,
            """
            CREATE TABLE product_image (
                product_image_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES product(product_id),
                source_url TEXT NOT NULL, storage_key TEXT NOT NULL, resolution TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0), UNIQUE (product_id, storage_key)
            )
            """,
            """
            CREATE TABLE review (
                review_id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES product(product_id),
                parent_asin TEXT NOT NULL, rating INTEGER, title TEXT, body TEXT, verified_purchase BOOLEAN,
                reviewed_at TIMESTAMPTZ, source_file TEXT NOT NULL, raw_body_hash TEXT NOT NULL
            )
            """,
            "CREATE INDEX ix_review_parent_asin ON review (parent_asin)",
            """
            CREATE TABLE product_embedding (
                product_id TEXT PRIMARY KEY REFERENCES product(product_id), model_version TEXT NOT NULL,
                embedding vector(1536) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE merchant_rules (
                merchant_id TEXT PRIMARY KEY REFERENCES merchant(merchant_id), version TEXT NOT NULL,
                max_transaction_minor BIGINT NOT NULL CHECK (max_transaction_minor >= 0),
                auto_approval_limit_minor BIGINT NOT NULL CHECK (auto_approval_limit_minor >= 0),
                max_discount_basis_points INTEGER NOT NULL CHECK (max_discount_basis_points BETWEEN 0 AND 10000),
                allowed_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
                blocked_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
                allowed_payment_methods JSONB NOT NULL DEFAULT '[]'::jsonb,
                allow_out_of_stock BOOLEAN NOT NULL DEFAULT false, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE category_pairing (
                pairing_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                source_category_id TEXT NOT NULL, target_category_id TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT true, UNIQUE (merchant_id, source_category_id, target_category_id)
            )
            """,
            """
            CREATE TABLE api_client (
                api_client_id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                key_hash TEXT NOT NULL UNIQUE, scopes JSONB NOT NULL, status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ,
                CHECK (status IN ('active', 'revoked'))
            )
            """,
            """
            CREATE TABLE buyer_policy (
                buyer_id TEXT PRIMARY KEY REFERENCES buyer(buyer_id), version TEXT NOT NULL,
                max_transaction_minor BIGINT NOT NULL CHECK (max_transaction_minor >= 0),
                auto_approval_limit_minor BIGINT NOT NULL CHECK (auto_approval_limit_minor >= 0),
                allowed_merchants JSONB NOT NULL DEFAULT '[]'::jsonb,
                allowed_categories JSONB NOT NULL DEFAULT '[]'::jsonb, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE offer (
                offer_id TEXT PRIMARY KEY, catalog_version_id TEXT NOT NULL REFERENCES catalog_version(catalog_version_id),
                product_id TEXT NOT NULL REFERENCES product(product_id), variant_id TEXT REFERENCES variant(variant_id),
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id), status TEXT NOT NULL,
                unit_price_minor BIGINT NOT NULL CHECK (unit_price_minor >= 0), currency TEXT NOT NULL,
                delivery_days INTEGER NOT NULL CHECK (delivery_days >= 0),
                return_period_days INTEGER NOT NULL CHECK (return_period_days >= 0),
                pricing_source TEXT NOT NULL, offer_version INTEGER NOT NULL CHECK (offer_version >= 1),
                expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('active', 'inactive', 'expired', 'needs_review')),
                CHECK (pricing_source IN ('synthetic_band_random', 'merchant_configured'))
            )
            """,
            "CREATE INDEX ix_offer_merchant_status ON offer (merchant_id, status)",
            "CREATE INDEX ix_offer_price ON offer (unit_price_minor)",
            """
            CREATE TABLE inventory (
                offer_id TEXT PRIMARY KEY REFERENCES offer(offer_id),
                available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
                reserved_quantity INTEGER NOT NULL DEFAULT 0 CHECK (reserved_quantity >= 0),
                version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
                CHECK (reserved_quantity <= available_quantity)
            )
            """,
            """
            CREATE TABLE checkout (
                checkout_id TEXT PRIMARY KEY, buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id),
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id), offer_id TEXT NOT NULL REFERENCES offer(offer_id),
                offer_version INTEGER NOT NULL CHECK (offer_version >= 1), status TEXT NOT NULL,
                subtotal_minor BIGINT NOT NULL CHECK (subtotal_minor >= 0),
                shipping_minor BIGINT NOT NULL CHECK (shipping_minor >= 0),
                tax_minor BIGINT NOT NULL CHECK (tax_minor >= 0), discount_minor BIGINT NOT NULL CHECK (discount_minor >= 0),
                total_minor BIGINT NOT NULL CHECK (total_minor >= 0), currency TEXT NOT NULL, price_hash TEXT NOT NULL,
                price_snapshot JSONB NOT NULL, expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('created', 'policy_checked', 'authorization_pending', 'authorized', 'cancelled', 'expired', 'price_changed', 'inventory_changed', 'completed', 'payment_failed', 'policy_blocked'))
            )
            """,
            """
            CREATE TABLE checkout_item (
                checkout_item_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL REFERENCES checkout(checkout_id),
                offer_id TEXT NOT NULL REFERENCES offer(offer_id), quantity INTEGER NOT NULL CHECK (quantity >= 1),
                unit_price_minor BIGINT NOT NULL CHECK (unit_price_minor >= 0), total_minor BIGINT NOT NULL CHECK (total_minor >= 0)
            )
            """,
            """
            CREATE TABLE reservation (
                reservation_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL UNIQUE REFERENCES checkout(checkout_id),
                offer_id TEXT NOT NULL REFERENCES offer(offer_id), quantity INTEGER NOT NULL CHECK (quantity >= 1),
                status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), released_at TIMESTAMPTZ, committed_at TIMESTAMPTZ,
                CHECK (status IN ('held', 'released', 'committed'))
            )
            """,
            """
            CREATE TABLE policy_decision (
                decision_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL REFERENCES checkout(checkout_id),
                decision TEXT NOT NULL, reason_code TEXT NOT NULL, policy_version TEXT NOT NULL,
                inputs_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (decision IN ('ALLOW', 'REQUIRE_APPROVAL', 'BLOCK'))
            )
            """,
            """
            CREATE TABLE \"authorization\" (
                authorization_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL UNIQUE REFERENCES checkout(checkout_id),
                buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id), merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                amount_ceiling_minor BIGINT NOT NULL CHECK (amount_ceiling_minor >= 0), currency TEXT NOT NULL,
                price_hash TEXT NOT NULL, policy_version TEXT NOT NULL, status TEXT NOT NULL,
                valid_until TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('pending', 'approved', 'rejected', 'revoked', 'consumed', 'expired'))
            )
            """,
            """
            CREATE TABLE payment (
                payment_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL REFERENCES checkout(checkout_id),
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id),
                authorization_id TEXT NOT NULL REFERENCES \"authorization\"(authorization_id), provider TEXT NOT NULL,
                provider_order_id TEXT UNIQUE, provider_payment_id TEXT UNIQUE,
                provider_signature TEXT, idempotency_key TEXT,
                amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0), currency TEXT NOT NULL,
                status TEXT NOT NULL, test_mode BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), verified_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('created', 'pending', 'verified', 'failed', 'timeout', 'unknown', 'manual_review'))
            )
            """,
            """
            CREATE TABLE provider_event (
                provider_event_id TEXT PRIMARY KEY, payment_id TEXT REFERENCES payment(payment_id),
                provider TEXT NOT NULL, event_type TEXT NOT NULL, signature TEXT,
                signature_valid BOOLEAN NOT NULL DEFAULT true, raw_body_hash TEXT,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb, status TEXT NOT NULL DEFAULT 'processed',
                received_at TIMESTAMPTZ NOT NULL DEFAULT now(), processed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE \"order\" (
                order_id TEXT PRIMARY KEY, order_number TEXT NOT NULL UNIQUE,
                payment_id TEXT NOT NULL UNIQUE REFERENCES payment(payment_id),
                checkout_id TEXT NOT NULL UNIQUE REFERENCES checkout(checkout_id), buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id),
                merchant_id TEXT NOT NULL REFERENCES merchant(merchant_id),
                amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
                total_minor BIGINT NOT NULL CHECK (total_minor >= 0),
                currency TEXT NOT NULL, status TEXT NOT NULL, shipping_address JSONB, confirmed_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (status IN ('confirmed', 'completed', 'cancelled'))
            )
            """,
            """
            CREATE TABLE negotiation_round (
                negotiation_round_id TEXT PRIMARY KEY, offer_id TEXT NOT NULL REFERENCES offer(offer_id),
                buyer_id TEXT NOT NULL REFERENCES buyer(buyer_id), round_number INTEGER NOT NULL CHECK (round_number >= 1),
                proposed_price_minor BIGINT NOT NULL CHECK (proposed_price_minor >= 0),
                floor_price_minor BIGINT NOT NULL CHECK (floor_price_minor >= 0), outcome TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (offer_id, buyer_id, round_number)
            )
            """,
            """
            CREATE TABLE recommendation (
                recommendation_id TEXT PRIMARY KEY, checkout_id TEXT NOT NULL REFERENCES checkout(checkout_id),
                offer_id TEXT NOT NULL REFERENCES offer(offer_id), reason_code TEXT NOT NULL,
                evaluation_run_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE agent_run (
                agent_run_id TEXT PRIMARY KEY, buyer_id TEXT REFERENCES buyer(buyer_id), checkout_id TEXT REFERENCES checkout(checkout_id),
                status TEXT NOT NULL, intent JSONB NOT NULL, model_version TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
                CHECK (status IN ('running', 'completed', 'failed', 'blocked', 'timed_out'))
            )
            """,
            """
            CREATE TABLE tool_call (
                tool_call_id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL REFERENCES agent_run(agent_run_id),
                tool_name TEXT NOT NULL, arguments JSONB NOT NULL, side_effect_class TEXT NOT NULL,
                confirmation_required BOOLEAN NOT NULL, status TEXT NOT NULL, result JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE research_session (
                research_session_id TEXT PRIMARY KEY, agent_run_id TEXT REFERENCES agent_run(agent_run_id),
                status TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE evidence (
                evidence_id TEXT PRIMARY KEY, agent_run_id TEXT REFERENCES agent_run(agent_run_id),
                research_session_id TEXT REFERENCES research_session(research_session_id), source_url TEXT, publisher TEXT,
                retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(), content_hash TEXT NOT NULL, excerpt TEXT, confidence DOUBLE PRECISION,
                source_type TEXT NOT NULL, claim_type TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE idempotency_record (
                idempotency_record_id TEXT PRIMARY KEY, actor_type TEXT NOT NULL DEFAULT 'buyer',
                actor_id TEXT NOT NULL, endpoint TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL, status TEXT NOT NULL, response_body JSONB, response_status INTEGER,
                response_status_code INTEGER, resource_type TEXT, resource_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '24 hours'),
                UNIQUE (actor_type, actor_id, endpoint, idempotency_key), CHECK (status IN ('in_progress', 'completed', 'failed'))
            )
            """,
            """
            CREATE TABLE audit_event (
                event_id TEXT PRIMARY KEY, merchant_id TEXT REFERENCES merchant(merchant_id), request_id TEXT, trace_id TEXT,
                agent_run_id TEXT REFERENCES agent_run(agent_run_id), actor_type TEXT NOT NULL, actor_id TEXT, event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL, input_hash TEXT, decision TEXT, reason_code TEXT,
                policy_version TEXT, model_version TEXT, amount_minor BIGINT, metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CHECK (actor_type IN ('buyer', 'agent', 'merchant', 'system', 'provider')),
                CHECK (amount_minor IS NULL OR amount_minor >= 0)
            )
            """,
            "CREATE INDEX ix_audit_event_aggregate ON audit_event (aggregate_type, aggregate_id, created_at)",
            "CREATE OR REPLACE FUNCTION reject_audit_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'audit_event is append-only'; END; $$",
            "CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event "
            "FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()",
        )
    )


def downgrade() -> None:
    _execute(
        (
            "DROP TRIGGER IF EXISTS audit_event_append_only ON audit_event",
            "DROP FUNCTION IF EXISTS reject_audit_event_mutation",
            "DROP TABLE IF EXISTS audit_event",
            "DROP TABLE IF EXISTS idempotency_record",
            "DROP TABLE IF EXISTS evidence",
            "DROP TABLE IF EXISTS research_session",
            "DROP TABLE IF EXISTS tool_call",
            "DROP TABLE IF EXISTS agent_run",
            "DROP TABLE IF EXISTS recommendation",
            "DROP TABLE IF EXISTS negotiation_round",
            "DROP TABLE IF EXISTS \"order\"",
            "DROP TABLE IF EXISTS provider_event",
            "DROP TABLE IF EXISTS payment",
            "DROP TABLE IF EXISTS \"authorization\"",
            "DROP TABLE IF EXISTS policy_decision",
            "DROP TABLE IF EXISTS reservation",
            "DROP TABLE IF EXISTS checkout_item",
            "DROP TABLE IF EXISTS checkout",
            "DROP TABLE IF EXISTS inventory",
            "DROP TABLE IF EXISTS offer",
            "DROP TABLE IF EXISTS buyer_policy",
            "DROP TABLE IF EXISTS api_client",
            "DROP TABLE IF EXISTS category_pairing",
            "DROP TABLE IF EXISTS merchant_rules",
            "DROP TABLE IF EXISTS product_embedding",
            "DROP TABLE IF EXISTS review",
            "DROP TABLE IF EXISTS product_image",
            "DROP TABLE IF EXISTS variant",
            "DROP TABLE IF EXISTS product",
            "DROP TABLE IF EXISTS catalog_version",
            "DROP TABLE IF EXISTS import_run",
            "DROP TABLE IF EXISTS buyer",
            "DROP TABLE IF EXISTS merchant",
        )
    )
