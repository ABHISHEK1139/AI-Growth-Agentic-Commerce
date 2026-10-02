"""Storefront customers: their Shopify Admin token, and persisted channel connections.

The connector registry was in-memory and lost every registration when the process
recycled, so a merchant's Shopify connection existed only until the next deploy
and the console's "connected" badge was true only of the current process. This
module is the persistence behind it, plus the append-only sync log the console
reads instead of asserting that a sync works.

The token is stored through :func:`services.connectors.models.encrypt_secret`,
not in plaintext and not hashed. Hashed is impossible -- every sync has to
replay it to the store's API -- so the choice was plaintext or encrypted, and
this was plaintext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.observability.context import new_id
from services.connectors.base import PlatformConnector
from services.connectors.models import (
    ChannelConnection,
    ChannelOrderPush,
    ChannelSyncRun,
    decrypt_secret,
    encrypt_secret,
)
from services.connectors.registry import ConnectorRegistry

#: Platforms whose credentials are stored. `internal_seed` is deliberately
#: absent: it is the in-memory demo store, it holds no third-party credential,
#: and persisting it would let a demo tenant masquerade as a real connection in
#: the console.
CREDENTIALED_PLATFORMS = frozenset({"shopify", "woocommerce", "generic_rest", "catalog_feed"})


class ChannelError(Exception):
    """A channel operation that could not be completed."""


@dataclass(frozen=True, slots=True)
class ConnectionSummary:
    """A connection as the console is allowed to see it.

    The access token is absent by construction rather than redacted at the edge.
    A field that exists and is blank invites a future endpoint to populate it; a
    field that does not exist cannot leak.
    """

    connection_id: str
    merchant_id: str
    platform_type: str
    store_domain: str
    label: str
    status: str
    product_count: int
    offer_count: int
    last_sync_status: str | None
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "merchant_id": self.merchant_id,
            "platform_type": self.platform_type,
            "store_domain": self.store_domain,
            "label": self.label,
            "status": self.status,
            "product_count": self.product_count,
            "offer_count": self.offer_count,
            "last_sync_status": self.last_sync_status,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SyncRunSummary:
    sync_run_id: str
    connection_id: str
    status: str
    product_count: int
    offer_count: int
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None

    def as_dict(self) -> dict[str, object]:
        return {
            "sync_run_id": self.sync_run_id,
            "connection_id": self.connection_id,
            "status": self.status,
            "product_count": self.product_count,
            "offer_count": self.offer_count,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


@dataclass(frozen=True, slots=True)
class OrderPushSummary:
    push_id: str
    order_id: str
    status: str
    remote_reference: str | None
    error_message: str | None
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "push_id": self.push_id,
            "order_id": self.order_id,
            "status": self.status,
            "remote_reference": self.remote_reference,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
        }


def summarize(row: ChannelConnection) -> ConnectionSummary:
    """Console-safe view of one connection. Public because the router needs it too."""
    return _to_summary(row)


def summarize_run(row: ChannelSyncRun) -> SyncRunSummary:
    """Console-safe view of one sync run. The counterpart to :func:`summarize`."""
    return SyncRunSummary(
        sync_run_id=row.sync_run_id,
        connection_id=row.connection_id,
        status=row.status,
        product_count=row.product_count,
        offer_count=row.offer_count,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _to_summary(row: ChannelConnection) -> ConnectionSummary:
    return ConnectionSummary(
        connection_id=row.connection_id,
        merchant_id=row.merchant_id,
        platform_type=row.platform_type,
        store_domain=row.store_domain,
        label=row.label,
        status=row.status,
        product_count=row.product_count,
        offer_count=row.offer_count,
        last_sync_status=row.last_sync_status,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        created_at=row.created_at,
    )


def save_connection(
    session: Session,
    *,
    merchant_id: str,
    platform_type: str,
    store_domain: str,
    access_token: str,
    encryption_key: bytes,
    label: str = "",
) -> ChannelConnection:
    """Persist a store connection, or update the one already registered for it.

    Update rather than a second row, because the unique index is on
    (merchant, platform, domain): re-registering a store the merchant already
    connected should refresh the token, not create a second connection that
    syncs the same products again.
    """
    platform = platform_type.lower()
    if platform not in CREDENTIALED_PLATFORMS:
        raise ChannelError(f"{platform} does not take a stored credential")

    existing = session.execute(
        select(ChannelConnection).where(
            ChannelConnection.merchant_id == merchant_id,
            ChannelConnection.platform_type == platform,
            ChannelConnection.store_domain == store_domain,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.access_token_encrypted = encrypt_secret(access_token, encryption_key)
        existing.status = "active"
        existing.label = label or existing.label
        # Cleared so the console cannot show a stale success badge over a token
        # that was just replaced. The next sync repopulates it.
        existing.last_error = None
        session.flush()
        return existing

    row = ChannelConnection(
        connection_id=new_id("chn"),
        merchant_id=merchant_id,
        platform_type=platform,
        store_domain=store_domain,
        access_token_encrypted=encrypt_secret(access_token, encryption_key),
        label=label or f"{platform} · {store_domain}",
        status="active",
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        # Two registrations of the same store raced. The loser updates the
        # winner's row instead of failing, because from the merchant's point of
        # view both requests asked for the same connection.
        session.rollback()
        return save_connection(
            session,
            merchant_id=merchant_id,
            platform_type=platform,
            store_domain=store_domain,
            access_token=access_token,
            encryption_key=encryption_key,
            label=label,
        )
    return row


#: Default and ceiling for :func:`list_connections`. A merchant with many store
#: connections is plausible (one per marketplace integration, plus historical rows), so
#: the listing is bounded rather than open-ended. The ceiling is high enough that the
#: realistic tenant is unaffected and low enough that a runaway tenant cannot turn one
#: request into a full-table read and serialise.
DEFAULT_CONNECTION_PAGE_SIZE = 100
MAX_CONNECTION_PAGE_SIZE = 500


def list_connections(
    session: Session,
    merchant_id: str,
    *,
    limit: int = DEFAULT_CONNECTION_PAGE_SIZE,
) -> list[ConnectionSummary]:
    """Every persisted store connection for a tenant, newest first.

    Bounded by ``limit`` because this feeds a tenant-facing listing rather than a
    fixed-size result. Callers that need the complete set (a migration, an export) must
    pass an explicit ``limit`` large enough to be a conscious decision.
    """
    rows = session.execute(
        select(ChannelConnection)
        .where(ChannelConnection.merchant_id == merchant_id)
        .order_by(ChannelConnection.created_at.desc())
        .limit(min(limit, MAX_CONNECTION_PAGE_SIZE))
    ).scalars()
    return [_to_summary(row) for row in rows]


def get_connection(session: Session, merchant_id: str, connection_id: str) -> ChannelConnection:
    row = session.execute(
        select(ChannelConnection).where(
            ChannelConnection.connection_id == connection_id,
            ChannelConnection.merchant_id == merchant_id,
        )
    ).scalar_one_or_none()
    if row is None:
        # Scoped to the tenant rather than looked up by id alone: a connection id
        # from another merchant is not "not found" in the sense of being absent,
        # and treating it as a plain 404 would confirm the id exists somewhere.
        raise ChannelError("no such connection")
    return row


def decrypt_token(row: ChannelConnection, encryption_key: bytes) -> str:
    """The plaintext access token, for the duration of one API call.

    Raises :class:`ChannelError` rather than propagating the underlying
    :class:`ValueError`, because the actionable message to a merchant is "this
    connection's key no longer decrypts, re-enter the token" -- not a stack trace
    about an envelope version they have never heard of.
    """
    try:
        return decrypt_secret(row.access_token_encrypted, encryption_key)
    except ValueError as exc:
        raise ChannelError(
            "This connection's stored credential could not be decrypted. "
            "CHANNEL_ENCRYPTION_KEY has probably changed since it was saved; "
            "re-enter the store access token."
        ) from exc


def record_sync(
    session: Session,
    connection: ChannelConnection,
    *,
    status: str,
    product_count: int,
    offer_count: int,
    error_message: str | None,
    started_at: datetime | None = None,
) -> ChannelSyncRun:
    """Append one sync attempt and roll its outcome onto the connection.

    A failed sync is recorded as a run rather than only as ``last_error`` on the
    connection, because "it failed once" and "it has failed every time since" are
    different problems and only a log can tell them apart.
    """
    now = started_at or datetime.now(UTC)
    run = ChannelSyncRun(
        sync_run_id=new_id("syn"),
        connection_id=connection.connection_id,
        merchant_id=connection.merchant_id,
        status=status,
        product_count=product_count,
        offer_count=offer_count,
        error_message=error_message,
        started_at=now,
        finished_at=datetime.now(UTC),
    )
    session.add(run)

    connection.last_sync_status = status
    connection.last_synced_at = run.finished_at
    connection.last_error = error_message
    if status == "success":
        # Only a clean sync updates the counts. Writing them from a partial run
        # would replace a real catalog size with a truncated one, and the console
        # would report a successful sync that lost products.
        connection.product_count = product_count
        connection.offer_count = offer_count
    session.flush()
    return run


def list_sync_runs(
    session: Session, connection_id: str, *, limit: int = 25
) -> list[SyncRunSummary]:
    rows = session.execute(
        select(ChannelSyncRun)
        .where(ChannelSyncRun.connection_id == connection_id)
        .order_by(ChannelSyncRun.started_at.desc())
        .limit(limit)
    ).scalars()
    return [summarize_run(row) for row in rows]


def record_order_push(
    session: Session,
    connection: ChannelConnection,
    *,
    order_id: str,
    status: str,
    remote_reference: str | None = None,
    error_message: str | None = None,
) -> OrderPushSummary:
    """Append one order push attempt.

    A push that already succeeded is reported as ``duplicate`` and not repeated.
    The failure this prevents is concrete: a push that reached Shopify but timed
    out on the way back, retried by the operator, creating a second order for a
    buyer who has already been charged once.
    """
    existing = session.execute(
        select(ChannelOrderPush).where(
            ChannelOrderPush.connection_id == connection.connection_id,
            ChannelOrderPush.order_id == order_id,
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "success":
        return OrderPushSummary(
            push_id=existing.push_id,
            order_id=existing.order_id,
            status="duplicate",
            remote_reference=existing.remote_reference,
            error_message=(
                "This order was already pushed to the store successfully; not sending it again."
            ),
            created_at=existing.created_at,
        )

    push = ChannelOrderPush(
        push_id=new_id("push"),
        connection_id=connection.connection_id,
        merchant_id=connection.merchant_id,
        order_id=order_id,
        status=status,
        remote_reference=remote_reference,
        error_message=error_message,
    )
    if existing is None:
        session.add(push)
    else:
        # A previous attempt failed, so this one is the retry. Updating the row
        # rather than appending keeps the one-push-per-order invariant that makes
        # the duplicate check above meaningful.
        existing.status = status
        existing.remote_reference = remote_reference
        existing.error_message = error_message
        existing.created_at = datetime.now(UTC)
        push = existing
    session.flush()
    return OrderPushSummary(
        push_id=push.push_id,
        order_id=push.order_id,
        status=push.status,
        remote_reference=push.remote_reference,
        error_message=push.error_message,
        created_at=push.created_at,
    )


def list_order_pushes(
    session: Session, connection_id: str, *, limit: int = 25
) -> list[OrderPushSummary]:
    rows = session.execute(
        select(ChannelOrderPush)
        .where(ChannelOrderPush.connection_id == connection_id)
        .order_by(ChannelOrderPush.created_at.desc())
        .limit(limit)
    ).scalars()
    return [
        OrderPushSummary(
            push_id=row.push_id,
            order_id=row.order_id,
            status=row.status,
            remote_reference=row.remote_reference,
            error_message=row.error_message,
            created_at=row.created_at,
        )
        for row in rows
    ]


def rehydrate(registry: ConnectorRegistry, session: Session, encryption_key: bytes) -> int:
    """Rebuild the in-memory registry from the database. Returns the count loaded.

    Called once at startup. Without it the registry is empty on every boot, so a
    merchant's connection silently stops syncing until they re-enter it, and the
    console shows no connections at all -- which reads as "nothing is connected"
    rather than "the process forgot".
    """
    rows = session.execute(select(ChannelConnection)).scalars()
    loaded = 0
    for row in rows:
        if row.status != "active":
            continue
        try:
            token = decrypt_token(row, encryption_key)
        except ChannelError:
            # Left out of the registry rather than loaded with an empty token: a
            # connector that cannot authenticate produces a confusing 401 from
            # the store on every sync. Absent, the console says the key no longer
            # decrypts, which is the true and actionable statement.
            continue
        connector = _connector_for(row, token)
        if connector is not None:
            registry.register(row.merchant_id, connector)
            loaded += 1
    return loaded


def _connector_for(row: ChannelConnection, token: str) -> PlatformConnector | None:
    """Rebuild the connector implementation for a stored connection.

    Returns ``None`` for a platform with no credentialed implementation rather
    than a partially-built connector, so a row for an unsupported platform is
    skipped instead of loaded and then failing on every call.
    """
    from services.connectors.ecommerce_platform import ShopifyWooConnector
    from services.connectors.feed import CatalogFeedConnector
    from services.connectors.generic_rest import GenericRestConnector

    if row.platform_type == "shopify":
        return ShopifyWooConnector(
            merchant_id=row.merchant_id,
            platform_flavor="shopify",
            store_domain=row.store_domain,
            access_token=token,
        )
    if row.platform_type == "woocommerce":
        return ShopifyWooConnector(
            merchant_id=row.merchant_id,
            platform_flavor="woocommerce",
            store_domain=row.store_domain,
            access_token=token,
        )
    if row.platform_type == "generic_rest":
        return GenericRestConnector(
            merchant_id=row.merchant_id,
            base_url=row.store_domain,
            api_key=token,
        )
    if row.platform_type == "catalog_feed":
        return CatalogFeedConnector(merchant_id=row.merchant_id, feed_content=token)
    return None


__all__ = [
    "CREDENTIALED_PLATFORMS",
    "ChannelError",
    "ConnectionSummary",
    "OrderPushSummary",
    "SyncRunSummary",
    "decrypt_token",
    "get_connection",
    "list_connections",
    "list_order_pushes",
    "list_sync_runs",
    "rehydrate",
    "record_order_push",
    "record_sync",
    "save_connection",
]
