"""The ORM and the live database must describe the same schema.

The failure this exists to prevent
----------------------------------
``provider_event`` reached production with seven columns while
``services.payments/models.py`` declared twelve. Every correctly-signed webhook failed
with ``UndefinedColumn``, and nothing noticed: the unit suite builds its tables *from*
the ORM metadata, so the ORM always agrees with itself, and no test posted a signed
webhook to a real database.

``failed_webhook`` and five catalog tables were worse -- declared in the ORM, imported
by live code, and never created by any migration at all.

Root cause: ``infra/migrations/env.py`` set ``target_metadata = Base.metadata`` without
importing a single model module. ``Base`` is a registry, so it was empty and
``--autogenerate`` proposed dropping all 36 tables.

So this module compares the two directly, on a real database. It is an integration test
because there is no substitute: the whole point is that the ORM cannot be trusted to
agree with itself.

What it deliberately does not check
-----------------------------------
Eight tables exist in the database with no ORM mapping (``agent_run``, ``audit_event``,
``evidence``, ``negotiation_round``, ``product_embedding``, ``recommendation``,
``research_session``, ``tool_call``). They are read by raw SQL, so autogenerate sees
them as surplus. They are reported as *unmapped* rather than failed, because a table
with no ORM class is a different (and much less urgent) problem than a column the ORM
reads and the database does not have.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
import sqlalchemy as sa

from apps.api.config import get_settings
from packages.db.base import Base


def _register_all_models() -> int:
    """Import every ``services.*.models`` module so ``Base.metadata`` is complete.

    Mirrors ``infra/migrations/env.py``. Written out rather than imported from there
    because that module reads the Alembic context at import time and cannot be
    imported outside a migration run.
    """
    import services

    count = 0
    for module in pkgutil.walk_packages(services.__path__, "services."):
        if module.name.endswith(".models"):
            importlib.import_module(module.name)
            count += 1
    return count


@pytest.fixture(scope="module")
def live_inspector() -> sa.Inspector:
    _register_all_models()
    engine = sa.create_engine(get_settings().resolved_database_url)
    try:
        yield sa.inspect(engine)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def orm_tables() -> dict:
    _register_all_models()
    return dict(Base.metadata.tables)


def test_model_modules_are_importable() -> None:
    """``Base.metadata`` being empty is the bug that caused all of this.

    Asserted directly so that if model registration silently breaks again, the failure
    names the cause rather than appearing later as a confusing schema mismatch.
    """
    registered = _register_all_models()
    assert registered > 0, "No services.*.models modules were imported"
    assert Base.metadata.tables, (
        "Base.metadata is empty after importing the model modules. Anything comparing "
        "the ORM to a real schema -- autogenerate included -- would conclude every "
        "table should be dropped."
    )


def test_every_orm_table_exists_in_the_database(
    live_inspector: sa.Inspector, orm_tables: dict
) -> None:
    """No table the ORM maps may be absent.

    This is the assertion ``failed_webhook`` would have failed on: it is imported by
    ``services.payments/models.py`` and written to by every webhook that fails after
    signature verification, so its absence turned a recoverable failure into a 500.
    """
    missing = sorted(name for name in orm_tables if not live_inspector.has_table(name))
    assert not missing, (
        f"The ORM maps tables that do not exist in the database: {missing}. Any code "
        "touching them fails at runtime with UndefinedTable. Either a migration is "
        "missing or a model was added without one."
    )


def test_no_orm_column_is_missing_from_the_database(
    live_inspector: sa.Inspector, orm_tables: dict
) -> None:
    """No column the ORM reads may be absent.

    This is the assertion ``provider_event`` failed on. A missing column produces
    ``UndefinedColumn`` the first time a query touches it, which for the webhook path
    means every payment event fails to record.
    """
    drift: dict[str, list[str]] = {}
    for name, table in orm_tables.items():
        if not live_inspector.has_table(name):
            continue  # reported by the test above; avoid a confusing second failure
        live_columns = {c["name"] for c in live_inspector.get_columns(name)}
        absent = sorted({c.name for c in table.columns} - live_columns)
        if absent:
            drift[name] = absent

    assert not drift, (
        f"The ORM reads columns the database does not have: {drift}. These queries "
        "fail at runtime. Note that the unit suite cannot catch this: it builds its "
        "tables from the ORM metadata, so the ORM always agrees with itself."
    )


def test_no_database_column_is_unknown_to_the_orm(
    live_inspector: sa.Inspector, orm_tables: dict
) -> None:
    """No column may exist in the database that the ORM knows nothing about.

    Less severe than a missing column -- nothing crashes -- but a silently-ignored
    column means a write that appears to succeed and stores nothing. That is how the
    ``provider_event`` drift stayed invisible: the table looked populated and healthy
    while five columns of every row were being discarded.
    """
    drift: dict[str, list[str]] = {}
    for name, table in orm_tables.items():
        if not live_inspector.has_table(name):
            continue
        live_columns = {c["name"] for c in live_inspector.get_columns(name)}
        unknown = sorted(live_columns - {c.name for c in table.columns})
        if unknown:
            drift[name] = unknown

    assert not drift, (
        f"The database has columns the ORM does not map: {drift}. Values written to "
        "these are dropped without error, so a write can report success and store "
        "nothing."
    )


def test_hot_path_tables_have_the_indexes_their_queries_need(
    live_inspector: sa.Inspector,
) -> None:
    """Each measured ``Seq Scan`` hot path has an index.

    Derived from ``EXPLAIN`` against the real queries rather than from a guess. The
    indexes are named in migration 0005; this asserts they exist *and* are present in
    the database, so a migration that ran against a different target cannot leave the
    hot path unprotected without this failing.

    ``provider_event``'s is the one that matters most: it serves the webhook replay
    check, which is the hottest correctness path in the system.
    """
    required = {
        "provider_event": {"ix_provider_event_raw_body_hash"},
        "checkout": {
            "ix_checkout_merchant_status",
            "ix_checkout_buyer_created",
            "ix_checkout_price_hash",
        },
        "authorization": {
            "ix_authorization_buyer_status",
            "ix_authorization_checkout",
        },
        "audit_event": {
            "ix_audit_event_merchant_created",
            "ix_audit_event_agent_run",
        },
        "agent_run": {"ix_agent_run_buyer_started", "ix_agent_run_checkout"},
        "tool_call": {"ix_tool_call_run_created"},
        "evidence": {"ix_evidence_run", "ix_evidence_research_session"},
        "failed_webhook": {"ix_failed_webhook_retry"},
    }

    absent: dict[str, list[str]] = {}
    for table, expected in required.items():
        if not live_inspector.has_table(table):
            absent[table] = sorted(expected)
            continue
        present = {i["name"] for i in live_inspector.get_indexes(table)}
        missing = sorted(expected - present)
        if missing:
            absent[table] = missing

    assert not absent, (
        f"Hot-path tables are missing indexes their measured queries need: {absent}. "
        "Every one of these was a full table scan before migration 0005."
    )


def test_unmapped_database_tables_are_reported(
    live_inspector: sa.Inspector, orm_tables: dict
) -> None:
    """Tables with no ORM mapping are a known, enumerated gap.

    Eight tables are read by raw SQL rather than by an ORM class. Autogenerate
    therefore proposes *dropping* them, which is a real hazard. Rather than pretend
    they do not exist, this pins the exact set: adding a ninth makes this fail, which
    forces a decision about mapping it or documenting it.
    """
    known_unmapped = {
        "agent_run",
        "audit_event",
        "evidence",
        "negotiation_round",
        "product_embedding",
        "recommendation",
        "research_session",
        "tool_call",
    }

    live_tables = set(live_inspector.get_table_names()) - {"alembic_version"}
    unmapped = live_tables - set(orm_tables)

    assert unmapped == known_unmapped, (
        "The set of database tables with no ORM mapping has changed. Expected "
        f"{sorted(known_unmapped)}, found {sorted(unmapped)}. A new unmapped table "
        "means autogenerate will propose dropping it -- map it or add it to this set "
        "deliberately."
    )
