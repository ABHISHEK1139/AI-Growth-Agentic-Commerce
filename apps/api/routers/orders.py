"""Session-authenticated buyer order surface.

The only order endpoint that existed before this module was
``GET /api/v1/agent/orders/{order_id}`` on the public agent surface, behind
:func:`apps.api.auth.token_principal`. A browser presents a session cookie and no
bearer token, so that route is unreachable from the web application by design, and
there was no list endpoint at all — the orders screens had nothing to read.

Two routes are added here, and both are buyer-owned reads:

* ``GET /api/v1/orders``            the caller's own orders, newest first, paged
* ``GET /api/v1/orders/{order_id}`` one order the caller owns

Ownership and tenancy are enforced twice, in two different places, because they are
the whole point of the surface:

1. **In the query.** :class:`services.orders.repository.OrderRepository` is a
   :class:`~packages.db.repository.TenantScopedRepository` declared
   ``requires_buyer_scope``, built from ``principal.tenant_scope()``. Every
   statement carries ``merchant_id = :merchant`` *and* ``buyer_id = :buyer`` before
   it is executable, and the base class refuses a statement it did not build. There
   is no unfiltered query to get wrong.
2. **On the record that came back.** The handler re-checks the returned row with
   :func:`packages.security.authorization.require_ownership`. Under correct scoping
   this can never fire; it is here so that a future change to the query cannot
   quietly turn into a cross-buyer read without a second control failing too.

A row belonging to another buyer or another tenant is answered ``NOT_FOUND``,
identical to an identifier that never existed, so a probing caller learns nothing
from the difference.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.auth import require_roles
from apps.api.db import get_db
from apps.api.envelope import success
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal, Role
from services.orders.service import (
    DEFAULT_ORDER_PAGE_SIZE,
    MAX_ORDER_PAGE_SIZE,
    OrderService,
)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

BuyerPrincipal = Annotated[Principal, Depends(require_roles(Role.BUYER))]
DatabaseSession = Annotated[Session, Depends(get_db)]


@router.get("", summary="List the signed-in buyer's orders")
def list_orders(
    principal: BuyerPrincipal,
    session: DatabaseSession,
    limit: int = Query(default=DEFAULT_ORDER_PAGE_SIZE, ge=1, le=MAX_ORDER_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Newest first. ``total`` is this buyer's own count, for paging."""
    if principal.buyer_id is None:
        raise DomainError("Buyer ID is required", code=ErrorCode.FORBIDDEN)

    service = OrderService()
    orders, total = service.list_orders_for_buyer(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        limit=limit,
        offset=offset,
    )
    return success(
        {
            "orders": [order.model_dump(mode="json") for order in orders],
            "count": len(orders),
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/{order_id}", summary="Fetch one order the signed-in buyer owns")
def get_order(
    order_id: str,
    principal: BuyerPrincipal,
    session: DatabaseSession,
) -> dict[str, Any]:
    if principal.buyer_id is None:
        raise DomainError("Buyer ID is required", code=ErrorCode.FORBIDDEN)

    service = OrderService()
    order = service.get_order_for_buyer(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        order_id=order_id,
    )
    return success({"order": order.model_dump(mode="json")})
