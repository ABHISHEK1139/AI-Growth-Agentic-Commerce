"""An in-memory :class:`packages.commerce.CommerceFacade` for tests.

The point of this double is that the agent loop can be exercised end to end with
no database, no session, and no transaction. If the loop ever reacquires a
persistence dependency, tests built on this fake stop compiling rather than
silently starting a database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from packages.schemas.v1 import (
    AuthorizationV1,
    CheckoutV1,
    OfferV1,
    PaymentV1,
    PolicyDecisionV1,
    PriceBreakdownV1,
    ProductSpecificationsV1,
)

SPECIFICATIONS = ProductSpecificationsV1(
    memory_gb=16,
    storage_gb=512,
    weight_grams=1400,
    length_mm=320,
    width_mm=220,
    height_mm=18,
)


def make_offer(
    offer_id: str = "off_1",
    merchant_id: str = "merch_1",
    unit_price_minor: int = 6_500_000,
) -> OfferV1:
    return OfferV1(
        schema_version="1.0",
        offer_id=offer_id,
        product_id="prd_1",
        merchant_id=merchant_id,
        status="active",
        unit_price_minor=unit_price_minor,
        currency="INR",
        available_quantity=5,
        delivery_days=2,
        return_period_days=7,
        expires_at="2999-01-01T00:00:00+00:00",
        offer_version=1,
        pricing_source="synthetic_band_random",
        specifications=SPECIFICATIONS,
    )


def make_checkout(
    checkout_id: str = "chk_1",
    buyer_id: str = "buy_1",
    merchant_id: str = "merch_1",
    quantity: int = 1,
    unit_price_minor: int = 6_500_000,
) -> CheckoutV1:
    subtotal = unit_price_minor * quantity
    return CheckoutV1(
        schema_version="1.0",
        checkout_id=checkout_id,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        offer_id="off_1",
        offer_version=1,
        product_id="prd_1",
        status="created",
        pricing=PriceBreakdownV1(
            unit_price_minor=unit_price_minor,
            quantity=quantity,
            subtotal_minor=subtotal,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            total_minor=subtotal,
            currency="INR",
        ),
        price_hash="a" * 64,
        expires_at="2999-01-01T00:00:00+00:00",
    )


def make_authorization(
    authorization_id: str = "ath_1",
    checkout_id: str = "chk_1",
    buyer_id: str = "buy_1",
    merchant_id: str = "merch_1",
) -> AuthorizationV1:
    return AuthorizationV1(
        schema_version="1.0",
        authorization_id=authorization_id,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        checkout_id=checkout_id,
        amount_ceiling_minor=6_500_000,
        currency="INR",
        category="laptop",
        price_hash="a" * 64,
        status="pending",
        valid_until="2999-01-01T00:00:00+00:00",
        policy=PolicyDecisionV1(
            decision="REQUIRE_APPROVAL",
            reason_code="AMOUNT_ABOVE_AUTO_LIMIT",
            policy_version="v1",
        ),
    )


def make_payment(
    payment_id: str = "pay_1",
    checkout_id: str = "chk_1",
    authorization_id: str = "ath_1",
) -> PaymentV1:
    return PaymentV1(
        schema_version="1.0",
        payment_id=payment_id,
        checkout_id=checkout_id,
        authorization_id=authorization_id,
        provider="fake",
        provider_order_id="order_fake_1",
        provider_payment_id=None,
        public_key="rzp_test_public",
        amount_minor=6_500_000,
        currency="INR",
        status="pending",
        test_mode=True,
    )


class FakeCommerceFacade:
    """Records every call and returns valid schema contracts. Holds no session."""

    def __init__(self, offers: Sequence[OfferV1] | None = None) -> None:
        self.offers: list[OfferV1] = list(offers) if offers is not None else [make_offer()]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.events: list[dict[str, Any]] = []

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    @property
    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    # -- read-only capabilities ------------------------------------------------

    def search_offers(
        self,
        *,
        merchant_id: str,
        category: str | None = None,
        max_price_minor: int | None = None,
        min_memory_gb: int | None = None,
        min_storage_gb: int | None = None,
        max_delivery_days: int | None = None,
        limit: int = 10,
    ) -> list[OfferV1]:
        self._record(
            "search_offers",
            merchant_id=merchant_id,
            category=category,
            max_price_minor=max_price_minor,
            min_memory_gb=min_memory_gb,
            min_storage_gb=min_storage_gb,
            max_delivery_days=max_delivery_days,
            limit=limit,
        )
        return list(self.offers[:limit])

    def get_offer(self, *, merchant_id: str, offer_id: str) -> OfferV1:
        self._record("get_offer", merchant_id=merchant_id, offer_id=offer_id)
        return make_offer(offer_id=offer_id, merchant_id=merchant_id)

    def compare_offers(self, *, merchant_id: str, offer_ids: Sequence[str]) -> list[OfferV1]:
        self._record("compare_offers", merchant_id=merchant_id, offer_ids=list(offer_ids))
        return [make_offer(offer_id=oid, merchant_id=merchant_id) for oid in offer_ids]

    # -- state-changing capabilities ------------------------------------------

    def create_checkout(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        offer_id: str,
        quantity: int = 1,
    ) -> CheckoutV1:
        self._record(
            "create_checkout",
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            offer_id=offer_id,
            quantity=quantity,
        )
        return make_checkout(buyer_id=buyer_id, merchant_id=merchant_id, quantity=quantity)

    def request_authorization(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
    ) -> AuthorizationV1:
        self._record(
            "request_authorization",
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            checkout_id=checkout_id,
        )
        return make_authorization(
            checkout_id=checkout_id, buyer_id=buyer_id, merchant_id=merchant_id
        )

    def create_payment(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
        authorization_id: str,
        idempotency_key: str | None = None,
    ) -> PaymentV1:
        self._record(
            "create_payment",
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            checkout_id=checkout_id,
            authorization_id=authorization_id,
            idempotency_key=idempotency_key,
        )
        return make_payment(checkout_id=checkout_id, authorization_id=authorization_id)

    # -- observability ---------------------------------------------------------

    def record_agent_event(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        actor_type: str,
        actor_id: str | None,
        merchant_id: str | None = None,
        model_version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        event = {
            "event_type": event_type,
            "aggregate_id": aggregate_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "merchant_id": merchant_id,
            "model_version": model_version,
            "metadata": dict(metadata) if metadata is not None else None,
        }
        self.events.append(event)
        self._record("record_agent_event", **event)
        return f"aud_fake_{len(self.events)}"
