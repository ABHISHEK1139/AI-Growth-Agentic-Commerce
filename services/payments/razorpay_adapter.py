"""Razorpay Payment Provider adapter for live and test-mode operations (Task 21, Requirement 14)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import httpx

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.payments.provider import (
    ProviderOrder,
    ProviderPayment,
    ProviderRefund,
)


class RazorpayPaymentProvider:
    """Production provider integrating with Razorpay REST API."""

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.name = "razorpay"
        self.key_id = key_id
        self.key_secret = key_secret
        # Not `webhook_secret or key_secret`. Razorpay signs webhooks with a
        # separate secret from the API key, and collapsing the two made every
        # webhook signature verify against the API key — so a signature minted
        # for a payment callback also satisfied the webhook endpoint. Left as
        # None when unset, which makes webhook verification fail closed.
        self.webhook_secret = webhook_secret or None
        self.timeout_seconds = timeout_seconds

    def _auth_header(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def create_order(
        self,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ProviderOrder:
        if not self.key_id or not self.key_secret:
            raise DomainError("Razorpay credentials not configured.", code=ErrorCode.INTERNAL_ERROR)

        payload = {
            "amount": amount_minor,
            "currency": currency,
            "receipt": receipt,
            "notes": notes,
        }
        headers = self._auth_header()
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.post(
                    "https://api.razorpay.com/v1/orders",
                    json=payload,
                    headers=headers,
                )
            if res.status_code not in (200, 201):
                raise DomainError(
                    f"Razorpay order creation failed with status {res.status_code}",
                    code=ErrorCode.SERVICE_UNAVAILABLE,
                )
            data = res.json()
            return ProviderOrder(
                provider_order_id=data["id"],
                amount_minor=data["amount"],
                currency=data["currency"],
                receipt=data.get("receipt", receipt),
                status=data.get("status", "created"),
            )
        except httpx.TimeoutException as exc:
            raise DomainError(
                "Razorpay provider timed out.", code=ErrorCode.PAYMENT_TIMEOUT
            ) from exc
        except Exception as exc:
            if isinstance(exc, DomainError):
                raise exc
            raise DomainError(
                f"Razorpay provider error: {exc}", code=ErrorCode.SERVICE_UNAVAILABLE
            ) from exc

    def fetch_payment(self, provider_payment_id: str) -> ProviderPayment:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(
                    f"https://api.razorpay.com/v1/payments/{provider_payment_id}",
                    headers=self._auth_header(),
                )
            if res.status_code != 200:
                raise DomainError("Payment not found on Razorpay", code=ErrorCode.NOT_FOUND)
            data = res.json()
            return ProviderPayment(
                provider_payment_id=data["id"],
                provider_order_id=data.get("order_id", ""),
                amount_minor=data["amount"],
                currency=data["currency"],
                status=data["status"],
                method=data.get("method", "card"),
                captured=data.get("captured", False),
            )
        except httpx.TimeoutException as exc:
            raise DomainError("Razorpay fetch timed out", code=ErrorCode.PAYMENT_TIMEOUT) from exc
        except Exception as exc:
            if isinstance(exc, DomainError):
                raise exc
            raise DomainError(f"Razorpay error: {exc}", code=ErrorCode.SERVICE_UNAVAILABLE) from exc

    def fetch_order(self, provider_order_id: str) -> ProviderOrder:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.get(
                    f"https://api.razorpay.com/v1/orders/{provider_order_id}",
                    headers=self._auth_header(),
                )
            if res.status_code != 200:
                raise DomainError("Order not found on Razorpay", code=ErrorCode.NOT_FOUND)
            data = res.json()
            return ProviderOrder(
                provider_order_id=data["id"],
                amount_minor=data["amount"],
                currency=data["currency"],
                receipt=data.get("receipt", ""),
                status=data["status"],
            )
        except httpx.TimeoutException as exc:
            raise DomainError("Razorpay fetch timed out", code=ErrorCode.PAYMENT_TIMEOUT) from exc
        except Exception as exc:
            if isinstance(exc, DomainError):
                raise exc
            raise DomainError(f"Razorpay error: {exc}", code=ErrorCode.SERVICE_UNAVAILABLE) from exc

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify a webhook signature against the webhook secret only.

        Deliberately does not fall back to the key secret. A webhook is signed
        with a different value than a payment callback, so accepting either
        would let a signature minted for one surface be replayed against the
        other — which is the separation the two verification paths depend on.
        No configured webhook secret means no verification, i.e. fail closed.
        """
        if not self.webhook_secret:
            return False
        expected = hmac.new(self.webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_payment_signature(self, payload: bytes, signature: str) -> bool:
        """Verify a payment callback signature against the key secret only."""
        if not self.key_secret:
            return False
        expected = hmac.new(self.key_secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def refund(self, provider_payment_id: str, amount_minor: int) -> ProviderRefund:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.post(
                    f"https://api.razorpay.com/v1/payments/{provider_payment_id}/refund",
                    json={"amount": amount_minor},
                    headers=self._auth_header(),
                )
            if res.status_code not in (200, 201):
                raise DomainError("Refund rejected by Razorpay", code=ErrorCode.SERVICE_UNAVAILABLE)
            data = res.json()
            return ProviderRefund(
                refund_id=data["id"],
                provider_payment_id=provider_payment_id,
                amount_minor=data["amount"],
                status=data.get("status", "processed"),
            )
        except Exception as exc:
            if isinstance(exc, DomainError):
                raise exc
            raise DomainError(f"Refund error: {exc}", code=ErrorCode.SERVICE_UNAVAILABLE) from exc
