"""Shopify & WooCommerce connector with native variant and inventory mapping."""

from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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


def parse_price_minor(value: Any) -> int:
    """Parse a platform price string into integer minor units, exactly.

    ``int(float(s) * 100)`` truncates binary floating-point error into a
    systematic 1-paise undercharge ("10.29" -> 1028). Decimal arithmetic
    with half-up rounding is exact for two-decimal prices.
    """
    if isinstance(value, bool):
        raise DomainError(
            "Connector price must be a decimal string or number.",
            code=ErrorCode.VALIDATION_ERROR,
        )
    try:
        text = str(value).strip().lstrip("$").replace(",", "")
        minor = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100
        result = int(minor.to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise DomainError(
            "Connector price must be a decimal string or number.",
            code=ErrorCode.VALIDATION_ERROR,
        ) from exc
    if result <= 0:
        raise DomainError(
            "Connector price must be positive.",
            code=ErrorCode.VALIDATION_ERROR,
        )
    return result


def parse_stock_quantity(value: Any, *, default_when_missing: int = 10) -> int:
    """Parse a stock count, distinguishing missing (default) from zero (out).

    ``int(value or default)`` treats an explicit 0 as missing and invents
    stock that turns into oversells. None/missing takes the default; anything
    present is honored, including 0.
    """
    if value is None:
        return default_when_missing
    try:
        return max(0, int(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return 0


class ShopifyWooConnector(PlatformConnector):
    """Adapter for Shopify & WooCommerce stores."""

    def __init__(
        self,
        merchant_id: str,
        platform_flavor: str = "shopify",
        store_domain: str = "mystore.myshopify.com",
        access_token: str | None = None,
        config: dict[str, Any] | None = None,
    ):
        merged = dict(config or {})
        merged["platform_flavor"] = platform_flavor
        merged["store_domain"] = store_domain
        merged["access_token"] = access_token
        # Server-side fetches carry the merchant's access token, so a loopback
        # or metadata target is an SSRF exfiltration channel. Same policy as the
        # research worker's URL gate.
        from services.research.safety.url_policy import is_safe_public_url

        # A bare domain ("mystore.myshopify.com") is normalized to https before
        # the policy check: urlparse treats a scheme-less string as a relative
        # path with no hostname, which would fail the check spuriously.
        candidate = str(store_domain)
        if candidate and not candidate.startswith("http"):
            candidate = f"https://{candidate}"
        if candidate and not is_safe_public_url(candidate):
            raise DomainError(
                f"Connector store_domain '{store_domain}' violates anti-SSRF policy: "
                "local, internal, and cloud metadata addresses are blocked.",
                code=ErrorCode.FORBIDDEN,
            )
        super().__init__(
            merchant_id,
            "shopify" if platform_flavor.lower() == "shopify" else "woocommerce",
            merged,
        )
        self.flavor = platform_flavor.lower()
        self.store_domain = store_domain
        self.access_token = access_token

    def parse_shopify_product(
        self, raw: dict[str, Any]
    ) -> tuple[CanonicalProduct, list[CanonicalOffer]]:
        pid = f"prd_sh_{raw.get('id', 'item')}"
        title = raw.get("title", "Shopify Product")
        vendor = raw.get("vendor", "Shopify Vendor")
        body = raw.get("body_html", "")
        category = "general"
        if raw.get("product_type"):
            category = str(raw["product_type"]).lower()

        attributes = {}
        for opt in raw.get("options", []):
            name = opt.get("name")
            values = opt.get("values")
            if name and values:
                attributes[name] = ", ".join(str(v) for v in values)

        product = CanonicalProduct(
            product_id=pid,
            merchant_id=self.merchant_id,
            title=title,
            category=category,
            brand=vendor,
            description=body,
            attributes=attributes,
            image_url=(raw.get("images") or [{}])[0].get("src"),
        )

        offers: list[CanonicalOffer] = []
        for v in raw.get("variants", []):
            vid = v.get("id")
            try:
                price_minor = parse_price_minor(v.get("price", "0"))
            except DomainError:
                continue

            stock = parse_stock_quantity(v.get("inventory_quantity"), default_when_missing=0)
            offers.append(
                CanonicalOffer(
                    offer_id=f"ofr_sh_{vid}",
                    product_id=pid,
                    merchant_id=self.merchant_id,
                    unit_price_minor=price_minor,
                    currency="INR",
                    available_stock=stock,
                    delivery_days=2,
                    return_period_days=14,
                )
            )

        # A product with no priced variants carries no price signal. Minting
        # a ₹1000 / 5-unit offer would make a draft or price-hidden product
        # searchable and purchasable at a hallucinated price.
        return product, offers

    def parse_woocommerce_product(
        self, raw: dict[str, Any]
    ) -> tuple[CanonicalProduct, list[CanonicalOffer]]:
        pid = f"prd_woo_{raw.get('id', 'item')}"
        title = raw.get("name", "WooCommerce Product")
        try:
            price_minor = parse_price_minor(raw.get("price") or raw.get("regular_price") or "0")
        except DomainError:
            price_minor = 0

        stock = parse_stock_quantity(
            raw.get("stock_quantity"),
            default_when_missing=10 if raw.get("in_stock", True) else 0,
        )

        cats = raw.get("categories", [])
        category = cats[0].get("name", "general").lower() if cats else "general"

        product = CanonicalProduct(
            product_id=pid,
            merchant_id=self.merchant_id,
            title=title,
            category=category,
            brand=raw.get("store_name", "WooCommerce Store"),
            description=raw.get("short_description") or raw.get("description") or "",
            attributes={
                attr.get("name", "spec"): ", ".join(attr.get("options", []))
                for attr in raw.get("attributes", [])
            },
            image_url=(raw.get("images") or [{}])[0].get("src"),
        )

        offer = CanonicalOffer(
            offer_id=f"ofr_woo_{raw.get('id', 'item')}",
            product_id=pid,
            merchant_id=self.merchant_id,
            unit_price_minor=price_minor,
            currency=raw.get("currency", "INR"),
            available_stock=max(0, stock),
            delivery_days=2,
            return_period_days=14,
        )

        return product, [offer]

    def _fetch_live_shopify(self, limit: int = 100) -> list[dict[str, Any]]:
        domain = self.config.get("store_domain") or self.store_domain
        token = self.config.get("access_token") or self.access_token
        if not domain or not token or "myshopify.com" not in domain:
            return []
        try:
            url = f"https://{domain}/admin/api/2024-01/products.json"
            headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
            with httpx.Client(timeout=8.0) as client:
                res = client.get(url, headers=headers, params={"limit": min(limit, 250)})
                if res.status_code == 200:
                    return res.json().get("products", [])
        except Exception:
            logger.warning("shopify_live_fetch_failed", extra={"domain": domain}, exc_info=True)
        return []

    def _fetch_live_woocommerce(self, limit: int = 100) -> list[dict[str, Any]]:
        domain = self.config.get("store_domain") or self.store_domain
        token = self.config.get("access_token") or self.access_token
        if not domain or not token:
            return []
        try:
            url = f"https://{domain}/wp-json/wc/v3/products"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            with httpx.Client(timeout=8.0) as client:
                res = client.get(url, headers=headers, params={"per_page": min(limit, 100)})
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        return data
        except Exception:
            logger.warning("woocommerce_live_fetch_failed", extra={"domain": domain}, exc_info=True)
        return []

    def fetch_products(self, limit: int = 100) -> list[CanonicalProduct]:
        # Try live platform API first if configured
        raw_items: list[dict[str, Any]] = []
        if self.flavor == "shopify":
            raw_items = self._fetch_live_shopify(limit)
        else:
            raw_items = self._fetch_live_woocommerce(limit)

        # Fallback to configured mock_items if offline / mock mode
        if not raw_items:
            raw_items = self.config.get("mock_items", [])

        products: list[CanonicalProduct] = []
        for item in raw_items[:limit]:
            if self.flavor == "shopify":
                p, _ = self.parse_shopify_product(item)
            else:
                p, _ = self.parse_woocommerce_product(item)
            products.append(p)
        return products

    def fetch_offers(self, limit: int = 100) -> list[CanonicalOffer]:
        raw_items: list[dict[str, Any]] = []
        if self.flavor == "shopify":
            raw_items = self._fetch_live_shopify(limit)
        else:
            raw_items = self._fetch_live_woocommerce(limit)

        if not raw_items:
            raw_items = self.config.get("mock_items", [])

        all_offers: list[CanonicalOffer] = []
        for item in raw_items[:limit]:
            if self.flavor == "shopify":
                _, ofrs = self.parse_shopify_product(item)
            else:
                _, ofrs = self.parse_woocommerce_product(item)
            all_offers.extend(ofrs)
        return all_offers

    def sync_catalog(self) -> SyncResult:
        start = time.perf_counter()
        products = self.fetch_products()
        offers = self.fetch_offers()
        duration = (time.perf_counter() - start) * 1000.0

        return SyncResult(
            platform_type=self.platform_type,
            merchant_id=self.merchant_id,
            products_imported=len(products),
            offers_updated=len(offers),
            duration_ms=round(duration, 2),
            status="success",
        )

    def push_order(self, order_id: str, checkout_payload: dict[str, Any]) -> dict[str, Any]:
        domain = self.config.get("store_domain") or self.store_domain
        token = self.config.get("access_token") or self.access_token

        if domain and token and "myshopify.com" in domain and self.flavor == "shopify":
            try:
                url = f"https://{domain}/admin/api/2024-01/orders.json"
                headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
                order_payload = {
                    "order": {
                        "financial_status": "paid",
                        "note": f"AgentPay Verified Autonomous Order {order_id}",
                        "tags": "AgentPay, AutonomousBuyer",
                        "total_price": f"{checkout_payload.get('amount_minor', 0) / 100:.2f}",
                    }
                }
                with httpx.Client(timeout=8.0) as client:
                    res = client.post(url, headers=headers, json=order_payload)
                    if res.status_code in (200, 201):
                        ext_id = res.json().get("order", {}).get("id", order_id)
                        return {
                            "status": "created",
                            "order_id": order_id,
                            "merchant_id": self.merchant_id,
                            "platform_type": self.platform_type,
                            "external_order_id": str(ext_id),
                            "message": f"Order successfully registered in live Shopify store ({domain}).",
                        }
            except Exception:
                logger.warning(
                    "shopify_order_push_failed", extra={"order_id": order_id}, exc_info=True
                )
                return {
                    "status": "failed",
                    "order_id": order_id,
                    "merchant_id": self.merchant_id,
                    "platform_type": self.platform_type,
                    "external_order_id": None,
                    "message": f"Failed to push order to {self.flavor.title()} store. Will retry.",
                }

        return {
            "status": "queued",
            "order_id": order_id,
            "merchant_id": self.merchant_id,
            "platform_type": self.platform_type,
            "external_order_id": None,
            "message": f"Order queued for {self.flavor.title()} store (no live store credentials configured).",
        }
