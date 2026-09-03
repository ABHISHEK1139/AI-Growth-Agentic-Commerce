"""Unit tests for the AI-reasoned and catalog-verified cross-sell recommendation engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from packages.security.principals import Role, Scope
from packages.security.tokens import issue_access_token
from services.agent.model import ModelResponse
from services.catalog.cross_sell import CrossSellRecommendation
from services.catalog.models import CategoryPairing, Product
from services.inventory.models import Inventory
from services.offers.models import Offer
from services.recommendations.service import RecommendationService


@pytest.fixture
def auth_headers(settings):
    issued = issue_access_token(
        secret=settings.jwt_secret,
        subject="test_agent",
        role=Role.BUYER,
        merchant_id="merchant_demo",
        buyer_id="buyer_1",
        ttl_seconds=3600,
        scopes=[Scope.CATALOG_READ],
    )
    return {"Authorization": f"Bearer {issued.token}"}


def test_cross_sell_with_real_db_pairings():
    """Merchant-curated category pairings with positive stock take priority."""
    service = RecommendationService()
    session = MagicMock()

    mock_target_prod = MagicMock(spec=Product)
    mock_target_prod.product_id = "prd_laptop_01"
    mock_target_prod.title = "Dell XPS 15 Laptop"
    mock_target_prod.category_id = "laptops"

    mock_target_offer = MagicMock(spec=Offer)
    mock_target_offer.unit_price_minor = 12000000
    mock_target_offer.currency = "INR"

    fake_pairing_rec = CrossSellRecommendation(
        pairing_id="pair_1",
        target_product_id="prd_mouse_01",
        target_title="Logitech MX Master 3S",
        target_category="accessories",
        target_unit_price_minor=899900,
        currency="INR",
        offer_id="off_mouse_01",
        available_quantity=15,
        rationale="Popular complementary accessory for laptops.",
    )

    def mock_query(model):
        q = MagicMock()
        if model is Product:
            q.filter.return_value.first.return_value = mock_target_prod
        elif model is Offer:
            q.filter.return_value.order_by.return_value.first.return_value = mock_target_offer
        else:
            q.filter.return_value.first.return_value = None
        return q

    session.query.side_effect = mock_query

    with patch(
        "services.catalog.cross_sell.CrossSellEngine.get_recommendations_for_product",
        return_value=[fake_pairing_rec],
    ):
        outcome = service.get_cross_sell_recommendations(
            session,
            merchant_id="merchant_demo",
            target_product_id="prd_laptop_01",
        )

    assert outcome.target_product_id == "prd_laptop_01"
    assert len(outcome.recommendations) >= 1
    assert outcome.recommendations[0].product_id == "prd_mouse_01"
    assert outcome.recommendations[0].offer_id == "off_mouse_01"
    assert outcome.recommendations[0].available_quantity == 15
    assert outcome.base_aov_minor == 12000000
    assert outcome.projected_aov_minor > outcome.base_aov_minor


def test_cross_sell_ai_reasoning_and_catalog_lookup():
    """AI suggests complementary keywords and service verifies real catalog products and stock."""
    mock_ai = MagicMock()
    mock_ai.generate.return_value = ModelResponse(
        content="ok",
        parsed_json={
            "suggestions": [
                {
                    "keyword": "hub",
                    "category": "accessories",
                    "reason": "Expands limited Thunderbolt ports with 4K HDMI.",
                }
            ]
        },
    )

    service = RecommendationService(model_provider=mock_ai)
    session = MagicMock()

    mock_target = MagicMock(spec=Product)
    mock_target.product_id = "prd_macbook_pro"
    mock_target.title = "Apple MacBook Pro M3"
    mock_target.category_id = "laptops"
    mock_target.specifications = {"ports": "Thunderbolt 4"}

    mock_accessory_prod = MagicMock(spec=Product)
    mock_accessory_prod.product_id = "prd_anker_hub"
    mock_accessory_prod.title = "Anker 7-in-1 USB-C Hub"
    mock_accessory_prod.category_id = "accessories"

    mock_accessory_offer = MagicMock(spec=Offer)
    mock_accessory_offer.offer_id = "off_anker_hub"
    mock_accessory_offer.unit_price_minor = 349900
    mock_accessory_offer.currency = "INR"

    mock_inventory = MagicMock(spec=Inventory)
    mock_inventory.available_quantity = 50
    mock_inventory.reserved_quantity = 5

    def mock_query(model):
        q = MagicMock()
        if model is Product:
            q.filter.return_value.first.return_value = mock_target
            q.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
                mock_accessory_prod
            ]
        elif model is Offer:
            q.filter.return_value.order_by.return_value.first.return_value = mock_accessory_offer
        elif model is Inventory:
            q.filter.return_value.first.return_value = mock_inventory
        elif model is CategoryPairing:
            q.filter.return_value.limit.return_value.all.return_value = []
        return q

    session.query.side_effect = mock_query

    outcome = service.get_cross_sell_recommendations(
        session,
        merchant_id="merchant_demo",
        target_product_id="prd_macbook_pro",
    )

    assert outcome.target_product_id == "prd_macbook_pro"
    assert len(outcome.recommendations) >= 1
    rec = outcome.recommendations[0]
    assert rec.product_id == "prd_anker_hub"
    assert rec.offer_id == "off_anker_hub"
    assert rec.price_minor == 349900
    assert rec.available_quantity == 45  # 50 - 5 reserved
    assert "Thunderbolt" in rec.compatibility_reason


def test_cross_sell_drops_out_of_stock_products():
    """If candidate accessory has 0 available inventory, it must NOT be recommended."""
    service = RecommendationService()
    session = MagicMock()

    mock_target = MagicMock(spec=Product)
    mock_target.product_id = "prd_macbook_pro"
    mock_target.title = "Apple MacBook Pro"
    mock_target.category_id = "laptops"

    mock_accessory_prod = MagicMock(spec=Product)
    mock_accessory_prod.product_id = "prd_out_of_stock_hub"
    mock_accessory_prod.title = "Out of Stock Hub"
    mock_accessory_prod.category_id = "accessories"

    mock_accessory_offer = MagicMock(spec=Offer)
    mock_accessory_offer.offer_id = "off_oos_hub"
    mock_accessory_offer.unit_price_minor = 299900
    mock_accessory_offer.currency = "INR"

    # Fully reserved stock (0 available)
    mock_inventory = MagicMock(spec=Inventory)
    mock_inventory.available_quantity = 10
    mock_inventory.reserved_quantity = 10

    def mock_query(model):
        q = MagicMock()
        if model is Product:
            q.filter.return_value.first.return_value = mock_target
            q.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [
                mock_accessory_prod
            ]
        elif model is Offer:
            q.filter.return_value.order_by.return_value.first.return_value = mock_accessory_offer
        elif model is Inventory:
            q.filter.return_value.first.return_value = mock_inventory
        elif model is CategoryPairing:
            q.filter.return_value.limit.return_value.all.return_value = []
        return q

    session.query.side_effect = mock_query

    outcome = service.get_cross_sell_recommendations(
        session,
        merchant_id="merchant_demo",
        target_product_id="prd_macbook_pro",
    )

    # Candidate was out of stock, so recommendations list is empty
    assert len(outcome.recommendations) == 0


def test_cross_sell_offline_fallback():
    """When session is None (offline mode), fallback recommendations are provided gracefully."""
    service = RecommendationService()
    outcome = service.get_cross_sell_recommendations(
        None,
        merchant_id="merchant_demo",
        target_product_id="prd_unknown_laptop",
    )

    assert outcome.target_product_id == "prd_unknown_laptop"
    assert len(outcome.recommendations) > 0
    assert outcome.base_aov_minor > 0
    assert outcome.projected_aov_minor > outcome.base_aov_minor


def test_cross_sell_respects_budget_limit():
    service = RecommendationService()
    outcome = service.get_cross_sell_recommendations(
        None,
        merchant_id="merchant_demo",
        target_product_id="prd_laptop",
        budget_limit_minor=100000,
    )
    for rec in outcome.recommendations:
        assert rec.price_minor <= 100000


def test_cross_sell_api_endpoint(client, auth_headers):
    res = client.post(
        "/api/v1/recommendations/cross-sell",
        json={"target_product_id": "prd_lenovo_ideapad_slim_5"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert "recommendations" in data
    assert "metrics" in data
    assert data["metrics"]["projected_aov_minor"] >= data["metrics"]["base_aov_minor"]


def test_cross_sell_metrics_endpoint(client, settings):
    issued = issue_access_token(
        secret=settings.jwt_secret,
        subject="test_merchant",
        role=Role.MERCHANT_ADMIN,
        merchant_id="merchant_demo",
        buyer_id=None,
        ttl_seconds=3600,
        scopes=[Scope.CATALOG_READ],
    )
    res = client.get(
        "/api/v1/recommendations/metrics",
        headers={"Authorization": f"Bearer {issued.token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["aov_growth_pct"] >= 0
    assert data["cross_sell_attachment_rate_pct"] >= 0
    assert data["currency"] == "INR"


def test_catalog_cross_sell_engine_skips_out_of_stock():
    """CrossSellEngine must not recommend products with zero available stock."""
    from services.catalog.cross_sell import CrossSellEngine

    session = MagicMock()

    pairing = MagicMock(spec=CategoryPairing)
    pairing.pairing_id = "pair_oos"
    pairing.target_category_id = "accessories"

    mock_pairing_query = MagicMock()
    mock_pairing_query.filter.return_value.limit.return_value.all.return_value = [pairing]

    mock_prod = MagicMock(spec=Product)
    mock_prod.product_id = "prod_oos"
    mock_prod.title = "Out of Stock Mouse"
    mock_prod.category_id = "accessories"

    mock_prod_query = MagicMock()
    mock_prod_query.filter.return_value.order_by.return_value.first.return_value = mock_prod

    mock_offer = MagicMock(spec=Offer)
    mock_offer.unit_price_minor = 99900
    mock_offer.offer_id = "off_oos"
    mock_offer.currency = "INR"

    mock_offer_query = MagicMock()
    mock_offer_query.filter.return_value.order_by.return_value.first.return_value = mock_offer

    mock_inv = MagicMock(spec=Inventory)
    mock_inv.available_quantity = 10
    mock_inv.reserved_quantity = 10  # All stock reserved

    mock_inv_query = MagicMock()
    mock_inv_query.filter.return_value.first.return_value = mock_inv

    def mock_query_router(model):
        if model is CategoryPairing:
            return mock_pairing_query
        if model is Product:
            return mock_prod_query
        if model is Offer:
            return mock_offer_query
        if model is Inventory:
            return mock_inv_query
        return MagicMock()

    session.query.side_effect = mock_query_router

    recs = CrossSellEngine.get_recommendations_for_product(
        session, merchant_id="merchant_demo", source_category="laptop"
    )

    assert len(recs) == 0
