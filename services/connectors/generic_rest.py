"""Generic REST API connector for custom merchant stores."""

from __future__ import annotations

import time
from typing import Any

import httpx

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.logging import get_logger
from services.connectors.base import (
    CanonicalOffer,
    CanonicalProduct,
    PlatformConnector,
    SyncResult,
)

logger = get_logger(__name__)


class GenericRestConnector(PlatformConnector):
    """Connector that ingests products and inventory from external merchant REST APIs."""

    def __init__(
        self,
        merchant_id: str,
        base_url: str = "https://api.merchantstore.com",
        api_key: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        # The same anti-SSRF policy the research worker applies: this URL is
        # fetched server-side with merchant credentials attached, so a loopback,
        # link-local, or cloud-metadata target is an exfiltration channel, not a
        # store endpoint.
        from services.research.safety.url_policy import is_safe_public_url

        if base_url and not is_safe_public_url(base_url):
            raise DomainError(
                f"Connector base_url '{base_url}' violates anti-SSRF policy: "
                "local, internal, and cloud metadata addresses are blocked.",
                code=ErrorCode.FORBIDDEN,
            )
        merged_config = dict(config or {})
        merged_config["base_url"] = base_url
        merged_config["api_key"] = api_key
        super().__init__(merchant_id, "generic_rest", merged_config)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def transform_raw_product(self, raw: dict[str, Any]) -> CanonicalProduct:
        """Map generic merchant JSON schema to AgentPay CanonicalProduct."""
        pid = str(raw.get("id") or raw.get("sku") or raw.get("product_id") or "prd_unknown")
        if not pid.startswith("prd_"):
            pid = f"prd_{pid}"

        return CanonicalProduct(
            product_id=pid,
            merchant_id=self.merchant_id,
            title=str(raw.get("title") or raw.get("name") or "Unnamed Product"),
            category=str(raw.get("category") or raw.get("type") or "electronics").lower(),
            brand=str(raw.get("brand") or raw.get("vendor") or "Generic"),
            description=str(raw.get("description") or raw.get("summary") or ""),
            attributes=raw.get("attributes") or raw.get("specs") or {},
            image_url=raw.get("image_url") or raw.get("image"),
        )

    def transform_raw_offer(self, raw: dict[str, Any], product_id: str) -> CanonicalOffer:
        """Map generic merchant pricing and stock to AgentPay CanonicalOffer.

        Prices are accepted as integer minor units only. The old heuristic —
        "any value below 10000 must be major units, multiply by 100" — silently
        overcharged 100× on every legitimately cheap item (₹99.99 = 9990 paise
        became 999000). A feed that cannot say which unit it means is refused,
        not guessed at.
        """
        raw_price = raw.get("price_minor")
        if raw_price is None and raw.get("price") is not None:
            raise DomainError(
                "Connector feeds must supply prices as integer minor units "
                "('price_minor'); ambiguous 'price' fields are not accepted.",
                code=ErrorCode.VALIDATION_ERROR,
            )
        if isinstance(raw_price, float) or not isinstance(raw_price, int) or raw_price < 0:
            raise DomainError(
                "Connector price_minor must be a non-negative integer amount in minor units.",
                code=ErrorCode.VALIDATION_ERROR,
            )
        price_minor = raw_price

        stock = int(raw.get("stock") or raw.get("quantity") or raw.get("inventory_level") or 0)
        currency = str(raw.get("currency") or "INR").upper()

        oid = f"ofr_{product_id[4:] if product_id.startswith('prd_') else product_id}"

        return CanonicalOffer(
            offer_id=oid,
            product_id=product_id,
            merchant_id=self.merchant_id,
            unit_price_minor=price_minor,
            currency=currency,
            available_stock=stock,
            delivery_days=int(raw.get("delivery_days") or 2),
            return_period_days=int(raw.get("return_period_days") or 14),
        )

    def _fetch_live_products(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.base_url or "api.merchantstore.com" in self.base_url:
            return []
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            with httpx.Client(timeout=8.0) as client:
                res = client.get(
                    f"{self.base_url}/products", headers=headers, params={"limit": limit}
                )
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict) and "products" in data:
                        return data["products"]
        except Exception:
            logger.warning(
                "generic_rest_fetch_failed", extra={"base_url": self.base_url}, exc_info=True
            )
        return []

    def fetch_products(self, limit: int = 100) -> list[CanonicalProduct]:
        # Live endpoint first
        live_data = self._fetch_live_products(limit)
        if live_data:
            return [self.transform_raw_product(item) for item in live_data[:limit]]

        # Fallback to configured items if offline
        mock_items = self.config.get("mock_items")
        if mock_items and isinstance(mock_items, list):
            return [self.transform_raw_product(item) for item in mock_items[:limit]]

        return []

    def fetch_offers(self, limit: int = 100) -> list[CanonicalOffer]:
        live_data = self._fetch_live_products(limit)
        if live_data:
            return [
                self.transform_raw_offer(item, self.transform_raw_product(item).product_id)
                for item in live_data[:limit]
            ]

        mock_items = self.config.get("mock_items")
        if mock_items and isinstance(mock_items, list):
            return [
                self.transform_raw_offer(item, self.transform_raw_product(item).product_id)
                for item in mock_items[:limit]
            ]
        return []

    def sync_catalog(self) -> SyncResult:
        start = time.perf_counter()
        products = self.fetch_products()
        offers = self.fetch_offers()
        duration = (time.perf_counter() - start) * 1000.0

        return SyncResult(
            platform_type="generic_rest",
            merchant_id=self.merchant_id,
            products_imported=len(products),
            offers_updated=len(offers),
            duration_ms=round(duration, 2),
            status="success",
        )

    def push_order(self, order_id: str, checkout_payload: dict[str, Any]) -> dict[str, Any]:
        if not order_id:
            raise DomainError("order_id required", code=ErrorCode.VALIDATION_ERROR)

        if self.base_url and "api.merchantstore.com" not in self.base_url:
            try:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                payload = {"order_id": order_id, "checkout": checkout_payload}
                with httpx.Client(timeout=8.0) as client:
                    res = client.post(f"{self.base_url}/orders", json=payload, headers=headers)
                    if res.status_code in (200, 201):
                        return {
                            "status": "received",
                            "order_id": order_id,
                            "merchant_id": self.merchant_id,
                            "platform_type": "generic_rest",
                            "external_reference": res.json().get("external_id", f"ext_{order_id}"),
                            "message": f"Order {order_id} successfully acknowledged by merchant REST endpoint.",
                        }
            except Exception:
                logger.warning(
                    "generic_rest_order_push_failed", extra={"order_id": order_id}, exc_info=True
                )
                return {
                    "status": "failed",
                    "order_id": order_id,
                    "merchant_id": self.merchant_id,
                    "platform_type": "generic_rest",
                    "external_reference": None,
                    "message": f"Order {order_id} failed to push to merchant REST endpoint. Will retry.",
                }

        return {
            "status": "queued",
            "order_id": order_id,
            "merchant_id": self.merchant_id,
            "platform_type": "generic_rest",
            "external_reference": None,
            "message": f"Order {order_id} queued (no live merchant REST endpoint configured).",
        }
