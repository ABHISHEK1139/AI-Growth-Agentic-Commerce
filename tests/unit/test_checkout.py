"""Unit tests for checkout with price integrity (Task 15, Requirement 11, Properties 1, 2, 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import OfferV1, ProductSpecificationsV1
from services.checkout.hash import PriceSnapshot, compute_price_hash
from services.checkout.models import Checkout
from services.checkout.service import CheckoutService


def _sample_offer(
    offer_id: str = "off_1",
    unit_price_minor: int = 4999900,
    status: str = "active",
    expires_in_hours: int = 24,
) -> OfferV1:
    now = datetime.now(UTC)
    return OfferV1(
        schema_version="1.0",
        offer_id=offer_id,
        product_id="prod_1",
        merchant_id="merch_1",
        status=status,  # type: ignore[arg-type]
        unit_price_minor=unit_price_minor,
        currency="INR",
        available_quantity=10,
        delivery_days=2,
        return_period_days=14,
        expires_at=(now + timedelta(hours=expires_in_hours)).isoformat(),
        offer_version=1,
        pricing_source="synthetic_band_random",
        specifications=ProductSpecificationsV1(
            memory_gb=16,
            storage_gb=512,
            weight_grams=None,
            length_mm=None,
            width_mm=None,
            height_mm=None,
        ),
    )


# ---------------------------------------------------------------------------
# Property 4: Price hash stability and sensitivity
# ---------------------------------------------------------------------------


def test_price_hash_stability():
    """Property 4: Hash is deterministic for identical pricing tuples across calls."""
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    snap1 = PriceSnapshot(
        offer_id="off_1",
        offer_version=1,
        unit_price_minor=5000000,
        quantity=2,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        currency="INR",
        expires_at=now,
    )
    snap2 = PriceSnapshot(
        offer_id="off_1",
        offer_version=1,
        unit_price_minor=5000000,
        quantity=2,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        currency="INR",
        expires_at=now,
    )
    assert compute_price_hash(snap1) == compute_price_hash(snap2)


def test_price_hash_differs_on_any_field_change():
    """Property 4: Modifying any single factor results in a distinct hash."""
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    base = PriceSnapshot(
        offer_id="off_1",
        offer_version=1,
        unit_price_minor=5000000,
        quantity=1,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        currency="INR",
        expires_at=now,
    )
    base_hash = compute_price_hash(base)

    # Change offer version
    h_ver = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=2,
            unit_price_minor=5000000,
            quantity=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_ver != base_hash

    # Change unit price
    h_price = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=1,
            unit_price_minor=4999900,
            quantity=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_price != base_hash

    # Change quantity
    h_qty = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=1,
            unit_price_minor=5000000,
            quantity=2,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=0,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_qty != base_hash

    # Change discount
    h_disc = compute_price_hash(
        PriceSnapshot(
            offer_id="off_1",
            offer_version=1,
            unit_price_minor=5000000,
            quantity=1,
            shipping_minor=0,
            tax_minor=0,
            discount_minor=50000,
            currency="INR",
            expires_at=now,
        )
    )
    assert h_disc != base_hash


# ---------------------------------------------------------------------------
# Property 1 & 2: Server-calculated totals and exact integer arithmetic
# ---------------------------------------------------------------------------


def test_create_checkout_computes_exact_server_totals():
    """Property 1: subtotal + shipping + tax - discount == total exactly in minor units."""
    offer = _sample_offer(unit_price_minor=4999900)
    mock_offer_service = MagicMock()
    mock_offer_service.get_offer_by_id.return_value = offer

    mock_inventory_service = MagicMock()

    service = CheckoutService(
        offer_service=mock_offer_service,
        inventory_service=mock_inventory_service,
    )

    session = MagicMock()
    checkout = service.create_checkout(
        session,
        buyer_id="buy_1",
        merchant_id="merch_1",
        offer_id="off_1",
        quantity=2,
    )

    assert checkout.pricing.unit_price_minor == 4999900
    assert checkout.pricing.quantity == 2
    assert checkout.pricing.subtotal_minor == 4999900 * 2
    assert checkout.pricing.total_minor == 4999900 * 2
    assert checkout.price_hash is not None
    assert mock_inventory_service.reserve_stock.called


def test_create_checkout_fails_on_expired_offer():
    offer = _sample_offer(expires_in_hours=-1)
    mock_offer_service = MagicMock()
    mock_offer_service.get_offer_by_id.return_value = offer

    service = CheckoutService(offer_service=mock_offer_service)
    session = MagicMock()

    with pytest.raises(DomainError) as exc_info:
        service.create_checkout(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            offer_id="off_1",
            quantity=1,
        )
    assert exc_info.value.code == ErrorCode.OFFER_EXPIRED


def test_cancel_checkout_releases_inventory():
    now = datetime.now(UTC)
    mock_checkout = Checkout(
        checkout_id="chk_1",
        buyer_id="buy_1",
        merchant_id="merch_1",
        offer_id="off_1",
        offer_version=1,
        status="created",
        subtotal_minor=5000000,
        shipping_minor=0,
        tax_minor=0,
        discount_minor=0,
        total_minor=5000000,
        currency="INR",
        price_hash="hash_1",
        price_snapshot={},
        expires_at=now + timedelta(minutes=15),
        created_at=now,
    )

    mock_inventory_service = MagicMock()
    service = CheckoutService(inventory_service=mock_inventory_service)

    session = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_id.return_value = mock_checkout

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.checkout.service.CheckoutRepository", lambda s, scope: mock_repo)
        res = service.cancel_checkout(
            session,
            buyer_id="buy_1",
            merchant_id="merch_1",
            checkout_id="chk_1",
        )

    assert mock_inventory_service.release_stock.called
    assert res.status == "cancelled"
