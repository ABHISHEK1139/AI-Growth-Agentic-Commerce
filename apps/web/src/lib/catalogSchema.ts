/**
 * SQLite schema for the web tier, generated - do not edit by hand.
 *
 * See `scripts/emit_web_schema.py` for how to regenerate and why this is
 * generated rather than written out. The file is emitted as a string so it
 * bundles with no loader configuration and no runtime file read.
 *
 * An empty database is the expected starting state: the web tier's read paths
 * fall back to the committed seed catalog baked into the bundle, which is the
 * same data `scripts/dev_bootstrap.py` loads locally.
 */
export const CATALOG_SCHEMA_SQL = `
CREATE TABLE audit_event (
                    event_id TEXT PRIMARY KEY,
                    merchant_id TEXT,
                    request_id TEXT,
                    trace_id TEXT,
                    agent_run_id TEXT,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    event_type TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    input_hash TEXT,
                    decision TEXT,
                    reason_code TEXT,
                    policy_version TEXT,
                    model_version TEXT,
                    amount_minor INTEGER,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
CREATE TABLE authorization (
	authorization_id VARCHAR NOT NULL, 
	checkout_id VARCHAR NOT NULL, 
	buyer_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	amount_ceiling_minor BIGINT NOT NULL, 
	currency VARCHAR NOT NULL, 
	price_hash VARCHAR NOT NULL, 
	policy_version VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	valid_until DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (authorization_id), 
	UNIQUE (checkout_id), 
	FOREIGN KEY(checkout_id) REFERENCES checkout (checkout_id), 
	FOREIGN KEY(buyer_id) REFERENCES buyer (buyer_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE buyer (
	buyer_id VARCHAR NOT NULL, 
	tenant_id VARCHAR NOT NULL, 
	display_name VARCHAR, 
	status VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (buyer_id)
);
CREATE TABLE buyer_policy (
	buyer_id VARCHAR NOT NULL, 
	version VARCHAR NOT NULL, 
	max_transaction_minor BIGINT NOT NULL, 
	auto_approval_limit_minor BIGINT NOT NULL, 
	allowed_merchants JSON NOT NULL, 
	allowed_categories JSON NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (buyer_id), 
	FOREIGN KEY(buyer_id) REFERENCES buyer (buyer_id)
);
CREATE TABLE catalog_import (
	import_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	filename VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	total_rows INTEGER NOT NULL, 
	valid_rows INTEGER NOT NULL, 
	invalid_rows INTEGER NOT NULL, 
	error_summary VARCHAR, 
	created_at DATETIME NOT NULL, 
	validated_at DATETIME, 
	published_at DATETIME, 
	published_catalog_version_id VARCHAR, 
	PRIMARY KEY (import_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id), 
	FOREIGN KEY(published_catalog_version_id) REFERENCES catalog_version (catalog_version_id)
);
CREATE TABLE catalog_import_row (
	row_id VARCHAR NOT NULL, 
	import_id VARCHAR NOT NULL, 
	row_number INTEGER NOT NULL, 
	sku VARCHAR, 
	title VARCHAR, 
	description VARCHAR, 
	price_minor INTEGER, 
	currency VARCHAR, 
	inventory INTEGER, 
	status VARCHAR, 
	image_url VARCHAR, 
	category VARCHAR, 
	is_valid BOOLEAN NOT NULL, 
	validation_errors JSON, 
	delivery_days INTEGER, 
	return_period_days INTEGER, 
	offer_id VARCHAR, 
	PRIMARY KEY (row_id), 
	FOREIGN KEY(import_id) REFERENCES catalog_import (import_id)
);
CREATE TABLE catalog_version (
	catalog_version_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	import_run_id VARCHAR, 
	status VARCHAR NOT NULL, 
	product_count INTEGER NOT NULL, 
	valid_count INTEGER NOT NULL, 
	needs_review_count INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	published_at DATETIME, 
	PRIMARY KEY (catalog_version_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id), 
	FOREIGN KEY(import_run_id) REFERENCES import_run (import_run_id)
);
CREATE TABLE category_pairing (
	pairing_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	source_category_id VARCHAR NOT NULL, 
	target_category_id VARCHAR NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	PRIMARY KEY (pairing_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE checkout (
	checkout_id VARCHAR NOT NULL, 
	buyer_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	offer_id VARCHAR NOT NULL, 
	offer_version INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	subtotal_minor BIGINT NOT NULL, 
	shipping_minor BIGINT NOT NULL, 
	tax_minor BIGINT NOT NULL, 
	discount_minor BIGINT NOT NULL, 
	total_minor BIGINT NOT NULL, 
	currency VARCHAR NOT NULL, 
	price_hash VARCHAR NOT NULL, 
	price_snapshot JSON NOT NULL, 
	expires_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (checkout_id), 
	FOREIGN KEY(buyer_id) REFERENCES buyer (buyer_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id), 
	FOREIGN KEY(offer_id) REFERENCES offer (offer_id)
);
CREATE TABLE checkout_item (
	checkout_item_id VARCHAR NOT NULL, 
	checkout_id VARCHAR NOT NULL, 
	offer_id VARCHAR NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit_price_minor BIGINT NOT NULL, 
	total_minor BIGINT NOT NULL, 
	PRIMARY KEY (checkout_item_id), 
	FOREIGN KEY(checkout_id) REFERENCES checkout (checkout_id), 
	FOREIGN KEY(offer_id) REFERENCES offer (offer_id)
);
CREATE TABLE failed_webhook (
	failed_webhook_id VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	signature VARCHAR, 
	raw_body_hash VARCHAR, 
	payload JSON NOT NULL, 
	status VARCHAR NOT NULL, 
	attempt_count INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	last_attempt_at DATETIME, 
	next_retry_at DATETIME, 
	last_error VARCHAR, 
	resolved_at DATETIME, 
	resolved_by VARCHAR, 
	resolution_note VARCHAR, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (failed_webhook_id)
);
CREATE TABLE idempotency_record (
	idempotency_record_id VARCHAR NOT NULL, 
	actor_type VARCHAR NOT NULL, 
	actor_id VARCHAR NOT NULL, 
	endpoint VARCHAR NOT NULL, 
	idempotency_key VARCHAR NOT NULL, 
	request_hash VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	response_status INTEGER, 
	response_status_code INTEGER, 
	response_body JSON, 
	resource_type VARCHAR, 
	resource_id VARCHAR, 
	created_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	expires_at DATETIME NOT NULL, 
	PRIMARY KEY (idempotency_record_id), 
	CONSTRAINT uq_idempotency_record_actor_type_id_endpoint_key UNIQUE (actor_type, actor_id, endpoint, idempotency_key)
);
CREATE TABLE import_run (
	import_run_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	source_name VARCHAR NOT NULL, 
	source_checksum VARCHAR NOT NULL, 
	schema_version VARCHAR NOT NULL, 
	licence_note VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	started_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	PRIMARY KEY (import_run_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE inventory (
	offer_id VARCHAR NOT NULL, 
	available_quantity INTEGER NOT NULL, 
	reserved_quantity INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (offer_id)
);
CREATE TABLE merchant (
	merchant_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (merchant_id)
);
CREATE TABLE merchant_rules (
	merchant_id VARCHAR NOT NULL, 
	version VARCHAR NOT NULL, 
	max_transaction_minor INTEGER NOT NULL, 
	auto_approval_limit_minor INTEGER NOT NULL, 
	max_discount_basis_points INTEGER NOT NULL, 
	allowed_categories JSON NOT NULL, 
	blocked_categories JSON NOT NULL, 
	allowed_payment_methods JSON NOT NULL, 
	allow_out_of_stock BOOLEAN NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (merchant_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE offer (
	offer_id VARCHAR NOT NULL, 
	catalog_version_id VARCHAR NOT NULL, 
	product_id VARCHAR NOT NULL, 
	variant_id VARCHAR, 
	merchant_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	unit_price_minor BIGINT NOT NULL, 
	currency VARCHAR NOT NULL, 
	delivery_days INTEGER NOT NULL, 
	return_period_days INTEGER NOT NULL, 
	pricing_source VARCHAR NOT NULL, 
	offer_version INTEGER NOT NULL, 
	expires_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (offer_id), 
	FOREIGN KEY(catalog_version_id) REFERENCES catalog_version (catalog_version_id), 
	FOREIGN KEY(product_id) REFERENCES product (product_id), 
	FOREIGN KEY(variant_id) REFERENCES variant (variant_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE "order" (
	order_id VARCHAR NOT NULL, 
	order_number VARCHAR NOT NULL, 
	checkout_id VARCHAR NOT NULL, 
	payment_id VARCHAR NOT NULL, 
	buyer_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	total_minor BIGINT NOT NULL, 
	amount_minor BIGINT NOT NULL, 
	currency VARCHAR NOT NULL, 
	shipping_address JSON, 
	confirmed_at DATETIME NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (order_id), 
	UNIQUE (order_number), 
	UNIQUE (checkout_id), 
	FOREIGN KEY(checkout_id) REFERENCES checkout (checkout_id), 
	UNIQUE (payment_id), 
	FOREIGN KEY(payment_id) REFERENCES payment (payment_id), 
	FOREIGN KEY(buyer_id) REFERENCES buyer (buyer_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE payment (
	payment_id VARCHAR NOT NULL, 
	checkout_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	buyer_id VARCHAR NOT NULL, 
	authorization_id VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	amount_minor BIGINT NOT NULL, 
	currency VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	provider_order_id VARCHAR, 
	provider_payment_id VARCHAR, 
	provider_signature VARCHAR, 
	idempotency_key VARCHAR, 
	test_mode BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	verified_at DATETIME, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (payment_id), 
	FOREIGN KEY(checkout_id) REFERENCES checkout (checkout_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id), 
	FOREIGN KEY(buyer_id) REFERENCES buyer (buyer_id), 
	FOREIGN KEY(authorization_id) REFERENCES authorization (authorization_id)
);
CREATE TABLE policy_decision (
	decision_id VARCHAR NOT NULL, 
	checkout_id VARCHAR NOT NULL, 
	decision VARCHAR NOT NULL, 
	reason_code VARCHAR NOT NULL, 
	policy_version VARCHAR NOT NULL, 
	inputs_hash VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (decision_id), 
	FOREIGN KEY(checkout_id) REFERENCES checkout (checkout_id)
);
CREATE TABLE product (
	product_id VARCHAR NOT NULL, 
	catalog_version_id VARCHAR NOT NULL, 
	merchant_id VARCHAR NOT NULL, 
	external_product_id VARCHAR NOT NULL, 
	category_id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	description JSON NOT NULL, 
	specifications JSON NOT NULL, 
	average_rating FLOAT NOT NULL, 
	rating_number INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (product_id), 
	FOREIGN KEY(catalog_version_id) REFERENCES catalog_version (catalog_version_id), 
	FOREIGN KEY(merchant_id) REFERENCES merchant (merchant_id)
);
CREATE TABLE product_image (
	product_image_id VARCHAR NOT NULL, 
	product_id VARCHAR NOT NULL, 
	source_url VARCHAR NOT NULL, 
	storage_key VARCHAR NOT NULL, 
	resolution VARCHAR NOT NULL, 
	position INTEGER NOT NULL, 
	PRIMARY KEY (product_image_id), 
	FOREIGN KEY(product_id) REFERENCES product (product_id)
);
CREATE TABLE provider_event (
	provider_event_id VARCHAR NOT NULL, 
	payment_id VARCHAR, 
	provider VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	signature VARCHAR, 
	signature_valid BOOLEAN NOT NULL, 
	raw_body_hash VARCHAR, 
	payload JSON NOT NULL, 
	status VARCHAR NOT NULL, 
	received_at DATETIME NOT NULL, 
	processed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (provider_event_id), 
	FOREIGN KEY(payment_id) REFERENCES payment (payment_id)
);
CREATE TABLE reservation (
	reservation_id VARCHAR NOT NULL, 
	checkout_id VARCHAR NOT NULL, 
	offer_id VARCHAR NOT NULL, 
	quantity INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	released_at DATETIME, 
	committed_at DATETIME, 
	PRIMARY KEY (reservation_id), 
	UNIQUE (checkout_id)
);
CREATE TABLE review (
	review_id VARCHAR NOT NULL, 
	product_id VARCHAR NOT NULL, 
	parent_asin VARCHAR NOT NULL, 
	rating INTEGER, 
	title TEXT, 
	body TEXT, 
	verified_purchase BOOLEAN, 
	reviewed_at DATETIME, 
	source_file VARCHAR NOT NULL, 
	raw_body_hash VARCHAR NOT NULL, 
	PRIMARY KEY (review_id), 
	FOREIGN KEY(product_id) REFERENCES product (product_id)
);
CREATE TABLE variant (
	variant_id VARCHAR NOT NULL, 
	product_id VARCHAR NOT NULL, 
	external_variant_id VARCHAR, 
	title VARCHAR NOT NULL, 
	specifications JSON NOT NULL, 
	PRIMARY KEY (variant_id), 
	FOREIGN KEY(product_id) REFERENCES product (product_id)
);
CREATE INDEX IF NOT EXISTS  idx_pi_pid_pos ON product_image(product_id, position)
`;
