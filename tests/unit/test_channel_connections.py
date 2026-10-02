"""Persisted channel connections and the logs the console reads.

The behaviours under test are the ones a mock cannot check and that the console
depends on: that a re-registered store updates its row rather than duplicating
it, that a failed sync never overwrites a real product count, and that an order
cannot be pushed to the same store twice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from services.connectors import channels
from services.connectors.models import (
    ChannelConnection,
    ChannelOrderPush,
    ChannelSyncRun,
    decrypt_secret,
    encrypt_secret,
)
from services.connectors.registry import ConnectorRegistry

KEY = b"k" * 32
OTHER_KEY = b"j" * 32


@pytest.fixture
def session():
    import services.connectors.models  # noqa: F401 - register the tables
    import tests.sqlite_types  # noqa: F401 - registers the JSONB/ARRAY compilers
    from packages.db.base import Base
    from services.catalog.models import Merchant

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Merchant(merchant_id="merchant_demo", name="Demo", status="active"))
    db.flush()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _connect(session, **overrides):
    kwargs = {
        "merchant_id": "merchant_demo",
        "platform_type": "shopify",
        "store_domain": "mystore.myshopify.com",
        "access_token": "shpat_secret_token_value",
        "encryption_key": KEY,
    }
    kwargs.update(overrides)
    return channels.save_connection(session, **kwargs)


# --- Encryption at rest ---------------------------------------------------


def test_token_is_never_stored_in_plaintext(session) -> None:
    row = _connect(session)

    assert "shpat_secret_token_value" not in row.access_token_encrypted
    assert row.access_token_encrypted.startswith("v1.")


def test_stored_token_round_trips(session) -> None:
    row = _connect(session)

    assert channels.decrypt_token(row, KEY) == "shpat_secret_token_value"


def test_a_different_key_refuses_rather_than_returning_garbage(session) -> None:
    """A wrong key must not yield plausible nonsense.

    Returning corrupted bytes would send a wrong credential to the store on the
    next sync and surface there as a confusing 401, instead of here as "your
    CHANNEL_ENCRYPTION_KEY changed".
    """
    row = _connect(session)

    with pytest.raises(ValueError, match="failed authentication"):
        decrypt_secret(row.access_token_encrypted, OTHER_KEY)


def test_encrypting_the_same_token_twice_differs(session) -> None:
    """Distinct nonces, so the table is not an equality oracle on token values."""

    first = encrypt_secret("same-token", KEY)
    second = encrypt_secret("same-token", KEY)

    assert first != second
    assert decrypt_secret(first, KEY) == decrypt_secret(second, KEY)


# --- Registration ---------------------------------------------------------


def test_re_registering_a_store_updates_rather_than_duplicates(session) -> None:
    """A merchant rotating a token must not end up with two connections.

    Two connections to one store would sync the same products twice, and the
    catalog import would report a duplication bug rather than a duplicate row.
    """
    first = _connect(session)
    second = _connect(session, access_token="shpat_rotated_token")

    session.commit()
    count = session.execute(select(func.count(ChannelConnection.connection_id))).scalar_one()
    assert count == 1
    assert second.connection_id == first.connection_id
    assert channels.decrypt_token(second, KEY) == "shpat_rotated_token"


def test_a_different_store_gets_its_own_connection(session) -> None:
    _connect(session)
    _connect(session, store_domain="second.myshopify.com")

    session.commit()
    assert len(channels.list_connections(session, "merchant_demo")) == 2


def test_an_uncredentialed_platform_is_refused(session) -> None:
    """`internal_seed` is the in-memory demo store and holds no credential.

    Persisting it would let a demo tenant masquerade as a real connection.
    """

    with pytest.raises(channels.ChannelError):
        _connect(session, platform_type="internal_seed")


def test_disconnecting_keeps_the_row_for_audit(session) -> None:
    """The sync and order-push history is the only record of what was sent.

    Dropping the row would take that with it, so this is a status change.
    """
    row = _connect(session)
    row.status = "disabled"
    session.commit()

    stored = session.get(ChannelConnection, row.connection_id)
    assert stored is not None
    assert stored.status == "disabled"


def test_listing_is_scoped_to_one_tenant(session) -> None:
    from services.catalog.models import Merchant

    session.add(Merchant(merchant_id="merchant_other", name="Other", status="active"))
    session.flush()
    _connect(session)
    _connect(session, merchant_id="merchant_other")

    assert len(channels.list_connections(session, "merchant_demo")) == 1
    assert len(channels.list_connections(session, "merchant_other")) == 1


def test_a_connection_from_another_tenant_is_not_found(session) -> None:
    from services.catalog.models import Merchant

    session.add(Merchant(merchant_id="merchant_other", name="Other", status="active"))
    session.flush()
    row = _connect(session)

    with pytest.raises(channels.ChannelError):
        channels.get_connection(session, "merchant_other", row.connection_id)


# --- Sync logging ---------------------------------------------------------


def test_a_clean_sync_writes_the_counts_onto_the_connection(session) -> None:
    row = _connect(session)

    channels.record_sync(
        session,
        row,
        status="success",
        product_count=42,
        offer_count=87,
        error_message=None,
    )

    assert row.product_count == 42
    assert row.offer_count == 87
    assert row.last_sync_status == "success"


def test_a_failed_sync_does_not_overwrite_the_counts(session) -> None:
    """Otherwise a partial run replaces a real catalog size with a truncated one.

    The console would then report a successful-looking connection that had lost
    products, which is the failure mode this rule exists to prevent.
    """
    row = _connect(session)
    channels.record_sync(
        session, row, status="success", product_count=42, offer_count=87, error_message=None
    )

    channels.record_sync(
        session,
        row,
        status="failed",
        product_count=0,
        offer_count=0,
        error_message="ConnectionError: refused",
    )

    assert row.product_count == 42
    assert row.offer_count == 87
    assert row.last_sync_status == "failed"
    assert row.last_error == "ConnectionError: refused"


def test_every_sync_is_recorded_not_just_the_last(session) -> None:
    """ "It failed once" and "it has failed every time" need a log to tell apart."""
    row = _connect(session)
    channels.record_sync(
        session, row, status="failed", product_count=0, offer_count=0, error_message="one"
    )
    channels.record_sync(
        session, row, status="failed", product_count=0, offer_count=0, error_message="two"
    )

    runs = channels.list_sync_runs(session, row.connection_id)
    assert len(runs) == 2
    assert session.execute(select(func.count(ChannelSyncRun.sync_run_id))).scalar_one() == 2


def test_sync_runs_are_newest_first(session) -> None:
    from datetime import UTC, datetime, timedelta

    row = _connect(session)
    base = datetime.now(UTC)
    for offset in range(3):
        channels.record_sync(
            session,
            row,
            status="success",
            product_count=offset,
            offer_count=0,
            error_message=None,
            started_at=base + timedelta(minutes=offset),
        )

    runs = channels.list_sync_runs(session, row.connection_id)
    assert [run.product_count for run in runs] == [2, 1, 0]


# --- Order push idempotency ----------------------------------------------


def test_a_successful_push_is_not_repeated(session) -> None:
    """A push that reached the store but timed out on the response is
    indistinguishable from one that never arrived.

    Retrying it blindly creates a duplicate order in the store for a buyer who
    has already been charged once.
    """
    row = _connect(session)
    first = channels.record_order_push(
        session, row, order_id="ord_1", status="success", remote_reference="gid://1"
    )
    second = channels.record_order_push(
        session, row, order_id="ord_1", status="success", remote_reference="gid://1"
    )

    assert first.status == "success"
    assert second.status == "duplicate"
    assert "already pushed" in (second.error_message or "")


def test_a_failed_push_is_replaced_not_duplicated(session) -> None:
    row = _connect(session)
    channels.record_order_push(
        session, row, order_id="ord_1", status="failed", error_message="timeout"
    )
    retry = channels.record_order_push(
        session, row, order_id="ord_1", status="success", remote_reference="gid://1"
    )

    assert retry.status == "success"
    count = session.execute(select(func.count(ChannelOrderPush.push_id))).scalar_one()
    assert count == 1


def test_pushes_for_different_orders_are_separate(session) -> None:
    row = _connect(session)
    channels.record_order_push(session, row, order_id="ord_1", status="success")
    channels.record_order_push(session, row, order_id="ord_2", status="success")

    assert len(channels.list_order_pushes(session, row.connection_id)) == 2


# --- Rehydration ----------------------------------------------------------


def test_rehydrate_rebuilds_the_registry_from_the_database(session) -> None:
    """Without this, a connection silently stops syncing after every restart.

    The console showed "connected" describing the current process rather than
    the deployment, and an empty registry read as "nothing is connected".
    """
    _connect(session)
    session.commit()
    registry = ConnectorRegistry()

    loaded = channels.rehydrate(registry, session, KEY)

    assert loaded == 1
    connector = registry.get("merchant_demo")
    assert connector.platform_type == "shopify"
    assert connector.config["store_domain"] == "mystore.myshopify.com"


def test_rehydrate_skips_a_disabled_connection(session) -> None:
    row = _connect(session)
    row.status = "disabled"
    session.commit()
    registry = ConnectorRegistry()

    assert channels.rehydrate(registry, session, KEY) == 0
    # Still the demo seed, not the Shopify store.
    assert registry.get("merchant_demo").merchant_id != "merchant_demo"


def test_rehydrate_skips_a_connection_whose_key_no_longer_matches(session) -> None:
    """Left out of the registry rather than loaded with a bad token.

    A connector that cannot authenticate produces a confusing 401 from the store
    on every sync. Absent, the console says the key no longer decrypts, which is
    the true and actionable statement.
    """
    _connect(session)
    session.commit()
    registry = ConnectorRegistry()

    assert channels.rehydrate(registry, session, OTHER_KEY) == 0
    assert registry.get("merchant_demo").merchant_id != "merchant_demo"


def test_rehydrate_is_a_no_op_on_an_empty_database(session) -> None:
    assert channels.rehydrate(ConnectorRegistry(), session, KEY) == 0
