"""Commerce channel console: connections, sync runs, and order pushes.

The endpoints here back the merchant console's channel screens. Two things
changed from the version they replace:

* **Connections are persisted.** The registry was in-memory, so every store
  connection vanished on restart and the console's "connected" state described
  the current process rather than the deployment.
* **Sync outcomes are logged.** Registration used to fire a sync and return its
  result in the response, with nothing written down. A merchant who came back
  the next day could not tell whether the sync ever ran, and neither could the
  console. :class:`~services.connectors.models.ChannelSyncRun` records each
  attempt, including the failures.

The token is write-only over HTTP. There is no endpoint that returns a stored
access token, because a merchant who has lost their Shopify token can revoke and
reissue one in the Shopify admin; a deployment that echoes stored tokens has
made every console read a credential disclosure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from apps.api.auth import require_roles, settings_for
from apps.api.db import get_db
from apps.api.envelope import success
from packages.errors.exceptions import DomainError, NotFoundError, ValidationError
from packages.observability.logging import get_logger
from packages.security.principals import Principal, Role
from services.connectors import channels
from services.connectors.ecommerce_platform import ShopifyWooConnector
from services.connectors.feed import CatalogFeedConnector
from services.connectors.generic_rest import GenericRestConnector
from services.connectors.registry import GLOBAL_CONNECTOR_REGISTRY

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])
MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN)),
]
DbSession = Annotated[Session, Depends(get_db)]


class ConnectStoreRequest(BaseModel):
    """Register or re-register a store connection.

    ``merchant_id`` is a *target*, checked against the caller's own tenant by
    :meth:`Principal.acting_on`, and defaults to the caller's. A merchant admin
    therefore cannot name another tenant here.
    """

    model_config = ConfigDict(extra="forbid")

    merchant_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    platform_type: str = Field(..., min_length=1, max_length=32)
    store_url: str = Field(..., min_length=1, max_length=512)
    access_token: str = Field(..., min_length=1, max_length=512)
    label: str = Field(default="", max_length=200)
    #: Sync immediately after saving. Off by default: a sync against a live
    #: Shopify store is a multi-second API call made inside a request, and an
    #: operator registering four stores should not wait 30 seconds to find out
    #: the third token is wrong.
    sync_now: bool = False


class SyncNowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(..., min_length=1, max_length=64)
    #: Products to pull in one pass. Capped because a first sync against a real
    #: store with 10,000 products would otherwise hold a request open until the
    #: client gives up.
    limit: int = Field(default=100, ge=1, le=250)


class PushOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: str = Field(..., min_length=1, max_length=64)
    order_id: str = Field(..., min_length=1, max_length=64)
    checkout: dict[str, Any] = Field(default_factory=dict)


def _target_merchant(principal: Principal, requested: str | None) -> str:
    """The tenant this request acts on, after the caller's own ceiling is applied."""
    resolved = requested or principal.merchant_id
    if not resolved:
        raise ValidationError("No merchant tenant is associated with this session.")
    acted = principal.acting_on(resolved)
    return acted.merchant_id or resolved


@router.get("/connections")
def list_connections(
    principal: MerchantPrincipal,
    session: DbSession,
    limit: int = Query(
        default=channels.DEFAULT_CONNECTION_PAGE_SIZE,
        ge=1,
        le=channels.MAX_CONNECTION_PAGE_SIZE,
    ),
) -> dict[str, Any]:
    """Every persisted store connection for the caller's tenant, newest first.

    ``limit`` is bounded because the result set scales with how many integrations a
    merchant has connected. The ceiling is enforced here as well as in the service so
    an out-of-range value is a 422 at the edge rather than a silently clamped query.
    """
    merchant_id = _target_merchant(principal, None)
    return success(
        {
            "merchant_id": merchant_id,
            "limit": limit,
            "connections": [
                item.as_dict()
                for item in channels.list_connections(session, merchant_id, limit=limit)
            ],
        }
    )


@router.post("/connections")
def connect_store(
    body: ConnectStoreRequest,
    principal: MerchantPrincipal,
    request: Request,
    session: DbSession,
) -> dict[str, Any]:
    """Save a store connection, encrypting the access token at rest.

    Re-registering the same store updates the existing row rather than adding a
    second one, so a merchant who rotates their Shopify token does not end up
    with two connections syncing the same products.
    """
    merchant_id = _target_merchant(principal, body.merchant_id)
    platform = body.platform_type.lower()
    if platform not in channels.CREDENTIALED_PLATFORMS:
        raise ValidationError(
            f"'{body.platform_type}' is not a credentialed platform. "
            f"Supported: {', '.join(sorted(channels.CREDENTIALED_PLATFORMS))}.",
            details={"field": "platform_type"},
        )

    key = settings_for(request).channel_key
    try:
        row = channels.save_connection(
            session,
            merchant_id=merchant_id,
            platform_type=platform,
            store_domain=body.store_url,
            access_token=body.access_token,
            encryption_key=key,
            label=body.label,
        )
    except channels.ChannelError as exc:
        raise ValidationError(str(exc), details={"field": "platform_type"}) from exc
    except ValueError as exc:
        # The connector's anti-SSRF gate refuses loopback and metadata targets.
        # Surfaced as a 422 naming the field rather than a 500, because it is the
        # merchant who typed the domain.
        raise ValidationError(str(exc), details={"field": "store_url"}) from exc

    connector = _build_connector(merchant_id, platform, body.store_url, body.access_token)
    GLOBAL_CONNECTOR_REGISTRY.register(merchant_id, connector)

    sync_result: dict[str, Any] | None = None
    if body.sync_now:
        sync_result = _run_sync(session, row, connector, limit=100)

    return success(
        {
            "connection_id": row.connection_id,
            "connection": _summary_for(session, row).as_dict(),
            "sync_result": sync_result,
        }
    )


def _summary_for(session: Session, row: Any) -> channels.ConnectionSummary:
    """Re-read a saved connection as a console-safe summary.

    Round-tripped through the query rather than hand-built from the row so the
    create response and the list response cannot drift into describing a
    connection differently.
    """
    from services.connectors.models import ChannelConnection

    fresh = session.get(ChannelConnection, row.connection_id)
    if fresh is None:  # pragma: no cover - the row was just flushed
        raise NotFoundError("Connection disappeared.", details={"reason": "not_found"})
    return channels.summarize(fresh)


@router.post("/sync")
def sync_now(
    body: SyncNowRequest,
    principal: MerchantPrincipal,
    request: Request,
    session: DbSession,
) -> dict[str, Any]:
    """Pull the catalog now and record the attempt."""
    merchant_id = _target_merchant(principal, None)
    try:
        row = channels.get_connection(session, merchant_id, body.connection_id)
    except channels.ChannelError as exc:
        raise NotFoundError("No such connection.", details={"reason": "not_found"}) from exc

    connector = GLOBAL_CONNECTOR_REGISTRY.get(merchant_id)
    if connector.merchant_id != merchant_id:
        # The registry lost this tenant (a restart without rehydration, or a
        # connection whose key no longer decrypts). Rehydrating this one row is
        # better than a 500, and better than silently syncing the demo seed and
        # reporting demo counts as this store's.
        try:
            token = channels.decrypt_token(row, settings_for(request).channel_key)
        except channels.ChannelError as exc:
            raise ValidationError(str(exc), details={"field": "access_token"}) from exc
        connector = _build_connector(merchant_id, row.platform_type, row.store_domain, token)
        GLOBAL_CONNECTOR_REGISTRY.register(merchant_id, connector)

    return success(_run_sync(session, row, connector, limit=body.limit))


@router.get("/sync-runs")
def list_sync_runs(
    principal: MerchantPrincipal, session: DbSession, connection_id: str
) -> dict[str, Any]:
    """Recent sync attempts for one connection, newest first."""
    merchant_id = _target_merchant(principal, None)
    try:
        channels.get_connection(session, merchant_id, connection_id)
    except channels.ChannelError as exc:
        raise NotFoundError("No such connection.", details={"reason": "not_found"}) from exc
    return success(
        {
            "connection_id": connection_id,
            "runs": [item.as_dict() for item in channels.list_sync_runs(session, connection_id)],
        }
    )


@router.post("/orders/push")
def push_order(
    body: PushOrderRequest,
    principal: MerchantPrincipal,
    request: Request,
    session: DbSession,
) -> dict[str, Any]:
    """Push a confirmed order to the store it was sold from.

    Refuses to push the same order twice. A push that reached the store but timed
    out on the response is indistinguishable from one that never arrived, and
    retrying it blindly creates a duplicate order for a buyer already charged.
    """
    merchant_id = _target_merchant(principal, None)
    try:
        row = channels.get_connection(session, merchant_id, body.connection_id)
    except channels.ChannelError as exc:
        raise NotFoundError("No such connection.", details={"reason": "not_found"}) from exc

    connector = GLOBAL_CONNECTOR_REGISTRY.get(merchant_id)
    if connector.merchant_id != merchant_id:
        try:
            token = channels.decrypt_token(row, settings_for(request).channel_key)
        except channels.ChannelError as exc:
            raise ValidationError(str(exc), details={"field": "access_token"}) from exc
        connector = _build_connector(merchant_id, row.platform_type, row.store_domain, token)
        GLOBAL_CONNECTOR_REGISTRY.register(merchant_id, connector)

    try:
        result = connector.push_order(body.order_id, body.checkout)
    except Exception as exc:  # noqa: BLE001 - a store failure is data, not a crash
        # Recorded and returned as a normal response with a failed status. A
        # 500 here would tell the merchant nothing and lose the attempt, which
        # is the one thing they need in order to retry safely.
        summary = channels.record_order_push(
            session,
            row,
            order_id=body.order_id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        session.commit()
        return success({"push": summary.as_dict()})

    summary = channels.record_order_push(
        session,
        row,
        order_id=body.order_id,
        status="success",
        remote_reference=str((result or {}).get("id") or (result or {}).get("order_id") or ""),
    )
    session.commit()
    return success({"push": summary.as_dict()})


@router.get("/order-pushes")
def list_order_pushes(
    principal: MerchantPrincipal, session: DbSession, connection_id: str
) -> dict[str, Any]:
    """Recent order push attempts for one connection, newest first."""
    merchant_id = _target_merchant(principal, None)
    try:
        channels.get_connection(session, merchant_id, connection_id)
    except channels.ChannelError as exc:
        raise NotFoundError("No such connection.", details={"reason": "not_found"}) from exc
    return success(
        {
            "connection_id": connection_id,
            "pushes": [
                item.as_dict() for item in channels.list_order_pushes(session, connection_id)
            ],
        }
    )


@router.delete("/connections/{connection_id}")
def disconnect_store(
    connection_id: str, principal: MerchantPrincipal, session: DbSession
) -> dict[str, Any]:
    """Mark a connection disabled.

    A soft delete rather than a row removal: the connection's sync and order-push
    history is the only record of what was sent to the store, and dropping the row
    would take that with it. A disabled connection is also skipped by
    :func:`services.connectors.channels.rehydrate`, so it does not resume syncing
    after a restart.
    """
    from sqlalchemy import select

    from services.connectors.models import ChannelConnection

    merchant_id = _target_merchant(principal, None)
    row = session.execute(
        select(ChannelConnection).where(
            ChannelConnection.connection_id == connection_id,
            ChannelConnection.merchant_id == merchant_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("No such connection.", details={"reason": "not_found"})
    row.status = "disabled"
    session.commit()
    return success({"connection_id": connection_id, "status": "disabled"})


def _build_connector(merchant_id: str, platform: str, store_url: str, token: str) -> Any:
    """The live connector for a platform, raising on an unusable store domain."""
    if platform == "shopify":
        return ShopifyWooConnector(
            merchant_id=merchant_id,
            platform_flavor="shopify",
            store_domain=store_url,
            access_token=token,
        )
    if platform == "woocommerce":
        return ShopifyWooConnector(
            merchant_id=merchant_id,
            platform_flavor="woocommerce",
            store_domain=store_url,
            access_token=token,
        )
    if platform == "generic_rest":
        return GenericRestConnector(merchant_id=merchant_id, base_url=store_url, api_key=token)
    if platform == "catalog_feed":
        return CatalogFeedConnector(merchant_id=merchant_id, feed_content=token)
    raise channels.ChannelError(f"unsupported platform {platform}")


def _run_sync(
    session: Session,
    row: Any,
    connector: Any,
    *,
    limit: int,
) -> dict[str, Any]:
    """Sync a connector, recording the outcome whatever it is.

    The try covers the whole sync, not just the fetch, because the interesting
    failures are the store's: a 401 from a revoked token, a 429 from rate
    limiting, a schema change that makes a variant unparseable. All of them are
    data the merchant needs to see, and none of them should become a 500 that
    loses the attempt.

    The counts are only written on success -- see
    :func:`services.connectors.channels.record_sync`. A failed run that reported
    zero products would otherwise overwrite a real catalog size, and the console
    would show a connection that had lost its catalog.
    """
    started = datetime.now(UTC)
    try:
        products = connector.fetch_products(limit=limit)
        offers = connector.fetch_offers(limit=limit)
        status = "success"
        error_message: str | None = None
    except DomainError as exc:
        # The store refused us, or could not be reached. The connector already
        # wrote an operator-readable message ("the token has been revoked", "the
        # store rate-limited this sync"), and it is recorded verbatim because it
        # is the line the merchant reads to decide what to do next.
        logger.warning(
            "channel sync refused by the store",
            extra={
                "event": "CHANNEL_SYNC_REFUSED",
                "connection_id": row.connection_id,
                "error_code": exc.code.value,
            },
        )
        products, offers = [], []
        status = "failed"
        error_message = exc.message
    except Exception as exc:  # noqa: BLE001 - the outcome is recorded either way
        logger.warning(
            "channel sync failed",
            extra={
                "event": "CHANNEL_SYNC_FAILED",
                "connection_id": row.connection_id,
                "error_kind": type(exc).__name__,
            },
        )
        products, offers = [], []
        status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"

    run = channels.record_sync(
        session,
        row,
        status=status,
        product_count=len(products),
        offer_count=len(offers),
        error_message=error_message,
        started_at=started,
    )
    session.commit()
    return channels.summarize_run(run).as_dict()
