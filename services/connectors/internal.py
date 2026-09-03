"""Internal seed catalog connector for standalone store and buildathon demo."""

from __future__ import annotations

import time
from typing import Any

from services.connectors.base import (
    CanonicalOffer,
    CanonicalProduct,
    PlatformConnector,
    SyncResult,
)


class InternalSeedConnector(PlatformConnector):
    """Connector reading from AgentPay's built-in demo catalog and local database."""

    _SEED_DATA: list[dict[str, Any]] = [
        {
            "product_id": "prd_macbook_pro_14",
            "title": "Apple MacBook Pro 14 M3 Pro",
            "category": "laptops",
            "brand": "Apple",
            "description": "Apple M3 Pro chip with 12-core CPU and 18-core GPU, 18GB Unified Memory, 512GB SSD.",
            "attributes": {
                "RAM": "18GB Unified",
                "Storage": "512GB SSD",
                "Display": "14.2 Liquid Retina XDR",
            },
            "price_minor": 19990000,
            "currency": "INR",
            "stock": 14,
        },
        {
            "product_id": "prd_lenovo_ideapad_slim_5",
            "title": "Lenovo IdeaPad Slim 5 16IAH8",
            "category": "laptops",
            "brand": "Lenovo",
            "description": "16-inch WUXGA IPS display, AMD Ryzen 7 7730U, 16GB DDR4, 512GB SSD.",
            "attributes": {
                "RAM": "16GB DDR4",
                "Storage": "512GB SSD",
                "Ports": "2x USB-A 3.2 Gen 1 (5Gbps), 1x USB-C 3.2 Gen 1, HDMI 1.4b",
            },
            "price_minor": 6499900,
            "currency": "INR",
            "stock": 28,
        },
        {
            "product_id": "prd_dell_xps_15",
            "title": "Dell XPS 15 9530",
            "category": "laptops",
            "brand": "Dell",
            "description": "15.6-inch OLED 3.5K display, Intel Core i7-13700H, 32GB DDR5, 1TB SSD, RTX 4060.",
            "attributes": {"RAM": "32GB DDR5", "Storage": "1TB NVMe", "GPU": "NVIDIA RTX 4060 8GB"},
            "price_minor": 18499000,
            "currency": "INR",
            "stock": 8,
        },
    ]

    def __init__(self, merchant_id: str = "mer_demo_seed", config: dict[str, Any] | None = None):
        super().__init__(merchant_id, "internal_seed", config)

    def fetch_products(self, limit: int = 100) -> list[CanonicalProduct]:
        return [
            CanonicalProduct(
                product_id=p["product_id"],
                merchant_id=self.merchant_id,
                title=p["title"],
                category=p["category"],
                brand=p["brand"],
                description=p["description"],
                attributes=p.get("attributes", {}),
            )
            for p in self._SEED_DATA[:limit]
        ]

    def fetch_offers(self, limit: int = 100) -> list[CanonicalOffer]:
        return [
            CanonicalOffer(
                offer_id=f"ofr_{p['product_id'][4:]}",
                product_id=p["product_id"],
                merchant_id=self.merchant_id,
                unit_price_minor=p["price_minor"],
                currency=p["currency"],
                available_stock=p["stock"],
                delivery_days=2,
                return_period_days=14,
            )
            for p in self._SEED_DATA[:limit]
        ]

    def sync_catalog(self) -> SyncResult:
        start = time.perf_counter()
        products = self.fetch_products()
        offers = self.fetch_offers()
        duration = (time.perf_counter() - start) * 1000.0

        return SyncResult(
            platform_type="internal_seed",
            merchant_id=self.merchant_id,
            products_imported=len(products),
            offers_updated=len(offers),
            duration_ms=round(duration, 2),
            status="success",
        )

    def push_order(self, order_id: str, checkout_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "confirmed",
            "order_id": order_id,
            "merchant_id": self.merchant_id,
            "platform_type": "internal_seed",
            "message": "Order recorded in local store ledger.",
        }
