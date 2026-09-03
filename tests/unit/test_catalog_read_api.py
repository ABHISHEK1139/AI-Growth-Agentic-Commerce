"""Unit tests for catalog search, offers query, and validation APIs (Task 13, Requirement 7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import OfferV1
from services.catalog.models import Product
from services.inventory.models import Inventory
from services.offers.models import Offer
from services.offers.service import OfferService, _offer_to_schema


def _make_sample_entities(
    offer_id: str = "off_1",
    product_id: str = "prod_1",
    merchant_id: str = "merch_1",
    price_minor: int = 5000000,
    status: str = "active",
    avail_qty: int = 5,
    res_qty: int = 0,
    expires_in_days: int = 7,
    specs: dict | None = None,
) -> tuple[Offer, Product, Inventory]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=expires_in_days)

    prod = Product(
        product_id=product_id,
        catalog_version_id="cat_1",
        merchant_id=merchant_id,
        external_product_id="ext_1",
        category_id="laptop",
        title="15-inch Pro Laptop",
        status="valid",
        description=["High performance"],
        specifications=specs or {"memory_gb": 16, "storage_gb": 512},
        average_rating=4.8,
        rating_number=50,
        created_at=now,
    )

    offer = Offer(
        offer_id=offer_id,
        catalog_version_id="cat_1",
        product_id=product_id,
        variant_id=None,
        merchant_id=merchant_id,
        status=status,
        unit_price_minor=price_minor,
        currency="INR",
        delivery_days=2,
        return_period_days=14,
        pricing_source="synthetic_band_random",
        offer_version=1,
        expires_at=expires_at,
        created_at=now,
    )

    inventory = Inventory(
        offer_id=offer_id,
        available_quantity=avail_qty,
        reserved_quantity=res_qty,
        version=1,
    )

    return offer, prod, inventory


def test_offer_to_schema_conversion():
    offer, prod, inv = _make_sample_entities()
    schema = _offer_to_schema(offer, prod, inv)
    assert isinstance(schema, OfferV1)
    assert schema.offer_id == "off_1"
    assert schema.unit_price_minor == 5000000
    assert schema.available_quantity == 5
    assert schema.specifications.memory_gb == 16
    assert schema.specifications.storage_gb == 512


def test_search_offers_honours_filters():
    offer, prod, inv = _make_sample_entities()
    session = MagicMock()

    mock_repo = MagicMock()
    # The repository returns the primary product image alongside each row, so the
    # service has one round trip rather than one per result.
    mock_repo.search_offers.return_value = [(offer, prod, inv, "https://example.test/1.jpg")]

    service = OfferService()

    # Mock repository call
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.offers.service.OfferRepository", lambda s, scope: mock_repo)
        results = service.search_offers(
            session,
            merchant_id="merch_1",
            category="laptop",
            max_price_minor=7000000,
            min_memory_gb=16,
            min_storage_gb=512,
            max_delivery_days=3,
            quantity=2,
            limit=5,
        )

    assert len(results) == 1
    assert results[0].offer_id == "off_1"
    assert results[0].specifications.memory_gb == 16

    # Every filter must reach the repository. Asserting the returned row is not
    # enough: a dropped filter still returns the mocked row, so the test would
    # pass while the constraint was silently discarded.
    passed = mock_repo.search_offers.call_args.kwargs["constraints"]
    assert passed.category == "laptop"
    assert passed.max_price_minor == 7000000
    assert passed.min_memory_gb == 16
    assert passed.min_storage_gb == 512
    assert passed.max_delivery_days == 3
    assert passed.quantity == 2
    assert passed.capped_limit == 5


def test_validate_offer_success():
    offer, prod, inv = _make_sample_entities()
    session = MagicMock()

    service = OfferService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            service,
            "get_offer_by_id",
            lambda s, merchant_id, offer_id: _offer_to_schema(offer, prod, inv),
        )
        validated = service.validate_offer(
            session,
            merchant_id="merch_1",
            offer_id="off_1",
            expected_price_minor=5000000,
            expected_offer_version=1,
        )
    assert validated.offer_id == "off_1"


def test_validate_offer_price_changed_raises():
    offer, prod, inv = _make_sample_entities(price_minor=6000000)
    session = MagicMock()

    service = OfferService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            service,
            "get_offer_by_id",
            lambda s, merchant_id, offer_id: _offer_to_schema(offer, prod, inv),
        )
        with pytest.raises(DomainError) as exc_info:
            service.validate_offer(
                session,
                merchant_id="merch_1",
                offer_id="off_1",
                expected_price_minor=5000000,
            )
        assert exc_info.value.code == ErrorCode.PRICE_CHANGED


def test_validate_offer_expired_raises():
    offer, prod, inv = _make_sample_entities(expires_in_days=-1)
    session = MagicMock()

    service = OfferService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            service,
            "get_offer_by_id",
            lambda s, merchant_id, offer_id: _offer_to_schema(offer, prod, inv),
        )
        with pytest.raises(DomainError) as exc_info:
            service.validate_offer(
                session,
                merchant_id="merch_1",
                offer_id="off_1",
            )
        assert exc_info.value.code == ErrorCode.OFFER_EXPIRED


def test_validate_offer_out_of_stock_raises():
    offer, prod, inv = _make_sample_entities(avail_qty=2, res_qty=2)  # effective available = 0
    session = MagicMock()

    service = OfferService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            service,
            "get_offer_by_id",
            lambda s, merchant_id, offer_id: _offer_to_schema(offer, prod, inv),
        )
        with pytest.raises(DomainError) as exc_info:
            service.validate_offer(
                session,
                merchant_id="merch_1",
                offer_id="off_1",
            )
        assert exc_info.value.code == ErrorCode.INVENTORY_UNAVAILABLE
