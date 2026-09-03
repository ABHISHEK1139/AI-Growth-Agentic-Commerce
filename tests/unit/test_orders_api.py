"""The session-authenticated buyer order surface (Requirements 24.5, 24.6).

These tests drive the real routes over a real SQLite session and the real
:class:`services.orders.repository.OrderRepository`, because the property under
test is a property of the *query*: a mocked session would happily return whatever
rows the mock was told to return and would prove nothing about scoping.

The ``order`` table is created from the shipped ORM metadata. One accommodation is
needed for SQLite: ``JSONB`` is a PostgreSQL type with no SQLite rendering, so a
dialect-specific compiler maps it to ``JSON`` for DDL. Nothing under test reads a
``JSONB`` column, and the production dialect is untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.db import Base, get_db
from packages.errors.registry import ErrorCode
from packages.security.principals import Role
from packages.security.tokens import issue_session_token
from services.orders.models import Order
from services.orders.service import MAX_ORDER_PAGE_SIZE

MERCHANT = "mrc_demo_electronics"
RIVAL_MERCHANT = "mrc_rival"
BUYER = "buy_ada"
OTHER_BUYER = "buy_grace"


@compiles(JSONB, "sqlite")
def _render_jsonb_as_json(type_: Any, compiler: Any, **kw: Any) -> str:
    """DDL only. SQLite has no JSONB; the column is never read by these tests."""
    return "JSON"


def _make_order(
    order_id: str,
    *,
    buyer_id: str = BUYER,
    merchant_id: str = MERCHANT,
    total_minor: int = 6899900,
    minutes_ago: int = 0,
    status: str = "confirmed",
) -> Order:
    confirmed_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return Order(
        order_id=order_id,
        order_number=f"ORD-{order_id.upper()}",
        checkout_id=f"chk_{order_id}",
        payment_id=f"pay_{order_id}",
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        status=status,
        total_minor=total_minor,
        currency="INR",
        shipping_address=None,
        confirmed_at=confirmed_at,
        created_at=confirmed_at,
    )


@pytest.fixture
def order_session() -> Iterator[Session]:
    # `StaticPool` plus `check_same_thread=False` because a sync FastAPI endpoint
    # runs in Starlette's threadpool, so the handler touches this connection from a
    # thread other than the one the fixture created it on.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Order.__table__])
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def order_client(app: FastAPI, order_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: order_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _sign_in(
    client: TestClient,
    app: FastAPI,
    *,
    role: Role = Role.BUYER,
    buyer_id: str | None = BUYER,
    merchant_id: str = MERCHANT,
) -> None:
    """Attach a session cookie for ``buyer_id``, exactly as the web surface would."""
    issued = issue_session_token(
        secret=app.state.settings.session_secret,
        subject=buyer_id or "usr_operator",
        role=role,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        ttl_seconds=3600,
    )
    client.cookies.set("agentpay_session", issued.token)


# ---------------------------------------------------------------------------
# The list returns only the caller's own orders
# ---------------------------------------------------------------------------


def test_list_returns_only_the_callers_orders(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    order_session.add_all(
        [
            _make_order("ord_mine_new", minutes_ago=1),
            _make_order("ord_mine_old", minutes_ago=90),
            _make_order("ord_other_buyer", buyer_id=OTHER_BUYER),
            _make_order("ord_other_tenant", merchant_id=RIVAL_MERCHANT),
        ]
    )
    order_session.flush()

    _sign_in(order_client, app)
    response = order_client.get("/api/v1/orders")

    assert response.status_code == 200
    data = response.json()["data"]
    # Newest first, and nothing belonging to another buyer or another tenant.
    assert [order["order_id"] for order in data["orders"]] == ["ord_mine_new", "ord_mine_old"]
    assert data["total"] == 2
    assert data["count"] == 2
    assert all(order["buyer_id"] == BUYER for order in data["orders"])
    assert all(order["merchant_id"] == MERCHANT for order in data["orders"])


def test_list_is_empty_rather_than_failing_for_a_buyer_with_no_orders(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    order_session.add(_make_order("ord_someone_else", buyer_id=OTHER_BUYER))
    order_session.flush()

    _sign_in(order_client, app)
    response = order_client.get("/api/v1/orders")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "orders": [],
        "count": 0,
        "total": 0,
        "limit": 20,
        "offset": 0,
    }


def test_list_returns_the_order_v1_shape(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    """The response is the existing schema, not a bespoke projection."""
    from packages.schemas.v1 import OrderV1

    order_session.add(_make_order("ord_shape"))
    order_session.flush()

    _sign_in(order_client, app)
    payload = order_client.get("/api/v1/orders").json()["data"]["orders"][0]

    order = OrderV1.model_validate(payload)
    assert order.order_id == "ord_shape"
    assert order.amount_minor == 6899900
    assert order.currency == "INR"


# ---------------------------------------------------------------------------
# Cross-buyer and cross-tenant fetches are refused
# ---------------------------------------------------------------------------


def test_cross_buyer_fetch_is_refused(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    order_session.add(_make_order("ord_grace", buyer_id=OTHER_BUYER))
    order_session.flush()

    _sign_in(order_client, app)
    response = order_client.get("/api/v1/orders/ord_grace")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND
    # The refusal must not confirm that the identifier belongs to someone.
    assert OTHER_BUYER not in response.text


def test_cross_tenant_fetch_is_refused(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    order_session.add(_make_order("ord_rival", merchant_id=RIVAL_MERCHANT))
    order_session.flush()

    _sign_in(order_client, app)
    response = order_client.get("/api/v1/orders/ord_rival")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND
    assert RIVAL_MERCHANT not in response.text


def test_unknown_order_id_is_not_found(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    _sign_in(order_client, app)
    response = order_client.get("/api/v1/orders/ord_does_not_exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == ErrorCode.NOT_FOUND


def test_owned_order_is_returned(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    order_session.add(_make_order("ord_mine"))
    order_session.flush()

    _sign_in(order_client, app)
    response = order_client.get("/api/v1/orders/ord_mine")

    assert response.status_code == 200
    order = response.json()["data"]["order"]
    assert order["order_id"] == "ord_mine"
    assert order["payment_id"] == "pay_ord_mine"
    assert order["amount_minor"] == 6899900


# ---------------------------------------------------------------------------
# Credential requirements
# ---------------------------------------------------------------------------


def test_unauthenticated_read_is_rejected(order_client: TestClient) -> None:
    assert order_client.get("/api/v1/orders").status_code == 401
    assert order_client.get("/api/v1/orders/ord_mine").status_code == 401


def test_a_merchant_operator_cannot_read_the_buyer_surface(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    """This surface answers "my orders". A merchant role has no buyer to be."""
    order_session.add(_make_order("ord_mine"))
    order_session.flush()

    _sign_in(order_client, app, role=Role.MERCHANT_OPERATOR, buyer_id=None)
    response = order_client.get("/api/v1/orders")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.FORBIDDEN


# ---------------------------------------------------------------------------
# The page size is bounded
# ---------------------------------------------------------------------------


def test_page_size_above_the_ceiling_is_rejected(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    _sign_in(order_client, app)

    refused = order_client.get(f"/api/v1/orders?limit={MAX_ORDER_PAGE_SIZE + 1}")
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == ErrorCode.VALIDATION_ERROR

    accepted = order_client.get(f"/api/v1/orders?limit={MAX_ORDER_PAGE_SIZE}")
    assert accepted.status_code == 200
    assert accepted.json()["data"]["limit"] == MAX_ORDER_PAGE_SIZE


def test_page_size_is_also_bounded_below_the_router(order_session: Session) -> None:
    """The bound lives in the service too, so a non-HTTP caller cannot bypass it."""
    from services.orders.service import OrderService

    service = OrderService()
    for bad_limit in (0, -1, MAX_ORDER_PAGE_SIZE + 1):
        with pytest.raises(ValueError, match="limit must be between"):
            service.list_orders_for_buyer(
                order_session,
                buyer_id=BUYER,
                merchant_id=MERCHANT,
                limit=bad_limit,
            )
    with pytest.raises(ValueError, match="offset must not be negative"):
        service.list_orders_for_buyer(
            order_session,
            buyer_id=BUYER,
            merchant_id=MERCHANT,
            offset=-1,
        )


def test_paging_walks_the_buyers_own_orders(
    order_client: TestClient, order_session: Session, app: FastAPI
) -> None:
    order_session.add_all([_make_order(f"ord_{index}", minutes_ago=index) for index in range(5)])
    order_session.add(_make_order("ord_other", buyer_id=OTHER_BUYER))
    order_session.flush()

    _sign_in(order_client, app)
    first = order_client.get("/api/v1/orders?limit=2&offset=0").json()["data"]
    second = order_client.get("/api/v1/orders?limit=2&offset=2").json()["data"]

    assert [order["order_id"] for order in first["orders"]] == ["ord_0", "ord_1"]
    assert [order["order_id"] for order in second["orders"]] == ["ord_2", "ord_3"]
    # `total` is the caller's own count, not the table's.
    assert first["total"] == 5
    assert second["total"] == 5


def test_confirm_order_same_payment_is_idempotent(order_session: Session) -> None:
    """Re-confirming an order with the same payment returns the existing order."""
    from services.orders.service import OrderService

    existing_order = _make_order("ord_1", buyer_id=BUYER)
    order_session.add(existing_order)
    order_session.flush()

    service = OrderService()
    # Same checkout_id and same payment_id returns existing
    schema = service.confirm_order(
        order_session,
        checkout_id=existing_order.checkout_id,
        payment_id=existing_order.payment_id,
    )
    assert schema.order_id == "ord_1"


def test_confirm_order_second_distinct_payment_rejected(order_session: Session) -> None:
    """Attempting to confirm an order for an existing checkout with a different payment raises ALREADY_FINALIZED (BUG-37)."""
    from packages.errors.exceptions import DomainError
    from services.orders.service import OrderService

    existing_order = _make_order("ord_1", buyer_id=BUYER)
    order_session.add(existing_order)
    order_session.flush()

    service = OrderService()
    with pytest.raises(DomainError) as exc_info:
        service.confirm_order(
            order_session,
            checkout_id=existing_order.checkout_id,
            payment_id="pay_second_distinct_payment",
        )
    assert exc_info.value.code == ErrorCode.ALREADY_FINALIZED
