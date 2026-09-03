"""Unit tests for Phase E: Payment provider, payment creation, idempotency, webhooks, and failure handling (Tasks 18-22)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.api.config import Settings
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.authorization.models import Authorization
from services.checkout.models import Checkout
from services.offers.models import Offer
from services.orders.models import Order
from services.orders.service import OrderService
from services.payments.idempotency import (
    IdempotencyManager,
    compute_request_hash,
)
from services.payments.models import IdempotencyRecord, Payment, ProviderEvent
from services.payments.provider import (
    FakePaymentProvider,
    PaymentProvider,
    ProviderPayment,
    get_payment_provider,
)
from services.payments.razorpay_adapter import RazorpayPaymentProvider
from services.payments.service import PaymentService
from services.payments.webhooks import WebhookProcessor

# ---------------------------------------------------------------------------
# Task 18: Payment Provider Interface & Fake Provider
# ---------------------------------------------------------------------------


def test_fake_payment_provider_lifecycle():
    provider = FakePaymentProvider(secret="test_secret")

    # Create order
    order = provider.create_order(
        amount_minor=500000,
        currency="INR",
        receipt="chk_1",
        notes={"checkout_id": "chk_1"},
    )
    assert order.provider_order_id.startswith("order_fake_")
    assert order.amount_minor == 500000
    assert provider.order_count_for("chk_1") == 1

    # Fetch order
    fetched_order = provider.fetch_order(order.provider_order_id)
    assert fetched_order.provider_order_id == order.provider_order_id

    # Fetch payment: unknown ids are uncaptured (fail closed); staged ids
    # return their staged outcome.
    payment = provider.fetch_payment("pay_1")
    assert payment.captured is False

    provider.stage_payment(
        ProviderPayment(
            provider_payment_id="pay_staged",
            provider_order_id="order_fake_sample",
            amount_minor=500000,
            currency="INR",
            status="captured",
            captured=True,
        )
    )
    assert provider.fetch_payment("pay_staged").status == "captured"

    # Signature verification
    payload = b'{"event":"payment.captured"}'
    import hashlib
    import hmac

    sig = hmac.new(b"test_secret", payload, hashlib.sha256).hexdigest()
    assert provider.verify_signature(payload, sig) is True
    assert provider.verify_signature(payload, "invalid_sig") is False


def test_fake_payment_provider_injected_behaviors():
    provider = FakePaymentProvider()

    provider.set_behavior("timeout")
    with pytest.raises(DomainError) as exc_info:
        provider.create_order(500000, "INR", "chk_1", {})
    assert exc_info.value.code == ErrorCode.PAYMENT_TIMEOUT

    provider.set_behavior("failure")
    with pytest.raises(DomainError) as exc_info:
        provider.create_order(500000, "INR", "chk_1", {})
    assert exc_info.value.code == ErrorCode.SERVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Provider resolution: the settings the adapter is actually built from
# ---------------------------------------------------------------------------


def test_default_settings_resolve_to_the_fake_provider():
    """A clean clone charges nothing: no credentials, no real provider."""
    cfg = Settings(app_env="local", payment_provider="fake", model_provider="mock")
    assert isinstance(get_payment_provider(cfg), FakePaymentProvider)


def test_configured_razorpay_provider_receives_the_configured_timeout():
    """Resolution reads settings that exist.

    This is the payment path: a setting named in code but absent from
    ``Settings`` raises ``AttributeError`` the first time a merchant switches
    the provider on, which is the worst possible moment to find out. The
    timeout is asserted by value so a silently-dropped argument fails too.
    """
    cfg = Settings(
        app_env="local",
        payment_provider="razorpay",
        razorpay_key_id="rzp_test_unit_placeholder",
        razorpay_key_secret="unit-test-placeholder-not-a-credential",  # noqa: S106
        razorpay_webhook_secret="unit-test-placeholder-not-a-credential",  # noqa: S106
        payment_provider_timeout_seconds=7,
        model_provider="mock",
    )

    provider = get_payment_provider(cfg)

    assert provider.name == "razorpay"
    assert provider.timeout_seconds == 7


def test_razorpay_without_credentials_falls_back_to_the_fake_provider():
    """Selecting the provider is not enough; the credentials have to be there."""
    cfg = Settings(app_env="local", payment_provider="razorpay", model_provider="mock")
    assert isinstance(get_payment_provider(cfg), FakePaymentProvider)


# ---------------------------------------------------------------------------
# Task 20: Idempotency Layer
# ---------------------------------------------------------------------------


def test_idempotency_locking_and_replay():
    session = MagicMock()
    actor_id = "buy_1"
    endpoint = "POST /payments"
    idempotency_key = "idk_123"
    req_hash = compute_request_hash({"amount": 5000})

    # 1. First request acquires lock
    session.query.return_value.filter.return_value.first.return_value = None
    is_replay, record, cached_body, status_code = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id=actor_id,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=req_hash,
    )
    assert is_replay is False
    assert record is not None
    assert record.status == "in_progress"

    # 2. In-flight concurrent request with same key -> 409 REQUEST_IN_PROGRESS
    session.query.return_value.filter.return_value.first.return_value = record
    with pytest.raises(DomainError) as exc_info:
        IdempotencyManager.acquire_lock(
            session,
            actor_type="buyer",
            actor_id=actor_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
    assert exc_info.value.code == ErrorCode.REQUEST_IN_PROGRESS

    # 3. Differing payload with same key -> 422 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST
    diff_hash = compute_request_hash({"amount": 9999})
    with pytest.raises(DomainError) as exc_info:
        IdempotencyManager.acquire_lock(
            session,
            actor_type="buyer",
            actor_id=actor_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=diff_hash,
        )
    assert exc_info.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST

    # 4. Completed request returns cached response (replay)
    record.status = "completed"
    record.response_body = {"payment_id": "pay_1"}
    record.response_status_code = 200
    is_replay, _, cached_body, status_code = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id=actor_id,
        endpoint=endpoint,
        idempotency_key=idempotency_key,
        request_hash=req_hash,
    )
    assert is_replay is True
    assert cached_body == {"payment_id": "pay_1"}
    assert status_code == 200


# ---------------------------------------------------------------------------
# Task 19 & 21 & 22: Payment Creation, Webhook Verification, Failure Recovery
# ---------------------------------------------------------------------------


def _setup_payment_entities():
    now = datetime.now(UTC)
    offer = Offer(
        offer_id="off_1",
        merchant_id="merch_1",
        product_id="prod_1",
        offer_version=1,
        unit_price_minor=5000000,
        currency="INR",
        status="active",
        expires_at=(now + timedelta(minutes=15)).isoformat(),
        created_at=now,
    )
    snapshot = {
        "offer_id": "off_1",
        "offer_version": 1,
        "unit_price_minor": 5000000,
        "quantity": 1,
        "shipping_minor": 0,
        "tax_minor": 0,
        "discount_minor": 0,
        "currency": "INR",
        "expires_at": (now + timedelta(minutes=15)).isoformat(),
    }
    from services.checkout.hash import compute_price_hash

    price_hash = compute_price_hash(snapshot)

    checkout = Checkout(
        checkout_id="chk_1",
        buyer_id="buy_1",
        merchant_id="merch_1",
        offer_id="off_1",
        offer_version=1,
        status="authorized",
        subtotal_minor=5000000,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        total_minor=5000000,
        currency="INR",
        price_hash=price_hash,
        price_snapshot=snapshot,
        expires_at=now + timedelta(minutes=15),
        created_at=now,
    )

    auth = Authorization(
        authorization_id="ath_1",
        checkout_id="chk_1",
        buyer_id="buy_1",
        merchant_id="merch_1",
        amount_ceiling_minor=5000000,
        currency="INR",
        price_hash=price_hash,
        policy_version="1.0",
        status="approved",
        valid_until=now + timedelta(minutes=15),
        created_at=now,
    )
    return checkout, auth, offer, price_hash


def test_payment_creation_success():
    checkout, auth, offer, _ = _setup_payment_entities()
    session = MagicMock()

    def mock_query(model):
        mock = MagicMock()
        if model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Authorization:
            mock.filter.return_value.first.return_value = auth
        elif model == Payment:
            mock.filter.return_value.first.return_value = None
        return mock

    session.query.side_effect = mock_query

    provider = FakePaymentProvider()
    mock_auth_service = MagicMock()
    mock_auth_service.revalidate_for_payment.return_value = auth

    service = PaymentService(provider=provider, auth_service=mock_auth_service)
    payment = service.create_payment(
        session,
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_1",
        authorization_id="ath_1",
    )

    assert payment.status == "pending"
    assert payment.amount_minor == 5000000
    assert payment.provider_order_id is not None
    assert auth.status == "consumed"


def test_payment_creation_detects_real_price_change():
    """When merchant modifies offer.unit_price_minor after checkout/approval, create_payment must raise PRICE_CHANGED (BUG-29)."""
    checkout, auth, offer, _ = _setup_payment_entities()

    # Merchant raises price from 50,000 INR to 55,000 INR
    offer.unit_price_minor = 5500000

    session = MagicMock()

    def mock_query(model):
        mock = MagicMock()
        if model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Authorization:
            mock.filter.return_value.first.return_value = auth
        return mock

    session.query.side_effect = mock_query

    service = PaymentService(provider=FakePaymentProvider())

    with pytest.raises(DomainError) as exc_info:
        service.create_payment(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
            authorization_id="ath_1",
        )

    assert exc_info.value.code == ErrorCode.PRICE_CHANGED
    assert checkout.status == "price_changed"


def test_payment_creation_detects_offer_version_change():
    """When merchant publishes a new offer_version after checkout, create_payment must raise PRICE_CHANGED (BUG-29)."""
    checkout, auth, offer, _ = _setup_payment_entities()

    # Merchant increments offer version
    offer.offer_version = 2

    session = MagicMock()

    def mock_query(model):
        mock = MagicMock()
        if model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Authorization:
            mock.filter.return_value.first.return_value = auth
        return mock

    session.query.side_effect = mock_query

    service = PaymentService(provider=FakePaymentProvider())

    with pytest.raises(DomainError) as exc_info:
        service.create_payment(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
            authorization_id="ath_1",
        )

    assert exc_info.value.code == ErrorCode.PRICE_CHANGED
    assert checkout.status == "price_changed"


def test_webhook_processing_confirms_order_and_commits_inventory():
    checkout, auth, _, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id="order_fake_123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    session = MagicMock()

    def mock_query(model):
        q = MagicMock()
        if model == Payment:
            q.filter.return_value.first.return_value = payment
        elif model == Checkout:
            q.filter.return_value.first.return_value = (
                q.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Order:
            q.filter.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
        return q

    session.query.side_effect = mock_query

    mock_inv_service = MagicMock()
    mock_order_service = OrderService()
    provider = FakePaymentProvider(secret="sec")
    # Stage the captured outcome: an unknown payment id is reported uncaptured
    # by design, so a webhook that claims capture for it must be refused.
    provider.stage_payment(
        ProviderPayment(
            provider_payment_id="pay_prov_1",
            provider_order_id="order_fake_123",
            amount_minor=5000000,
            currency="INR",
            status="captured",
            captured=True,
        )
    )

    payment_service = PaymentService(
        provider=provider,
        inventory_service=mock_inv_service,
        order_service=mock_order_service,
    )
    webhook_proc = WebhookProcessor(provider=provider, payment_service=payment_service)

    raw_body = json.dumps(
        {
            "event": "payment.captured",
            "event_id": "evt_100",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_prov_1",
                        "order_id": "order_fake_123",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode("utf-8")

    import hashlib
    import hmac

    sig = hmac.new(b"sec", raw_body, hashlib.sha256).hexdigest()

    res = webhook_proc.process_webhook(session, raw_body=raw_body, signature=sig)
    assert res["status"] == "processed"
    assert payment.status == "verified"
    assert checkout.status == "completed"
    assert mock_inv_service.commit_stock.called


def test_webhook_processor_performs_independent_provider_fetch():
    """WebhookProcessor calls provider fetch_payment before verifying (Requirement 16.4, BUG-45)."""
    checkout, auth, _, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id="order_fake_123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = MagicMock()

    def mock_query(model):
        q = MagicMock()
        if model == Payment:
            q.filter.return_value.first.return_value = payment
        elif model == Checkout:
            q.filter.return_value.first.return_value = (
                q.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Order:
            q.filter.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
        return q

    session.query.side_effect = mock_query

    mock_inv_service = MagicMock()
    mock_order_service = OrderService()
    mock_provider = MagicMock(spec=PaymentProvider)
    mock_provider.verify_signature.return_value = True
    mock_provider.fetch_payment.return_value = ProviderPayment(
        provider_payment_id="pay_prov_1",
        provider_order_id="order_fake_123",
        amount_minor=5000000,
        currency="INR",
        status="captured",
        captured=True,
    )

    payment_service = PaymentService(
        provider=mock_provider,
        inventory_service=mock_inv_service,
        order_service=mock_order_service,
    )
    webhook_proc = WebhookProcessor(provider=mock_provider, payment_service=payment_service)

    raw_body = json.dumps(
        {
            "event": "payment.captured",
            "event_id": "evt_200",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_prov_1",
                        "order_id": "order_fake_123",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode("utf-8")

    res = webhook_proc.process_webhook(session, raw_body=raw_body, signature="sig_test")
    assert res["status"] == "processed"
    # The provider is consulted twice by design: once by the webhook handler's
    # independent verification, and again inside verify_payment's gate, which
    # now re-fetches amount+status even for signed callbacks.
    assert mock_provider.fetch_payment.call_count >= 1
    mock_provider.fetch_payment.assert_called_with("pay_prov_1")
    assert payment.status == "verified"
    assert checkout.status == "completed"


def test_webhook_processor_rejects_when_provider_fetch_reports_uncaptured():
    """WebhookProcessor refuses to verify if provider reports uncaptured status (BUG-45)."""
    checkout, auth, _, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id="order_fake_123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = MagicMock()

    def mock_query(model):
        q = MagicMock()
        if model == Payment:
            q.filter.return_value.first.return_value = payment
        elif model == Checkout:
            q.filter.return_value.first.return_value = (
                q.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Order:
            q.filter.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
        return q

    session.query.side_effect = mock_query

    mock_inv_service = MagicMock()
    mock_order_service = OrderService()
    mock_provider = MagicMock(spec=PaymentProvider)
    mock_provider.verify_signature.return_value = True
    mock_provider.fetch_payment.return_value = ProviderPayment(
        provider_payment_id="pay_prov_1",
        provider_order_id="order_fake_123",
        amount_minor=5000000,
        currency="INR",
        status="failed",
        captured=False,
    )
    mock_provider.fetch_order.return_value = None

    payment_service = PaymentService(
        provider=mock_provider,
        inventory_service=mock_inv_service,
        order_service=mock_order_service,
    )
    webhook_proc = WebhookProcessor(provider=mock_provider, payment_service=payment_service)

    raw_body = json.dumps(
        {
            "event": "payment.captured",
            "event_id": "evt_201",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_prov_1",
                        "order_id": "order_fake_123",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode("utf-8")

    # No DomainError is raised — the webhook is dead-lettered instead so the
    # provider does not retry the same bad payload forever (BUG-46 / Requirement 16.5).
    result = webhook_proc.process_webhook(session, raw_body=raw_body, signature="sig_test")
    assert result["status"] == "dead_lettered"
    assert result["ok"] is True
    assert payment.status == "pending"
    assert checkout.status != "completed"


def test_webhook_processor_deduplicates_when_provider_omits_event_id():
    """WebhookProcessor deduplicates via body hash when provider payload omits event_id (BUG-46)."""
    mock_existing_event = ProviderEvent(
        provider_event_id="evt_hash_12345",
        provider="fake",
        event_type="payment.captured",
        payload={},
        signature="sig_test",
        raw_body_hash="some_hash",
        status="processed",
    )

    session = MagicMock()
    # First call: query returns existing_event -> already processed
    session.query.return_value.filter.return_value.first.return_value = mock_existing_event

    mock_provider = MagicMock(spec=PaymentProvider)
    mock_provider.verify_signature.return_value = True

    webhook_proc = WebhookProcessor(provider=mock_provider)

    raw_body = json.dumps(
        {
            "event": "payment.captured",
            # Note: No event_id or id in payload!
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_prov_1",
                        "order_id": "order_fake_123",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode("utf-8")

    res = webhook_proc.process_webhook(session, raw_body=raw_body, signature="sig_test")
    assert res["status"] == "already_processed"
    assert res["ok"] is True
    assert res["event_id"] == "evt_hash_12345"
    mock_provider.fetch_payment.assert_not_called()


def test_webhook_processor_rejects_missing_provider_payment_id():
    """WebhookProcessor rejects payment capture without fabricating a synthetic payment ID (BUG-47)."""
    checkout, auth, _, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id=None,
        provider_payment_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = MagicMock()

    def mock_query(model):
        q = MagicMock()
        if model == Payment:
            q.filter.return_value.first.return_value = payment
        elif model == Checkout:
            q.filter.return_value.first.return_value = (
                q.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == ProviderEvent:
            q.filter.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
        return q

    session.query.side_effect = mock_query

    mock_provider = MagicMock(spec=PaymentProvider)
    mock_provider.verify_signature.return_value = True

    webhook_proc = WebhookProcessor(provider=mock_provider)

    raw_body = json.dumps(
        {
            "event": "payment.captured",
            "event_id": "evt_missing_ids",
            "payment_id": "pay_1",
            "payload": {
                # Missing payment.entity.id and order_id
                "payment": {"entity": {"status": "captured"}}
            },
        }
    ).encode("utf-8")

    # No DomainError is raised — the webhook is dead-lettered instead so the
    # provider does not retry the same bad payload forever (BUG-46 / Requirement 16.5).
    result = webhook_proc.process_webhook(session, raw_body=raw_body, signature="sig_test")
    assert result["status"] == "dead_lettered"
    assert result["ok"] is True


def test_webhook_processor_raises_not_found_on_unmatched_payment_and_marks_event_unmatched():
    """WebhookProcessor raises NOT_FOUND on unknown payment and marks event unmatched (BUG-48)."""
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None

    mock_provider = MagicMock(spec=PaymentProvider)
    mock_provider.verify_signature.return_value = True

    webhook_proc = WebhookProcessor(provider=mock_provider)

    raw_body = json.dumps(
        {
            "event": "payment.captured",
            "event_id": "evt_unknown",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_prov_unknown",
                        "order_id": "order_unknown_123",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode("utf-8")

    # No DomainError is raised — unmatched webhooks are dead-lettered for
    # manual replay rather than lost (BUG-46 / Requirement 16.5).
    result = webhook_proc.process_webhook(session, raw_body=raw_body, signature="sig_test")
    assert result["status"] == "dead_lettered"
    assert result["ok"] is True


def test_payment_failure_releases_inventory():
    checkout, auth, _, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id="order_fake_123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    session = MagicMock()
    session.query.return_value.filter.return_value.first.side_effect = [
        payment,
        checkout,
    ]

    mock_inv_service = MagicMock()
    service = PaymentService(inventory_service=mock_inv_service)
    res = service.fail_payment(session, payment_id="pay_1", reason="Card declined")

    assert res.status == "failed"
    assert checkout.status == "payment_failed"
    assert mock_inv_service.release_stock.called


def test_verify_payment_rejects_fake_signature_and_uncaptured_status():
    """Verify PaymentService.verify_payment rejects unverified signatures and uncaptured provider payments (BUG-30)."""
    from services.inventory.models import Reservation
    from services.orders.models import Order

    checkout, auth, offer, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id="order_fake_123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    session = MagicMock()

    def mock_query(model):
        mock = MagicMock()
        if model == Payment:
            mock.filter.return_value.first.return_value = payment
        elif model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Order:
            mock.filter.return_value.first.return_value = None
        elif model == Reservation:
            mock.filter.return_value.first.return_value = MagicMock(status="held", quantity=1)
        return mock

    session.query.side_effect = mock_query

    provider = FakePaymentProvider(secret="sec")
    provider.set_behavior("failure")  # fetch_payment returns status='failed'

    service = PaymentService(provider=provider)

    with pytest.raises(DomainError) as exc_info:
        service.verify_payment(
            session,
            payment_id="pay_1",
            provider_payment_id="pay_prov_fake",
            provider_signature="invalid_garbage_signature_123",
        )

    assert exc_info.value.code == ErrorCode.WEBHOOK_SIGNATURE_INVALID
    assert payment.status == "pending"  # Not marked verified


def test_verify_payment_succeeds_with_valid_hmac_signature():
    """Payment verification succeeds when valid HMAC signature is supplied (BUG-30)."""
    import hashlib
    import hmac

    from services.inventory.models import Reservation
    from services.orders.models import Order

    checkout, auth, offer, _ = _setup_payment_entities()
    payment = Payment(
        payment_id="pay_1",
        checkout_id="chk_1",
        merchant_id="merch_1",
        buyer_id="buy_1",
        authorization_id="ath_1",
        status="pending",
        amount_minor=5000000,
        currency="INR",
        provider="fake",
        provider_order_id="order_fake_123",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    session = MagicMock()

    def mock_query(model):
        mock = MagicMock()
        if model == Payment:
            mock.filter.return_value.first.return_value = payment
        elif model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Order:
            mock.filter.return_value.first.return_value = None
        elif model == Reservation:
            mock.filter.return_value.first.return_value = MagicMock(status="held", quantity=1)
        return mock

    session.query.side_effect = mock_query

    mock_inv = MagicMock()
    mock_order_svc = OrderService()

    provider = FakePaymentProvider(secret="sec")
    # Stage the captured outcome the webhook claims; unknown ids fail closed.
    provider.stage_payment(
        ProviderPayment(
            provider_payment_id="pay_prov_123",
            provider_order_id="order_fake_123",
            amount_minor=5000000,
            currency="INR",
            status="captured",
            captured=True,
        )
    )
    payload = b"order_fake_123|pay_prov_123"
    valid_sig = hmac.new(b"sec", payload, hashlib.sha256).hexdigest()

    service = PaymentService(
        provider=provider, inventory_service=mock_inv, order_service=mock_order_svc
    )
    res_payment, res_order = service.verify_payment(
        session,
        payment_id="pay_1",
        provider_payment_id="pay_prov_123",
        provider_signature=valid_sig,
    )

    assert res_payment.status == "verified"
    assert payment.status == "verified"
    assert checkout.status == "completed"
    assert res_order.order_id is not None


# ---------------------------------------------------------------------------
# Razorpay Adapter Unit Tests (BUG-07)
# ---------------------------------------------------------------------------

KEY_ID_FIXTURE = "rzp_test_fixture_123"
KEY_SECRET_FIXTURE = "secret_fixture_456"


@pytest.fixture
def rzp_provider() -> RazorpayPaymentProvider:
    return RazorpayPaymentProvider(
        key_id=KEY_ID_FIXTURE, key_secret=KEY_SECRET_FIXTURE, timeout_seconds=5.0
    )


def test_razorpay_missing_credentials_raises() -> None:
    empty_provider = RazorpayPaymentProvider(key_id="", key_secret="")
    with pytest.raises(DomainError) as exc:
        empty_provider.create_order(10000, "INR", "rcpt_1", {})
    assert exc.value.code == ErrorCode.INTERNAL_ERROR


def test_razorpay_create_order_success(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "order_rzp_987",
        "amount": 500000,
        "currency": "INR",
        "receipt": "rcpt_1",
        "status": "created",
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        order = rzp_provider.create_order(
            500000, "INR", "rcpt_1", {"chk": "1"}, idempotency_key="idm_123"
        )
        assert order.provider_order_id == "order_rzp_987"
        assert order.amount_minor == 500000
        assert order.currency == "INR"
        assert order.status == "created"
        mock_post.assert_called_once()


def test_razorpay_create_order_non_200_raises(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            rzp_provider.create_order(500000, "INR", "rcpt_1", {})
        assert exc.value.code == ErrorCode.SERVICE_UNAVAILABLE


def test_razorpay_create_order_timeout_raises(rzp_provider: RazorpayPaymentProvider) -> None:
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(DomainError) as exc:
            rzp_provider.create_order(500000, "INR", "rcpt_1", {})
        assert exc.value.code == ErrorCode.PAYMENT_TIMEOUT


def test_razorpay_fetch_payment_success(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "pay_rzp_123",
        "order_id": "order_rzp_987",
        "amount": 500000,
        "currency": "INR",
        "status": "captured",
        "method": "card",
        "captured": True,
    }

    with patch("httpx.Client.get", return_value=mock_resp):
        payment = rzp_provider.fetch_payment("pay_rzp_123")
        assert payment.provider_payment_id == "pay_rzp_123"
        assert payment.amount_minor == 500000
        assert payment.captured is True


def test_razorpay_fetch_payment_not_found(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            rzp_provider.fetch_payment("pay_unknown")
        assert exc.value.code == ErrorCode.NOT_FOUND


def test_razorpay_fetch_payment_timeout(rzp_provider: RazorpayPaymentProvider) -> None:
    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(DomainError) as exc:
            rzp_provider.fetch_payment("pay_rzp_123")
        assert exc.value.code == ErrorCode.PAYMENT_TIMEOUT


def test_razorpay_fetch_order_success(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "order_rzp_987",
        "amount": 500000,
        "currency": "INR",
        "receipt": "rcpt_1",
        "status": "paid",
    }

    with patch("httpx.Client.get", return_value=mock_resp):
        order = rzp_provider.fetch_order("order_rzp_987")
        assert order.provider_order_id == "order_rzp_987"
        assert order.status == "paid"


def test_razorpay_fetch_order_not_found(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            rzp_provider.fetch_order("order_unknown")
        assert exc.value.code == ErrorCode.NOT_FOUND


def test_razorpay_verify_signature(rzp_provider: RazorpayPaymentProvider) -> None:
    payload = b'{"event":"payment.captured"}'
    import hashlib
    import hmac

    valid_sig = hmac.new(KEY_SECRET_FIXTURE.encode(), payload, hashlib.sha256).hexdigest()

    assert rzp_provider.verify_signature(payload, valid_sig) is True
    assert rzp_provider.verify_signature(payload, "forged_sig") is False


def test_razorpay_refund_success(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "rfnd_rzp_111",
        "amount": 500000,
        "status": "processed",
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        refund = rzp_provider.refund("pay_rzp_123", 500000)
        assert refund.refund_id == "rfnd_rzp_111"
        assert refund.amount_minor == 500000
        assert refund.status == "processed"


def test_razorpay_refund_non_200_raises(rzp_provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 400

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            rzp_provider.refund("pay_rzp_123", 500000)
        assert exc.value.code == ErrorCode.SERVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Idempotency Layer Tests (BUG-23)
# ---------------------------------------------------------------------------


def test_idempotency_manager_acquire_and_complete():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None

    req_hash = compute_request_hash({"amount": 5000})
    is_replay, record, cached_body, cached_code = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_1",
        request_hash=req_hash,
    )

    assert is_replay is False
    assert record is not None
    assert record.status == "in_progress"
    assert cached_body is None

    # Complete the lock
    session.query.return_value.filter.return_value.first.return_value = record
    IdempotencyManager.complete(
        session,
        record_id=record.idempotency_record_id,
        status_code=200,
        response_body={"payment_id": "pay_123"},
    )
    assert record.status == "completed"
    assert record.response_status_code == 200


def test_idempotency_manager_replay():
    session = MagicMock()
    req_hash = compute_request_hash({"amount": 5000})
    existing = IdempotencyRecord(
        idempotency_record_id="idm_1",
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_1",
        request_hash=req_hash,
        status="completed",
        response_status_code=200,
        response_body={"payment_id": "pay_123"},
    )
    session.query.return_value.filter.return_value.first.return_value = existing

    is_replay, record, cached_body, cached_code = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_1",
        request_hash=req_hash,
    )

    assert is_replay is True
    assert cached_body == {"payment_id": "pay_123"}
    assert cached_code == 200


def test_idempotency_manager_in_progress_and_mismatch():
    session = MagicMock()
    req_hash = compute_request_hash({"amount": 5000})
    in_flight = IdempotencyRecord(
        idempotency_record_id="idm_1",
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_1",
        request_hash=req_hash,
        status="in_progress",
    )
    session.query.return_value.filter.return_value.first.return_value = in_flight

    with pytest.raises(DomainError) as exc:
        IdempotencyManager.acquire_lock(
            session,
            actor_type="buyer",
            actor_id="buy_123",
            endpoint="/api/v1/checkout/chk_1/pay",
            idempotency_key="idm_key_1",
            request_hash=req_hash,
        )
    assert exc.value.code == ErrorCode.REQUEST_IN_PROGRESS

    # Differing payload
    other_hash = compute_request_hash({"amount": 9999})
    with pytest.raises(DomainError) as exc2:
        IdempotencyManager.acquire_lock(
            session,
            actor_type="buyer",
            actor_id="buy_123",
            endpoint="/api/v1/checkout/chk_1/pay",
            idempotency_key="idm_key_1",
            request_hash=other_hash,
        )
    assert exc2.value.code == ErrorCode.IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST


def test_idempotency_concurrency_race_handled():
    """Verify concurrent lock acquisition race handles IntegrityError without failing."""
    from sqlalchemy.exc import IntegrityError

    session = MagicMock()
    req_hash = compute_request_hash({"amount": 5000})
    existing = IdempotencyRecord(
        idempotency_record_id="idm_winner",
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_1",
        request_hash=req_hash,
        status="completed",
        response_status_code=200,
        response_body={"payment_id": "pay_winner"},
    )

    # Initial query returns None (both saw empty slot)
    session.query.return_value.filter.return_value.first.side_effect = [
        None,  # First query before insert
        existing,  # Query after IntegrityError
    ]
    # Flush raises IntegrityError from unique constraint
    session.flush.side_effect = IntegrityError("duplicate key", params=None, orig=Exception())

    is_replay, record, cached_body, cached_code = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_1",
        request_hash=req_hash,
    )

    assert is_replay is True
    assert record.idempotency_record_id == "idm_winner"
    assert cached_body == {"payment_id": "pay_winner"}
    assert cached_code == 200


def test_create_payment_failure_fails_idempotency_lock_and_allows_retry():
    """Verify provider failure in create_payment marks idempotency lock as failed and allows retry (BUG-31)."""
    checkout, auth, offer, _ = _setup_payment_entities()
    session = MagicMock()

    idm_record = None

    def mock_query(model):
        mock = MagicMock()
        if model == Checkout:
            mock.filter.return_value.first.return_value = (
                mock.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Offer:
            mock.filter.return_value.first.return_value = offer
        elif model == Authorization:
            mock.filter.return_value.first.return_value = auth
        elif model == IdempotencyRecord:
            mock.filter.return_value.first.return_value = idm_record
        return mock

    session.query.side_effect = mock_query

    provider = FakePaymentProvider()
    provider.set_behavior("timeout")  # Simulate provider timeout on first attempt

    mock_auth_service = MagicMock()
    mock_auth_service.revalidate_for_payment.return_value = auth

    service = PaymentService(provider=provider, auth_service=mock_auth_service)

    # 1. First attempt fails due to provider timeout
    with pytest.raises(DomainError) as exc_info:
        service.create_payment(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
            authorization_id="ath_1",
            idempotency_key="idm_buyer_key_123",
        )

    assert exc_info.value.code == ErrorCode.PAYMENT_TIMEOUT

    # Verify idempotency record was created and failed (not stuck in_progress)
    added_records = [
        arg[0] for arg, _ in session.add.call_args_list if isinstance(arg[0], IdempotencyRecord)
    ]
    assert len(added_records) == 1
    failed_record = added_records[0]
    assert failed_record.status == "failed"

    # 2. Second attempt (retry with same idempotency key) when provider recovers
    idm_record = failed_record  # Next query finds the failed record
    provider.set_behavior("success")
    mock_auth_svc = MagicMock()
    mock_auth_svc.revalidate_for_payment.return_value = auth
    service._auth_service = mock_auth_svc

    payment = service.create_payment(
        session,
        buyer_id="buy_1",
        merchant_id="merch_1",
        checkout_id="chk_1",
        authorization_id="ath_1",
        idempotency_key="idm_buyer_key_123",
    )

    assert payment.status == "pending"
    assert failed_record.status == "completed"


def test_idempotency_manager_allows_retry_on_failed_and_expired_locks():
    """Verify IdempotencyManager permits retries when record is failed or in_progress lock expired (BUG-31)."""
    now = datetime.now(UTC)
    session = MagicMock()
    req_hash = compute_request_hash({"amount": 5000})

    # Case 1: Failed record allows retry
    failed_record = IdempotencyRecord(
        idempotency_record_id="idm_failed",
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_fail",
        request_hash=req_hash,
        status="failed",
        expires_at=now + timedelta(hours=24),
    )
    session.query.return_value.filter.return_value.first.return_value = failed_record

    is_replay, record, cached_body, _ = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_fail",
        request_hash=req_hash,
        now=now,
    )
    assert is_replay is False
    assert record.status == "in_progress"

    # Case 2: Expired in_progress lock allows retry
    expired_record = IdempotencyRecord(
        idempotency_record_id="idm_expired",
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_exp",
        request_hash=req_hash,
        status="in_progress",
        expires_at=now - timedelta(seconds=10),
    )
    session.query.return_value.filter.return_value.first.return_value = expired_record

    is_replay, record, cached_body, _ = IdempotencyManager.acquire_lock(
        session,
        actor_type="buyer",
        actor_id="buy_123",
        endpoint="/api/v1/checkout/chk_1/pay",
        idempotency_key="idm_key_exp",
        request_hash=req_hash,
        now=now,
    )
    assert is_replay is False
    assert record.status == "in_progress"


def test_payment_schema_reflects_actual_provider_key_and_test_mode():
    """Verify payment responses do NOT use placeholder keys and reflect actual provider mode (BUG-34)."""
    # 1. Live/Production mode provider
    live_provider = FakePaymentProvider()
    live_provider.key_id = "rzp_live_real_merchant_key_999"
    live_provider.test_mode = False

    service = PaymentService(provider=live_provider)
    payment = Payment(
        payment_id="pay_live_1",
        checkout_id="chk_1",
        authorization_id="ath_1",
        merchant_id="mrc_1",
        buyer_id="buy_1",
        amount_minor=499900,
        currency="INR",
        status="pending",
        test_mode=False,
    )

    schema = service._to_schema(payment)
    assert schema.public_key == "rzp_live_real_merchant_key_999"
    assert schema.test_mode is False
    assert schema.public_key != "rzp_test_public_placeholder"

    # 2. Test mode provider
    test_provider = FakePaymentProvider()
    test_provider.key_id = "rzp_test_custom_key_123"
    test_provider.test_mode = True

    service_test = PaymentService(provider=test_provider)
    payment_test = Payment(
        payment_id="pay_test_1",
        checkout_id="chk_1",
        authorization_id="ath_1",
        merchant_id="mrc_1",
        buyer_id="buy_1",
        amount_minor=499900,
        currency="INR",
        status="pending",
        test_mode=True,
    )

    schema_test = service_test._to_schema(payment_test)
    assert schema_test.public_key == "rzp_test_custom_key_123"
    assert schema_test.test_mode is True


def test_verify_payment_rejects_second_payment_for_completed_checkout():
    """Verifying a second distinct payment for an already completed checkout raises ALREADY_FINALIZED (BUG-37)."""
    provider = FakePaymentProvider()
    provider.set_behavior("success")
    service = PaymentService(provider=provider)

    session = MagicMock()
    # Payment 2 is pending
    payment2 = Payment(
        payment_id="pay_distinct_2",
        checkout_id="chk_completed_1",
        authorization_id="ath_1",
        merchant_id="mrc_1",
        buyer_id="buy_1",
        amount_minor=499900,
        currency="INR",
        status="pending",
        provider="razorpay",
        provider_order_id="order_rzp_2",
    )
    # Checkout is already completed
    checkout = Checkout(
        checkout_id="chk_completed_1",
        merchant_id="mrc_1",
        buyer_id="buy_1",
        offer_id="off_1",
        offer_version=1,
        subtotal_minor=499900,
        total_minor=499900,
        currency="INR",
        price_hash="hash_123",
        price_snapshot={},
        expires_at=datetime.now(UTC),
        status="completed",
    )
    # Order 1 exists pointing at payment 1
    order1 = Order(
        order_id="ord_1",
        order_number="ORD-1",
        checkout_id="chk_completed_1",
        payment_id="pay_first_1",
        buyer_id="buy_1",
        merchant_id="mrc_1",
        status="confirmed",
        total_minor=499900,
        currency="INR",
    )

    def mock_query(model):
        q = MagicMock()
        if model == Payment:
            q.filter.return_value.first.return_value = payment2
        elif model == Checkout:
            q.filter.return_value.first.return_value = (
                q.filter.return_value.with_for_update.return_value.first.return_value
            ) = checkout
        elif model == Order:
            q.filter.return_value.first.return_value = order1
        return q

    session.query.side_effect = mock_query

    with pytest.raises(DomainError) as exc_info:
        service.verify_payment(
            session,
            payment_id="pay_distinct_2",
            provider_payment_id="pay_rzp_2",
            provider_signature="sig_valid",
        )

    assert exc_info.value.code == ErrorCode.ALREADY_FINALIZED
