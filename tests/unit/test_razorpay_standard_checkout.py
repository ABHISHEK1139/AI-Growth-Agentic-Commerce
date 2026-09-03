"""Unit and integration tests for Razorpay Standard Web Checkout endpoints."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apps.api.db
from apps.api.config import Settings
from apps.api.db import Base, get_db
from apps.api.main import create_app
from packages.errors.registry import ErrorCode
from services.catalog.models import CatalogVersion, Merchant, Product
from services.inventory.models import Inventory
from services.offers.models import Offer

TEST_KEY_ID = "rzp_test_mock_12345"
TEST_KEY_SECRET = "mock_secret_key_67890"


@compiles(JSONB, "sqlite")
def _render_jsonb_as_json(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


def _app_with_razorpay_keys():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            apps.api.db.text(
                """
                CREATE TABLE IF NOT EXISTS audit_event (
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
                """
            )
        )
        conn.commit()

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as s:
        s.add(
            Merchant(
                merchant_id="mer_demo_electronics",
                name="Demo Electronics",
                status="active",
            )
        )
        cat = CatalogVersion(
            catalog_version_id="cat_demo_1",
            merchant_id="mer_demo_electronics",
            status="published",
            product_count=2,
            valid_count=2,
        )
        s.add(cat)
        prod1 = Product(
            product_id="prod_demo_1",
            catalog_version_id="cat_demo_1",
            merchant_id="mer_demo_electronics",
            external_product_id="ext_prod_1",
            category_id="electronics",
            title="Demo Product 1",
        )
        prod2 = Product(
            product_id="prod_demo_2",
            catalog_version_id="cat_demo_1",
            merchant_id="mer_demo_electronics",
            external_product_id="ext_prod_2",
            category_id="electronics",
            title="Demo Product 2",
        )
        s.add_all([prod1, prod2])
        off1 = Offer(
            offer_id="off_demo_10000",
            catalog_version_id="cat_demo_1",
            product_id="prod_demo_1",
            merchant_id="mer_demo_electronics",
            unit_price_minor=10000,
            currency="INR",
            delivery_days=3,
            return_period_days=14,
            pricing_source="merchant_configured",
            offer_version=1,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        off2 = Offer(
            offer_id="off_demo_25000",
            catalog_version_id="cat_demo_1",
            product_id="prod_demo_2",
            merchant_id="mer_demo_electronics",
            unit_price_minor=25000,
            currency="INR",
            delivery_days=3,
            return_period_days=14,
            pricing_source="merchant_configured",
            offer_version=1,
            status="active",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        s.add_all([off1, off2])
        inv1 = Inventory(
            offer_id="off_demo_10000",
            available_quantity=100,
            reserved_quantity=0,
            version=1,
        )
        inv2 = Inventory(
            offer_id="off_demo_25000",
            available_quantity=100,
            reserved_quantity=0,
            version=1,
        )
        s.add_all([inv1, inv2])
        s.commit()

    apps.api.db._SESSION_FACTORY = session_factory

    settings = Settings(
        payment_provider="razorpay",
        razorpay_key_id=TEST_KEY_ID,
        razorpay_key_secret=TEST_KEY_SECRET,
        default_merchant_id="mer_demo_electronics",
    )
    app = create_app(settings=settings)
    app.state.session_factory = session_factory

    def _override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return app


def _authenticated_client(app) -> TestClient:
    client = TestClient(app)
    client.post(
        "/api/v1/auth/session",
        json={
            "role": "buyer",
            "buyer_id": "buyer_test_123",
            "merchant_id": "mer_demo_electronics",
        },
    )
    return client


def test_create_order_and_verify_unauthenticated_rejected():
    """Unauthenticated calls to /api/create-order and /api/verify-payment return 401 (BUG-24)."""
    app = _app_with_razorpay_keys()
    unauth_client = TestClient(app)

    res_create = unauth_client.post("/api/create-order", json={"amount": 10000})
    assert res_create.status_code == 401

    res_verify = unauth_client.post(
        "/api/verify-payment",
        json={
            "razorpay_order_id": "order_123",
            "razorpay_payment_id": "pay_123",
            "razorpay_signature": "sig_123",
        },
    )
    assert res_verify.status_code == 401


def test_create_razorpay_order_minimum_amount_validation():
    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    # Less than 100 paise should fail validation
    res = client.post("/api/create-order", json={"amount": 50})
    assert res.status_code == 422


def test_create_razorpay_order_missing_credentials():
    app = create_app(settings=Settings(razorpay_key_id="", razorpay_key_secret=""))
    client = _authenticated_client(app)

    res = client.post(
        "/api/create-order",
        json={"amount": 10000, "currency": "INR", "receipt": "test_rcpt_01"},
    )
    assert res.status_code == 500
    assert res.json()["error"]["code"] == ErrorCode.INTERNAL_ERROR


def test_create_razorpay_order_success():
    import uuid

    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    unique_order_id = f"order_test_{uuid.uuid4().hex[:8]}"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": unique_order_id,
        "amount": 10000,
        "currency": "INR",
        "status": "created",
    }

    with patch("services.payments.razorpay_adapter.httpx.Client") as MockClient:
        mock_client_instance = MagicMock()
        mock_client_instance.post.return_value = mock_resp
        MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
        MockClient.return_value.__exit__ = MagicMock(return_value=False)

        rcpt = f"test_rcpt_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/create-order",
            json={"amount": 10000, "currency": "INR", "receipt": rcpt},
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["id"] == unique_order_id
        assert data["amount"] == 10000
        assert data["currency"] == "INR"
        assert data["key_id"] == TEST_KEY_ID


@patch("apps.api.routers.razorpay_checkout.PaymentService")
def test_verify_razorpay_signature_valid(mock_payment_service_class):
    import uuid

    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    order_id = f"order_test_{uuid.uuid4().hex[:8]}"
    payment_id = f"pay_test_{uuid.uuid4().hex[:8]}"
    valid_signature = hmac.new(
        TEST_KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    # Mock the returned order_schema
    mock_service_instance = mock_payment_service_class.return_value
    mock_order = MagicMock()
    mock_order.order_id = "ord_1"
    mock_order.amount_minor = 1000
    mock_service_instance.verify_payment.return_value = (None, mock_order)

    # We also need to mock the session.query(Payment) since the endpoint checks for it now (BUG-58 fix)
    with (
        patch("apps.api.routers.razorpay_checkout.Depends"),
        patch("apps.api.routers.razorpay_checkout.current_principal"),
    ):
        # The test client uses the overridden get_db which returns a real session.
        # Let's insert a dummy payment directly into the DB so the query succeeds,
        # but mock the checkout/inventory via the PaymentService patch above.
        db = next(app.dependency_overrides[get_db]())
        from services.payments.models import Payment

        db.add(
            Payment(
                payment_id=payment_id,
                checkout_id="chk_1",
                merchant_id="mrc_1",
                buyer_id="buyer_1",
                authorization_id="auth_1",
                provider_order_id=order_id,
                amount_minor=1000,
                currency="INR",
            )
        )
        db.commit()

        res = client.post(
            "/api/verify-payment",
            json={
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": valid_signature,
            },
        )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["verified"] is True
    assert data["status"] == "paid"


def test_verify_razorpay_signature_invalid_mismatch():
    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    res = client.post(
        "/api/verify-payment",
        json={
            "razorpay_order_id": "order_test_123456",
            "razorpay_payment_id": "pay_test_987654",
            "razorpay_signature": "forged_signature_00000000000000",
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.WEBHOOK_SIGNATURE_INVALID


def test_verify_razorpay_signature_rejects_simulated_bypasses():
    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    # Simulated order id or magic string MUST be rejected without valid HMAC
    res = client.post(
        "/api/verify-payment",
        json={
            "razorpay_order_id": "order_sim_123456",
            "razorpay_payment_id": "pay_test_987654",
            "razorpay_signature": "valid_test_signature",
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == ErrorCode.WEBHOOK_SIGNATURE_INVALID


def test_end_to_end_commerce_core_lifecycle():
    """Verify /api/create-order and /api/verify-payment drive the real commerce engine (BUG-03)."""
    import uuid

    from services.checkout.models import Checkout
    from services.orders.models import Order
    from services.payments.models import Payment

    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    unique_order_id = f"order_e2e_{uuid.uuid4().hex[:8]}"
    payment_id = f"pay_e2e_{uuid.uuid4().hex[:8]}"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": unique_order_id,
        "amount": 25000,
        "currency": "INR",
        "status": "created",
    }

    with patch("services.payments.razorpay_adapter.httpx.Client") as MockClient:
        mock_client_instance = MagicMock()
        mock_client_instance.post.return_value = mock_resp
        MockClient.return_value.__enter__ = MagicMock(return_value=mock_client_instance)
        MockClient.return_value.__exit__ = MagicMock(return_value=False)

        rcpt = f"test_rcpt_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/create-order",
            json={"amount": 25000, "currency": "INR", "receipt": rcpt},
        )
        assert res.status_code == 200
        data = res.json()["data"]
        checkout_id = data["checkout_id"]
        payment_record_id = data["payment_id"]
        assert data["id"] == unique_order_id
        assert data["amount"] == 25000

        session = app.state.session_factory()
        try:
            # 1. Verify Checkout created with price hash
            chk = session.query(Checkout).filter(Checkout.checkout_id == checkout_id).first()
            assert chk is not None
            assert chk.total_minor == 25000
            assert chk.status == "authorized"

            # 2. Verify Payment created in DB
            pay = session.query(Payment).filter(Payment.payment_id == payment_record_id).first()
            assert pay is not None
            assert pay.provider_order_id == unique_order_id
            assert pay.status == "pending"

            # 3. Verify PAYMENT_CREATED audit event in ledger
            from services.audit.repository import list_events

            evts = list_events(
                session,
                merchant_id=chk.merchant_id,
                aggregate_type="payment",
                aggregate_id=payment_record_id,
            )
            assert any(e["event_type"] == "PAYMENT_CREATED" for e in evts)

            # 4. Verify payment via HMAC-SHA256 signature.
            # The verification gate now re-fetches the payment from Razorpay and
            # compares amount + capture state even for signed callbacks, so the
            # provider GET is mocked to report a captured payment at the exact
            # checkout amount.
            valid_sig = hmac.new(
                TEST_KEY_SECRET.encode("utf-8"),
                f"{unique_order_id}|{payment_id}".encode(),
                hashlib.sha256,
            ).hexdigest()

            captured_resp = MagicMock()
            captured_resp.status_code = 200
            captured_resp.json.return_value = {
                "id": payment_id,
                "order_id": unique_order_id,
                "amount": 25000,
                "currency": "INR",
                "status": "captured",
                "captured": True,
                "method": "card",
            }

            def _client_get(_self, url, **_kwargs):
                resp = MagicMock()
                resp.status_code = 200
                if "/payments/" in url:
                    resp.json.return_value = captured_resp.json.return_value
                else:
                    resp.json.return_value = mock_resp.json.return_value
                return resp

            with patch("services.payments.razorpay_adapter.httpx.Client") as MockVerifyClient:
                verify_client_instance = MagicMock()
                verify_client_instance.get.side_effect = _client_get.__get__(verify_client_instance)
                MockVerifyClient.return_value.__enter__ = MagicMock(
                    return_value=verify_client_instance
                )
                MockVerifyClient.return_value.__exit__ = MagicMock(return_value=False)

                verify_res = client.post(
                    "/api/verify-payment",
                    json={
                        "razorpay_order_id": unique_order_id,
                        "razorpay_payment_id": payment_id,
                        "razorpay_signature": valid_sig,
                    },
                )
            assert verify_res.status_code == 200
            vdata = verify_res.json()["data"]
            assert vdata["verified"] is True
            assert vdata["status"] == "paid"

            # 5. Verify Checkout, Payment, and Order status in DB
            session.expire_all()
            chk_after = session.query(Checkout).filter(Checkout.checkout_id == checkout_id).first()
            assert chk_after.status == "completed"

            pay_after = (
                session.query(Payment).filter(Payment.payment_id == payment_record_id).first()
            )
            assert pay_after.status == "verified"

            order = session.query(Order).filter(Order.checkout_id == checkout_id).first()
            assert order is not None
            assert order.status == "confirmed"
        finally:
            session.close()


def test_create_razorpay_order_rejects_unverified_offer():
    """Verify arbitrary unverified amount or unknown offer_id is rejected with 404 NOT_FOUND."""
    app = _app_with_razorpay_keys()
    client = _authenticated_client(app)

    # 1. Unknown amount with no active offer
    res = client.post(
        "/api/create-order",
        json={"amount": 999999, "currency": "INR", "receipt": "test_rcpt_unverified"},
    )
    assert res.status_code == 404
    assert res.json()["ok"] is False
    assert res.json()["error"]["code"] == "NOT_FOUND"

    # 2. Unknown explicit offer_id
    res_off = client.post(
        "/api/create-order",
        json={
            "amount": 10000,
            "currency": "INR",
            "offer_id": "off_nonexistent_999",
            "receipt": "test_rcpt_unverified_2",
        },
    )
    assert res_off.status_code == 404
    assert res_off.json()["ok"] is False
    assert res_off.json()["error"]["code"] == "NOT_FOUND"
