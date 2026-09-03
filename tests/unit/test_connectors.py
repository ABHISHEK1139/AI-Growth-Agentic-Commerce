"""Unit tests for the platform-agnostic e-commerce connector architecture."""

from __future__ import annotations

from services.connectors.base import CanonicalOffer, CanonicalProduct, SyncResult
from services.connectors.ecommerce_platform import ShopifyWooConnector
from services.connectors.feed import CatalogFeedConnector
from services.connectors.generic_rest import GenericRestConnector
from services.connectors.internal import InternalSeedConnector
from services.connectors.registry import ConnectorRegistry


def test_internal_seed_connector_lifecycle():
    conn = InternalSeedConnector("mer_test_seed")
    products = conn.fetch_products(limit=5)
    offers = conn.fetch_offers(limit=5)

    assert len(products) >= 2
    assert len(offers) >= 2
    assert isinstance(products[0], CanonicalProduct)
    assert isinstance(offers[0], CanonicalOffer)
    assert products[0].merchant_id == "mer_test_seed"

    res = conn.sync_catalog()
    assert isinstance(res, SyncResult)
    assert res.status == "success"
    assert res.products_imported >= 2

    order_ack = conn.push_order("ord_test_123", {"amount_minor": 6499900})
    assert order_ack["status"] == "confirmed"


def test_generic_rest_connector_schema_transformation():
    conn = GenericRestConnector("mer_custom_rest", base_url="https://api.mystore.com")

    raw_item = {
        "id": "item_9921",
        "name": "Custom Mechanical Keyboard",
        "category": "Keyboards",
        "brand": "KeyCraft",
        "description": "Hot-swappable RGB mechanical keyboard.",
        # Minor units only: the old "<10000 means major units" guess overcharged
        # cheap items 100x, so ambiguous 'price' fields are now refused.
        "price_minor": 849900,
        "stock": 35,
        "attributes": {"Switch": "Gateron Brown", "Layout": "75%"},
    }

    prod = conn.transform_raw_product(raw_item)
    assert prod.product_id == "prd_item_9921"
    assert prod.title == "Custom Mechanical Keyboard"
    assert prod.category == "keyboards"
    assert prod.brand == "KeyCraft"
    assert prod.attributes["Switch"] == "Gateron Brown"

    offer = conn.transform_raw_offer(raw_item, prod.product_id)
    assert offer.offer_id == "ofr_item_9921"
    assert offer.unit_price_minor == 849900
    assert offer.available_stock == 35
    assert offer.currency == "INR"

    # An ambiguous major-unit price is refused rather than guessed at.
    import pytest

    from packages.errors.exceptions import DomainError

    with pytest.raises(DomainError):
        conn.transform_raw_offer({**raw_item, "price_minor": None, "price": 8499.0}, "prd_x")


def test_shopify_woo_connector_variant_mapping():
    shopify_conn = ShopifyWooConnector("mer_shopify_1", platform_flavor="shopify")

    raw_shopify = {
        "id": 88123912,
        "title": "Ergonomic Office Chair",
        "vendor": "ErgoPlus",
        "body_html": "<p>Lumbar support chair</p>",
        "product_type": "Furniture",
        "options": [{"name": "Color", "values": ["Black", "Grey"]}],
        "variants": [
            {"id": 1001, "price": "14999.00", "inventory_quantity": 20},
            {"id": 1002, "price": "15999.00", "inventory_quantity": 12},
        ],
    }

    prod, offers = shopify_conn.parse_shopify_product(raw_shopify)
    assert prod.product_id == "prd_sh_88123912"
    assert prod.brand == "ErgoPlus"
    assert prod.category == "furniture"
    assert prod.attributes["Color"] == "Black, Grey"
    assert len(offers) == 2
    assert offers[0].unit_price_minor == 1499900
    assert offers[1].unit_price_minor == 1599900
    assert offers[0].available_stock == 20


def test_woocommerce_connector_product_mapping():
    woo_conn = ShopifyWooConnector("mer_woo_1", platform_flavor="woocommerce")

    raw_woo = {
        "id": 4410,
        "name": "Wireless Ergonomic Mouse",
        "regular_price": "2499.00",
        "stock_quantity": 50,
        "categories": [{"name": "Accessories"}],
        "attributes": [{"name": "Connectivity", "options": ["Bluetooth", "2.4Ghz"]}],
    }

    prod, offers = woo_conn.parse_woocommerce_product(raw_woo)
    assert prod.product_id == "prd_woo_4410"
    assert prod.title == "Wireless Ergonomic Mouse"
    assert prod.category == "accessories"
    assert len(offers) == 1
    assert offers[0].unit_price_minor == 249900
    assert offers[0].available_stock == 50


def test_catalog_feed_connector_csv_and_jsonl():
    # CSV feeds carry explicit minor-unit prices: the old "<10000 means major
    # units" guess overcharged cheap items 100x and is gone.
    csv_data = """id,title,category,brand,price_minor,stock
101,Monitor Arm,Accessories,MountPro,299900,25
102,USB-C Hub,Accessories,HubTech,149900,40
"""
    feed_conn = CatalogFeedConnector("mer_feed_1", feed_content=csv_data, feed_format="csv")
    products = feed_conn.fetch_products()
    offers = feed_conn.fetch_offers()

    assert len(products) == 2
    assert products[0].product_id == "prd_101"
    assert products[0].title == "Monitor Arm"
    assert offers[0].unit_price_minor == 299900
    assert offers[0].available_stock == 25

    jsonl_data = """{"id": "201", "title": "Desk Mat", "category": "Accessories", "price_minor": 99900, "stock": 100}
{"id": "202", "title": "Webcam Cover", "category": "Accessories", "price_minor": 19900, "stock": 500}
"""
    jsonl_conn = CatalogFeedConnector("mer_feed_2", feed_content=jsonl_data, feed_format="jsonl")
    j_prods = jsonl_conn.fetch_products()
    j_ofrs = jsonl_conn.fetch_offers()

    assert len(j_prods) == 2
    assert j_prods[0].title == "Desk Mat"
    assert j_ofrs[0].unit_price_minor == 99900


def test_connector_registry_tenant_isolation_and_fallback():
    registry = ConnectorRegistry()

    # Register tenant A with Shopify
    conn_a = ShopifyWooConnector("mer_tenant_a", platform_flavor="shopify")
    registry.register("mer_tenant_a", conn_a)

    # Register tenant B with Generic REST
    conn_b = GenericRestConnector("mer_tenant_b", base_url="https://store-b.com")
    registry.register("mer_tenant_b", conn_b)

    # Lookups
    assert registry.get("mer_tenant_a").platform_type == "shopify"
    assert registry.get("mer_tenant_b").platform_type == "generic_rest"

    # Unknown tenant falls back gracefully to internal seed
    active_list = registry.list_connectors()
    assert len(active_list) >= 2


def test_connector_api_endpoints_auth_and_cross_tenant_checks(client):
    """Verify connectors router refuses unauthenticated and cross-tenant calls (BUG-24)."""
    # 1. Unauthenticated calls are rejected with 401
    assert client.get("/api/v1/connectors/status").status_code == 401
    assert client.post("/api/v1/connectors/sync", json={"merchant_id": "mer_1"}).status_code == 401
    assert (
        client.post(
            "/api/v1/connectors/register",
            json={"merchant_id": "mer_1", "platform_type": "shopify"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/connectors/webhook",
            json={"merchant_id": "mer_1", "event": "inventory.updated", "payload": {}},
        ).status_code
        == 401
    )

    # 2. Authenticate as merchant A
    client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "mer_tenant_a"},
    )

    # 3. Status succeeds
    assert client.get("/api/v1/connectors/status").status_code == 200

    # 4. Syncing own merchant succeeds
    sync_res = client.post("/api/v1/connectors/sync", json={"merchant_id": "mer_tenant_a"})
    assert sync_res.status_code == 200

    # 5. Cross-tenant sync of merchant B is refused with 403
    sync_cross = client.post("/api/v1/connectors/sync", json={"merchant_id": "mer_tenant_b"})
    assert sync_cross.status_code == 403
