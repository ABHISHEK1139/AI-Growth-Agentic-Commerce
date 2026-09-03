"""Unit tests for RazorpayPaymentProvider adapter (BUG-07)."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import httpx
import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.payments.razorpay_adapter import RazorpayPaymentProvider

KEY_ID = "rzp_test_fixture_123"
KEY_SECRET = "secret_fixture_456"


@pytest.fixture
def provider() -> RazorpayPaymentProvider:
    return RazorpayPaymentProvider(key_id=KEY_ID, key_secret=KEY_SECRET, timeout_seconds=5.0)


def test_missing_credentials_raises(provider: RazorpayPaymentProvider) -> None:
    empty_provider = RazorpayPaymentProvider(key_id="", key_secret="")
    with pytest.raises(DomainError) as exc:
        empty_provider.create_order(10000, "INR", "rcpt_1", {})
    assert exc.value.code == ErrorCode.INTERNAL_ERROR


def test_create_order_success(provider: RazorpayPaymentProvider) -> None:
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
        order = provider.create_order(
            500000, "INR", "rcpt_1", {"chk": "1"}, idempotency_key="idm_123"
        )
        assert order.provider_order_id == "order_rzp_987"
        assert order.amount_minor == 500000
        assert order.currency == "INR"
        assert order.status == "created"
        mock_post.assert_called_once()


def test_create_order_non_200_raises(provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "Bad Request"

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            provider.create_order(500000, "INR", "rcpt_1", {})
        assert exc.value.code == ErrorCode.SERVICE_UNAVAILABLE


def test_create_order_timeout_raises(provider: RazorpayPaymentProvider) -> None:
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(DomainError) as exc:
            provider.create_order(500000, "INR", "rcpt_1", {})
        assert exc.value.code == ErrorCode.PAYMENT_TIMEOUT


def test_fetch_payment_success(provider: RazorpayPaymentProvider) -> None:
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
        payment = provider.fetch_payment("pay_rzp_123")
        assert payment.provider_payment_id == "pay_rzp_123"
        assert payment.amount_minor == 500000
        assert payment.captured is True


def test_fetch_payment_not_found(provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            provider.fetch_payment("pay_unknown")
        assert exc.value.code == ErrorCode.NOT_FOUND


def test_fetch_payment_timeout(provider: RazorpayPaymentProvider) -> None:
    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(DomainError) as exc:
            provider.fetch_payment("pay_rzp_123")
        assert exc.value.code == ErrorCode.PAYMENT_TIMEOUT


def test_fetch_order_success(provider: RazorpayPaymentProvider) -> None:
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
        order = provider.fetch_order("order_rzp_987")
        assert order.provider_order_id == "order_rzp_987"
        assert order.status == "paid"


def test_fetch_order_not_found(provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            provider.fetch_order("order_unknown")
        assert exc.value.code == ErrorCode.NOT_FOUND


def test_verify_signature(provider: RazorpayPaymentProvider) -> None:
    payload = b'{"event":"payment.captured"}'
    valid_sig = hmac.new(KEY_SECRET.encode(), payload, hashlib.sha256).hexdigest()

    assert provider.verify_signature(payload, valid_sig) is True
    assert provider.verify_signature(payload, "forged_sig") is False


def test_refund_success(provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "rfnd_rzp_111",
        "amount": 500000,
        "status": "processed",
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        refund = provider.refund("pay_rzp_123", 500000)
        assert refund.refund_id == "rfnd_rzp_111"
        assert refund.amount_minor == 500000
        assert refund.status == "processed"


def test_refund_non_200_raises(provider: RazorpayPaymentProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 400

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(DomainError) as exc:
            provider.refund("pay_rzp_123", 500000)
        assert exc.value.code == ErrorCode.SERVICE_UNAVAILABLE
