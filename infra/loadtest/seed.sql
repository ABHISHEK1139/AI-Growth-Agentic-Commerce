-- Seed a realistic data volume for load testing.
--
-- WHY THIS EXISTS
-- ---------------
-- The development database holds 41 offers and 536 audit rows. Load testing against
-- that measures PostgreSQL's buffer cache, not AgentPay: every table fits in memory,
-- every index would be used if one existed, and the sequential scans that make the hot
-- paths slow in production look free. A number produced against a fixture this size is
-- not a baseline, it is an artefact.
--
-- The volumes below are chosen to make the *difference* between a sequential scan and
-- an index lookup unmissable, which is the thing these tests are here to measure. They
-- are not a claim about 60k customers' production row counts -- §4 of
-- docs/production/READINESS_PLAN.md derives those from traffic assumptions, and this
-- file deliberately does not encode an opinion about them.
--
-- WHAT IS SEEDED, AND WHY EACH TABLE MATTERS
-- --------------------------------------------
--   buyer         2,000    the foreign key every buyer-scoped query filters on
--   merchant        200    tenant column in the two biggest tables
--   api_client     500    agent token exchange reads all active keys per request
--   offer        20,000    catalogue read path (agent browse dominates traffic)
--   checkout    100,000    grow-only table; drives audit_event volume
--   payment      60,000    provider-side lookups by order id
--   provider_event 60,000  THE webhook replay check. Scales linearly with payments.
--   agent_run     40,000    agent conversation timeline
--   tool_call    160,000    4 per agent run, the agent tool-loop join
--   evidence      80,000    2 per agent run, the evidence bundle
--   audit_event  400,000    legally retained, never deleted -- grows without bound
--
-- provider_event and audit_event are the two that matter most here. Both grow by one
-- row per event forever, neither is ever cleaned up, and before migration 0005 both
-- were read by sequential scan.
--
-- SAFE TO RUN MORE THAN ONCE
-- ---------------------------
-- Every insert is keyed on a deterministic synthetic id with ON CONFLICT DO NOTHING, so
-- re-running tops up to the target count rather than duplicating. TRUNCATE first (see
-- `--truncate`) if you want a clean measurement.

\echo 'Seeding load-test volumes (idempotent; safe to re-run)...'

\timing on

-- ---------------------------------------------------------------------
-- Tenants and actors
-- ---------------------------------------------------------------------

INSERT INTO buyer (buyer_id, tenant_id, display_name, status, created_at)
SELECT
    'ld_buyer_' || g,
    'ld_tenant_' || (g % 200),
    'Load Buyer ' || g,
    'active',
    now() - (g || ' minutes')::interval
FROM generate_series(1, 2000) AS g
ON CONFLICT (buyer_id) DO NOTHING;

INSERT INTO merchant (merchant_id, name, status, created_at)
SELECT
    'ld_merch_' || g,
    'Load Merchant ' || g,
    'active',
    now() - (g || ' minutes')::interval
FROM generate_series(1, 200) AS g
ON CONFLICT (merchant_id) DO NOTHING;

-- key_hash is the unique column the token exchange looks up on every agent request.
INSERT INTO api_client (
    api_client_id, merchant_id, key_hash, scopes, status, created_at, label, role
)
SELECT
    'ld_key_' || g,
    'ld_merch_' || (1 + (g % 200)),
    'ld_hash_' || g,
    '["catalog:read","checkout:write"]',
    'active',
    now(),
    'Load Key ' || g,
    'agent'
FROM generate_series(1, 500) AS g
ON CONFLICT (api_client_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Catalogue
-- ---------------------------------------------------------------------

-- One published catalog version per merchant, which is what the offer FK needs.
INSERT INTO catalog_version (
    catalog_version_id, merchant_id, status, product_count, valid_count,
    needs_review_count, created_at, published_at
)
SELECT
    'ld_catv_' || g,
    'ld_merch_' || g,
    'published',
    0, 0, 0,
    now() - (g || ' minutes')::interval,
    now() - (g || ' minutes')::interval
FROM generate_series(1, 200) AS g
ON CONFLICT (catalog_version_id) DO NOTHING;

-- product and variant are required by the offer foreign keys, so they cannot be
-- skipped: `offer.product_id` and `offer.variant_id` both point at real rows.
INSERT INTO product (
    product_id, catalog_version_id, merchant_id, external_product_id, category_id,
    title, status, description, specifications, average_rating, rating_number, created_at
)
SELECT
    'ld_product_' || g,
    'ld_catv_' || (1 + (g % 200)),
    'ld_merch_' || (1 + (g % 200)),
    'ld_ext_' || g,
    'ld_cat_' || (1 + (g % 50)),
    'Load Product ' || g,
    CASE g % 20 WHEN 0 THEN 'needs_review' WHEN 1 THEN 'inactive' ELSE 'valid' END,
    -- product.description is JSONB and the ORM types it as a *list* of sections
-- (`Mapped[list[Any]]`), not a string. Seeded to match, because a seed that writes a
-- different shape than the application writes would make any later read of this column
-- fail for reasons unrelated to load.
    ('[{"heading":"Overview","body":"Load test product ' || g
        || '"},{"heading":"Details","body":"Synthetic description for load testing."}]')::jsonb,
    -- The cast wraps the *whole* concatenation. `a || b || '}'::jsonb` parses as
    -- `a || b || ('}'::jsonb)` because `::` binds tighter than `||`, and casting the
    -- lone '}' is invalid JSON.
    ('{"weight_g":' || (100 + (g % 5000)) || ',"warranty_months":' || (1 + (g % 36)) || '}')::jsonb,
    3.0 + ((g % 20) / 10.0),
    g % 500,
    now() - (g || ' minutes')::interval
FROM generate_series(1, 20000) AS g
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO variant (
    variant_id, product_id, external_variant_id, title, specifications
)
SELECT
    'ld_variant_' || g,
    'ld_product_' || g,
    'ld_extvar_' || g,
    'Load Variant ' || g,
    ('{"sku":"LD-SKU-' || g || '"}')::jsonb
FROM generate_series(1, 20000) AS g
ON CONFLICT (variant_id) DO NOTHING;

-- Every enumerated column below is checked against the database's own CHECK
-- constraints rather than invented, because these constraints are a genuine data
-- integrity control and the seed must not route around them to save time:
--
--   checkout.status       created, policy_checked, authorization_pending, authorized,
--                         cancelled, expired, price_changed, completed
--   payment.status        created, pending, verified, failed, timeout, unknown,
--                         manual_review
--   offer.pricing_source  synthetic_band_random, merchant_configured
--   audit_event.actor_type  buyer, agent, merchant, system, provider
--
-- The status columns are deliberately given a realistic *mix* of values. A column where
-- every row is identical makes an index look useless to the planner, which would make
-- this file measure the wrong thing -- the opposite of its purpose.

INSERT INTO offer (
    offer_id, catalog_version_id, product_id, variant_id, merchant_id, status,
    unit_price_minor, currency, delivery_days, return_period_days, pricing_source,
    offer_version, expires_at, created_at
)
SELECT
    'ld_offer_' || g,
    'ld_catv_' || (1 + (g % 200)),
    'ld_product_' || g,
    'ld_variant_' || g,
    'ld_merch_' || (1 + (g % 200)),
    CASE g % 10 WHEN 0 THEN 'expired' WHEN 1 THEN 'inactive' ELSE 'active' END,
    1000 + (g % 90000),
    'INR',
    2 + (g % 8),
    7 + (g % 23),
    -- Both permitted values, so the column is not single-valued.
    CASE WHEN g % 2 = 0 THEN 'synthetic_band_random' ELSE 'merchant_configured' END,
    1,
    now() + interval '30 days',
    now() - (g || ' minutes')::interval
FROM generate_series(1, 20000) AS g
ON CONFLICT (offer_id) DO NOTHING;

-- inventory is required by OfferService.get_offer_by_id, which refuses an offer whose
-- inventory row is missing. Without this every catalogue read returns OFFER_NOT_FOUND
-- and the load test would measure 404s -- fast, and meaningless. Found by doing it.
-- `reserved <= available` is a real CHECK constraint, so the split below respects it.
INSERT INTO inventory (offer_id, available_quantity, reserved_quantity, version)
SELECT
    'ld_offer_' || g,
    -- ~15% of offers sold out, so the availability filter is actually selective.
    CASE WHEN g % 7 = 0 THEN 0 ELSE 5 + (g % 500) END,
    CASE WHEN g % 7 = 0 THEN 0 ELSE g % 3 END,
    1 + (g % 4)
FROM generate_series(1, 20000) AS g
ON CONFLICT (offer_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Commerce transactions
-- ---------------------------------------------------------------------

INSERT INTO checkout (
    checkout_id, buyer_id, merchant_id, offer_id, offer_version, status,
    subtotal_minor, shipping_minor, tax_minor, discount_minor, total_minor,
    currency, price_hash, price_snapshot, expires_at, created_at
)
SELECT
    'ld_ck_' || g,
    'ld_buyer_' || (1 + (g % 2000)),
    'ld_merch_' || (1 + (g % 200)),
    'ld_offer_' || (1 + (g % 20000)),
    1,
    -- A realistic mix rather than all-'pending', so index selectivity is honest: a
    -- status column where every row is identical makes an index look useless.
    CASE g % 8
        WHEN 0 THEN 'completed'      WHEN 1 THEN 'policy_checked'
        WHEN 2 THEN 'expired'        WHEN 3 THEN 'authorized'
        WHEN 4 THEN 'cancelled'      WHEN 5 THEN 'price_changed'
        WHEN 6 THEN 'authorization_pending'
        ELSE 'created'
    END,
    1000 + (g % 90000), 0, 0, 0, 1000 + (g % 90000),
    'INR',
    'ld_price_hash_' || g,
    -- Whole concatenation wrapped before the cast; see the note on product.specifications.
    ('{"offer_id":"ld_offer_' || (1 + (g % 20000))
        || '","unit_price_minor":' || (1000 + (g % 90000))
        || ',"currency":"INR"}')::jsonb,
    now() + interval '1 hour',
    now() - (g || ' seconds')::interval
FROM generate_series(1, 100000) AS g
ON CONFLICT (checkout_id) DO NOTHING;

-- 1.5 items per checkout, so this references only the 100,000 checkouts that exist.
-- The modulo is what keeps it in range: a flat `ld_ck_ || g` over 150,000 rows would
-- reference 50,000 checkouts that were never created.
INSERT INTO checkout_item (
    checkout_item_id, checkout_id, offer_id, quantity, unit_price_minor, total_minor
)
SELECT
    'ld_ci_' || g,
    'ld_ck_' || (1 + (g % 100000)),
    'ld_offer_' || (1 + (g % 20000)),
    1 + (g % 3),
    1000 + (g % 90000),
    1000 + (g % 90000)
FROM generate_series(1, 150000) AS g
ON CONFLICT (checkout_item_id) DO NOTHING;

-- Standing authorization: the buyer's pre-approval that makes an agent-initiated
-- capture legal. payment.authorization_id is NOT NULL, so this is not optional -- it
-- is the financial invariant that an agent cannot move money without one. Seeded before
-- payment for that reason.
INSERT INTO "authorization" (
    authorization_id, checkout_id, buyer_id, merchant_id, amount_ceiling_minor,
    currency, price_hash, policy_version, status, valid_until, created_at
)
SELECT
    'ld_auth_' || g,
    'ld_ck_' || g,
    'ld_buyer_' || (1 + (g % 2000)),
    'ld_merch_' || (1 + (g % 200)),
    100000,
    'INR',
    'ld_price_hash_' || g,
    'policy-v3',
    CASE g % 10 WHEN 0 THEN 'revoked' WHEN 1 THEN 'expired' ELSE 'approved' END,
    now() + interval '30 days',
    now() - (g || ' seconds')::interval
FROM generate_series(1, 60000) AS g
ON CONFLICT (authorization_id) DO NOTHING;

INSERT INTO payment (
    payment_id, checkout_id, authorization_id, provider, provider_order_id,
    provider_payment_id, amount_minor, currency, status, test_mode, created_at,
    verified_at, merchant_id, buyer_id, idempotency_key, updated_at
)
SELECT
    'ld_pay_' || g,
    'ld_ck_' || g,
    'ld_auth_' || g,
    'razorpay',
    'ld_order_' || g,
    'ld_payref_' || g,
    1000 + (g % 90000),
    'INR',
    CASE g % 4
        WHEN 0 THEN 'verified' WHEN 1 THEN 'pending' WHEN 2 THEN 'failed' ELSE 'created'
    END,
    true,
    now() - (g || ' seconds')::interval,
    now() - (g || ' seconds')::interval,
    'ld_merch_' || (1 + (g % 200)),
    'ld_buyer_' || (1 + (g % 2000)),
    'ld_idem_' || g,
    now() - (g || ' seconds')::interval
FROM generate_series(1, 60000) AS g
ON CONFLICT (payment_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Provider events: the webhook replay check
-- ---------------------------------------------------------------------
--
-- raw_body_hash is populated and *deliberately varied*, because the dedup query is
--     WHERE provider_event_id = ? OR (raw_body_hash = ? AND signature = ?)
-- A constant hash would make every row collide on one index entry and measure the
-- wrong thing. Distinct hashes are what a real provider stream produces.
INSERT INTO provider_event (
    provider_event_id, payment_id, event_type, signature_valid, raw_body_hash,
    received_at, processed_at, provider, signature, payload, status, created_at
)
SELECT
    'ld_evt_' || g,
    'ld_pay_' || g,
    CASE g % 4
        WHEN 0 THEN 'payment.captured' WHEN 1 THEN 'payment.authorized'
        WHEN 2 THEN 'payment.failed' ELSE 'refund.processed'
    END,
    true,
    md5('ld_body_' || g),
    now() - (g || ' seconds')::interval,
    now() - (g || ' seconds')::interval,
    'razorpay',
    md5('ld_sig_' || g),
    ('{"event_id":"ld_evt_' || g || '","amount_minor":' || (1000 + (g % 90000)) || '}')::jsonb,
    'processed',
    now() - (g || ' seconds')::interval
FROM generate_series(1, 60000) AS g
ON CONFLICT (provider_event_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Agent activity
-- ---------------------------------------------------------------------

INSERT INTO agent_run (
    agent_run_id, buyer_id, checkout_id, status, intent, model_version,
    started_at, completed_at
)
SELECT
    'ld_run_' || g,
    'ld_buyer_' || (1 + (g % 2000)),
    'ld_ck_' || (1 + (g % 100000)),
    CASE g % 3 WHEN 0 THEN 'completed' WHEN 1 THEN 'running' ELSE 'failed' END,
    ('{"intent":"find_a_product_matching_a_requirement","slot":' || (g % 7) || '}')::jsonb,
    'llm-test-v1',
    now() - (g || ' seconds')::interval,
    now() - (g || ' seconds')::interval + interval '3 seconds'
FROM generate_series(1, 40000) AS g
ON CONFLICT (agent_run_id) DO NOTHING;

INSERT INTO tool_call (
    tool_call_id, agent_run_id, tool_name, arguments, side_effect_class,
    confirmation_required, status, result, created_at
)
SELECT
    'ld_tc_' || g,
    'ld_run_' || (1 + (g % 40000)),
    (ARRAY['catalog_search','get_offer','get_product','create_checkout'])[1 + (g % 4)],
    ('{"q":"load test query ' || (g % 500) || '","limit":20}')::jsonb,
    CASE WHEN g % 4 = 3 THEN 'financial' ELSE 'read' END,
    (g % 4) = 3,
    'completed',
    ('{"ok":true,"rows":' || (g % 20) || '}')::jsonb,
    now() - (g || ' seconds')::interval
FROM generate_series(1, 160000) AS g
ON CONFLICT (tool_call_id) DO NOTHING;

-- research_session is referenced by evidence.research_session_id.
INSERT INTO research_session (
    research_session_id, agent_run_id, status, started_at, completed_at
)
SELECT
    'ld_rs_' || g,
    'ld_run_' || (1 + (g % 40000)),
    'completed',
    now() - (g || ' seconds')::interval,
    now() - (g || ' seconds')::interval + interval '5 seconds'
FROM generate_series(1, 5000) AS g
ON CONFLICT (research_session_id) DO NOTHING;

INSERT INTO evidence (
    evidence_id, agent_run_id, research_session_id, source_url, publisher,
    retrieved_at, content_hash, excerpt, confidence, source_type, claim_type
)
SELECT
    'ld_ev_' || g,
    'ld_run_' || (1 + (g % 40000)),
    'ld_rs_' || (1 + (g % 5000)),
    'https://example.com/spec/' || g,
    'Example Publisher ' || (g % 200),
    now() - (g || ' seconds')::interval,
    md5('ld_content_' || g),
    'Load test evidence excerpt ' || g,
    0.5 + ((g % 50) / 100.0),
    (ARRAY['vendor_site','review','spec_sheet'])[1 + (g % 3)],
    'specification'
FROM generate_series(1, 80000) AS g
ON CONFLICT (evidence_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Audit ledger: legally retained, never deleted
-- ---------------------------------------------------------------------

INSERT INTO audit_event (
    event_id, merchant_id, request_id, trace_id, agent_run_id, actor_type, actor_id,
    event_type, aggregate_type, aggregate_id, input_hash, decision, reason_code,
    policy_version, model_version, amount_minor, metadata, created_at
)
SELECT
    'ld_audit_' || g,
    'ld_merch_' || (1 + (g % 200)),
    'ld_req_' || g,
    'ld_trace_' || g,
    'ld_run_' || (1 + (g % 40000)),
    (ARRAY['buyer','agent','merchant','system','provider'])[1 + (g % 5)],
    'ld_actor_' || (g % 2000),
    (ARRAY['checkout.created','payment.captured','offer.viewed','agent.tool_called',
           'authorization.granted'])[1 + (g % 5)],
    (ARRAY['checkout','payment','offer','agent_run'])[1 + (g % 4)],
    'ld_agg_' || g,
    md5('ld_input_' || g),
    (ARRAY['allow','require_confirmation','deny'])[1 + (g % 3)],
    (ARRAY['', 'policy.price_changed', 'policy.amount_ceiling'])[1 + (g % 3)],
    'policy-v3',
    'llm-test-v1',
    CASE WHEN g % 5 = 1 THEN 1000 + (g % 90000) ELSE NULL END,
    ('{"seeded":true,"seq":' || g || '}')::jsonb,
    now() - (g || ' seconds')::interval
FROM generate_series(1, 400000) AS g
ON CONFLICT (event_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- Report
-- ---------------------------------------------------------------------

\timing off

\echo ''
\echo 'Row counts after seeding:'
SELECT relname AS "table", n_live_tup AS "rows"
FROM pg_stat_user_tables
WHERE n_live_tup > 0
ORDER BY n_live_tup DESC;

\echo ''
\echo 'Table sizes:'
SELECT
    relname AS "table",
    pg_size_pretty(pg_total_relation_size(c.oid)) AS "total"
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 12;
