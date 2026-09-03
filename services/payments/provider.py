"""Payment provider interface, fake provider, and provider order types (Task 18, Requirement 14)."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from packages.config.providers import PaymentProviderConfig
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    provider_order_id: str
    amount_minor: int
    currency: str
    receipt: str
    status: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    provider_payment_id: str
    provider_order_id: str
    amount_minor: int
    currency: str
    status: str
    method: str = "card"
    captured: bool = True
    error_code: str | None = None
    error_description: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRefund:
    refund_id: str
    provider_payment_id: str
    amount_minor: int
    status: str


class PaymentProvider(Protocol):
    """Protocol satisfied by real payment providers and the controllable fake."""

    def create_order(
        self,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ProviderOrder: ...

    def fetch_payment(self, provider_payment_id: str) -> ProviderPayment: ...

    def fetch_order(self, provider_order_id: str) -> ProviderOrder: ...

    def verify_signature(self, payload: bytes, signature: str) -> bool: ...

    def refund(self, provider_payment_id: str, amount_minor: int) -> ProviderRefund: ...


class FakePaymentProvider:
    """Deterministic, controllable fake payment provider for tests and zero-credential environments."""

    def __init__(
        self,
        secret: str = "fake_webhook_secret_key",  # noqa: S107
        behavior: Literal["success", "failure", "timeout", "invalid_signature"] = "success",
    ) -> None:
        self.name = "fake"
        self.secret = secret
        self.behavior: Literal["success", "failure", "timeout", "invalid_signature"] = behavior
        self._orders: dict[str, ProviderOrder] = {}
        self._payments: dict[str, ProviderPayment] = {}
        self._checkout_order_counts: dict[str, int] = {}

    def set_behavior(
        self, behavior: Literal["success", "failure", "timeout", "invalid_signature"]
    ) -> None:
        self.behavior = behavior

    def order_count_for(self, checkout_id: str) -> int:
        """Count of provider orders created for a checkout (for duplicate charge assertions)."""
        return self._checkout_order_counts.get(checkout_id, 0)

    def create_order(
        self,
        amount_minor: int,
        currency: str,
        receipt: str,
        notes: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ProviderOrder:
        notes_dict = notes if notes is not None else {}
        if amount_minor <= 0:
            raise DomainError("Order amount must be positive.", code=ErrorCode.VALIDATION_ERROR)
        if self.behavior == "timeout":
            raise DomainError("Payment provider timed out.", code=ErrorCode.PAYMENT_TIMEOUT)
        if self.behavior == "failure":
            raise DomainError(
                "Payment provider rejected the order request.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
            )

        order_id = f"order_fake_{new_id('pord')}"
        order = ProviderOrder(
            provider_order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
            receipt=receipt,
            status="created",
        )
        self._orders[order_id] = order

        checkout_id = str(notes_dict.get("checkout_id") or receipt)
        self._checkout_order_counts[checkout_id] = (
            self._checkout_order_counts.get(checkout_id, 0) + 1
        )
        return order

    def fetch_payment(self, provider_payment_id: str) -> ProviderPayment:
        if provider_payment_id in self._payments:
            return self._payments[provider_payment_id]
        # Unknown payment ids are NOT reported as captured. A verification gate
        # that trusts an id the caller invented is not a gate: reporting any
        # unknown id as "captured" let a fabricated identifier satisfy the
        # independent-verification check in `verify_payment`. Fail closed; a test
        # that needs a specific outcome stages it via `stage_payment`.
        return ProviderPayment(
            provider_payment_id=provider_payment_id,
            provider_order_id="order_fake_sample",
            amount_minor=5000000,
            currency="INR",
            status="failed",
            captured=False,
        )

    def stage_payment(self, payment: ProviderPayment) -> None:
        """Stage a known provider payment outcome for verification tests."""
        self._payments[payment.provider_payment_id] = payment

    def stage_order(self, order: ProviderOrder) -> None:
        """Stage a known provider order outcome for verification tests."""
        self._orders[order.provider_order_id] = order

    def fetch_order(self, provider_order_id: str) -> ProviderOrder:
        if provider_order_id in self._orders:
            return self._orders[provider_order_id]
        # Same fail-closed posture as `fetch_payment`: an order this provider did
        # not create is unpaid, never paid.
        return ProviderOrder(
            provider_order_id=provider_order_id,
            amount_minor=5000000,
            currency="INR",
            receipt="chk_sample",
            status="created",
        )

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        if self.behavior == "invalid_signature":
            return False
        expected = hmac.new(self.secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def refund(self, provider_payment_id: str, amount_minor: int) -> ProviderRefund:
        refund_id = f"rfnd_fake_{new_id('prfnd')}"
        return ProviderRefund(
            refund_id=refund_id,
            provider_payment_id=provider_payment_id,
            amount_minor=amount_minor,
            status="processed",
        )


_FAKE_PROVIDER_INSTANCE = FakePaymentProvider()


def get_payment_provider(config: PaymentProviderConfig | Any | None = None) -> PaymentProvider:
    """Resolve the configured payment provider (defaults to the controllable fake)."""
    if config is None:
        cfg = PaymentProviderConfig()
    elif isinstance(config, PaymentProviderConfig):
        cfg = config
    elif hasattr(config, "payment_provider_config") and callable(config.payment_provider_config):
        cfg = config.payment_provider_config()
    else:
        cfg = PaymentProviderConfig(
            provider=getattr(config, "payment_provider", getattr(config, "provider", "fake")),
            razorpay_key_id=getattr(config, "razorpay_key_id", ""),
            razorpay_key_secret=getattr(config, "razorpay_key_secret", ""),
            razorpay_webhook_secret=getattr(config, "razorpay_webhook_secret", ""),
            timeout_seconds=int(
                getattr(
                    config,
                    "payment_provider_timeout_seconds",
                    getattr(config, "timeout_seconds", 10),
                )
            ),
        )

    if cfg.provider == "razorpay" and cfg.razorpay_is_configured:
        from services.payments.razorpay_adapter import RazorpayPaymentProvider

        return RazorpayPaymentProvider(
            key_id=cfg.razorpay_key_id,
            key_secret=cfg.razorpay_key_secret,
            webhook_secret=cfg.razorpay_webhook_secret,
            timeout_seconds=cfg.timeout_seconds,
        )
    return _FAKE_PROVIDER_INSTANCE
