"""Task 9 integration assertions against a real PostgreSQL migration target."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, Inspector
from sqlalchemy.exc import SQLAlchemyError

from apps.api.config import get_settings

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "agent_run",
    "api_client",
    "audit_event",
    "authorization",
    "buyer",
    "buyer_policy",
    "catalog_version",
    "category_pairing",
    "checkout",
    "checkout_item",
    "evidence",
    "idempotency_record",
    "import_run",
    "inventory",
    "merchant",
    "merchant_rules",
    "negotiation_round",
    "offer",
    "order",
    "payment",
    "policy_decision",
    "product",
    "product_embedding",
    "product_image",
    "provider_event",
    "recommendation",
    "research_session",
    "reservation",
    "review",
    "tool_call",
    "variant",
}


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(get_settings().database_url)
    try:
        with eng.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not reachable; start the compose stack for migration tests")
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def inspector(engine: Engine) -> Iterator[Inspector]:
    yield inspect(engine)


@pytest.fixture(scope="module")
def dialect(engine: Engine) -> str:
    """Return the dialect name so PostgreSQL-specific tests can be skipped on SQLite."""
    return engine.dialect.name


def test_initial_migration_created_every_designed_table(inspector: Inspector) -> None:
    """All designed tables exist after migrations run."""
    assert set(inspector.get_table_names()) >= EXPECTED_TABLES


def test_inventory_and_reservation_invariants_are_database_constraints(
    inspector: Inspector,
    dialect: str,
) -> None:
    """Inventory CHECK constraints and reservation unique constraints exist.

    These are enforced by the database (not just application code), which is the
    actual requirement. PostgreSQL-specific: partial indexes and CHECK constraints
    with arbitrary expressions are not portable to SQLite, so this test is skipped
    when running against SQLite.
    """
    if dialect == "sqlite":
        pytest.skip("CHECK constraints and partial indexes are PostgreSQL-specific")

    checks = {constraint["sqltext"] for constraint in inspector.get_check_constraints("inventory")}
    unique_sets = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("reservation")
    }

    assert any("available_quantity >= 0" in check for check in checks)
    assert any("reserved_quantity >= 0" in check for check in checks)
    assert any("reserved_quantity <= available_quantity" in check for check in checks)
    assert ("checkout_id",) in unique_sets


def test_idempotency_provider_and_catalog_uniqueness_are_enforced(inspector: Inspector) -> None:
    idempotency_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("idempotency_record")
    }
    provider_primary_key = inspector.get_pk_constraint("provider_event")["constrained_columns"]
    indexes = inspector.get_indexes("catalog_version")

    assert ("actor_type", "actor_id", "endpoint", "idempotency_key") in idempotency_unique
    assert provider_primary_key == ["provider_event_id"]
    assert any(
        index["name"] == "uq_catalog_version_one_published_per_merchant"
        and index["unique"]
        and "published" in str(index["dialect_options"])
        for index in indexes
    )


def test_filter_and_audit_indexes_are_present(inspector: Inspector) -> None:
    offer_indexes = {index["name"] for index in inspector.get_indexes("offer")}
    product_indexes = {index["name"] for index in inspector.get_indexes("product")}
    audit_columns = {
        column["name"]: column["type"] for column in inspector.get_columns("audit_event")
    }

    assert {"ix_offer_merchant_status", "ix_offer_price"} <= offer_indexes
    assert "ix_product_category_status" in product_indexes
    assert audit_columns["amount_minor"].__class__.__name__ == "BIGINT"
