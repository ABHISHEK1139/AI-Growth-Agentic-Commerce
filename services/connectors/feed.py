"""Catalog Feed connector for CSV, JSONL, and merchant product feeds."""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from services.connectors.base import (
    CanonicalOffer,
    CanonicalProduct,
    PlatformConnector,
    SyncResult,
)


class CatalogFeedConnector(PlatformConnector):
    """Parses flat file feeds (CSV/JSONL) and generates canonical products & offers."""

    def __init__(
        self,
        merchant_id: str,
        feed_content: str = "",
        feed_format: str = "csv",
        config: dict[str, Any] | None = None,
    ):
        merged = dict(config or {})
        merged["feed_format"] = feed_format
        super().__init__(merchant_id, "catalog_feed", merged)
        self.feed_content = feed_content
        self.feed_format = feed_format.lower()

    def parse_csv_feed(self, content: str) -> tuple[list[CanonicalProduct], list[CanonicalOffer]]:
        products: list[CanonicalProduct] = []
        offers: list[CanonicalOffer] = []

        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            pid = (
                row.get("id")
                or row.get("sku")
                or row.get("product_id")
                or f"prd_feed_{len(products) + 1}"
            )
            if not pid.startswith("prd_"):
                pid = f"prd_{pid}"

            title = row.get("title") or row.get("name") or "Feed Product"
            category = (row.get("category") or "general").lower()
            brand = row.get("brand") or "Generic"
            description = row.get("description") or ""

            # Minor units only, same contract as the generic REST connector: the
            # "<10000 means major units" guess overcharged cheap items 100×.
            try:
                price_minor = int(row.get("price_minor") or 0)
                if price_minor < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise DomainError(
                    "Catalog feed rows must supply a non-negative integer "
                    "'price_minor' in minor units.",
                    code=ErrorCode.VALIDATION_ERROR,
                ) from None

            try:
                stock = int(row.get("stock") or row.get("quantity") or 10)
            except ValueError:
                stock = 10

            products.append(
                CanonicalProduct(
                    product_id=pid,
                    merchant_id=self.merchant_id,
                    title=title,
                    category=category,
                    brand=brand,
                    description=description,
                    attributes={
                        k: v
                        for k, v in row.items()
                        if k not in {"id", "sku", "title", "price", "stock"}
                    },
                )
            )

            offers.append(
                CanonicalOffer(
                    offer_id=f"ofr_{pid[4:]}",
                    product_id=pid,
                    merchant_id=self.merchant_id,
                    unit_price_minor=price_minor,
                    currency="INR",
                    available_stock=stock,
                )
            )

        return products, offers

    def parse_jsonl_feed(self, content: str) -> tuple[list[CanonicalProduct], list[CanonicalOffer]]:
        products: list[CanonicalProduct] = []
        offers: list[CanonicalOffer] = []

        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            pid = str(raw.get("product_id") or raw.get("id") or f"prd_feed_{len(products) + 1}")
            if not pid.startswith("prd_"):
                pid = f"prd_{pid}"

            products.append(
                CanonicalProduct(
                    product_id=pid,
                    merchant_id=self.merchant_id,
                    title=str(raw.get("title") or "JSONL Item"),
                    category=str(raw.get("category") or "general").lower(),
                    brand=str(raw.get("brand") or "Generic"),
                    description=str(raw.get("description") or ""),
                    attributes=raw.get("attributes") or {},
                )
            )

            price_minor = int(raw.get("price_minor") or (float(raw.get("price", 0)) * 100))
            offers.append(
                CanonicalOffer(
                    offer_id=f"ofr_{pid[4:]}",
                    product_id=pid,
                    merchant_id=self.merchant_id,
                    unit_price_minor=price_minor,
                    currency=str(raw.get("currency") or "INR"),
                    available_stock=int(raw.get("stock") or 10),
                )
            )

        return products, offers

    def fetch_products(self, limit: int = 100) -> list[CanonicalProduct]:
        if self.feed_format == "jsonl":
            prods, _ = self.parse_jsonl_feed(self.feed_content)
        else:
            prods, _ = self.parse_csv_feed(self.feed_content)
        return prods[:limit]

    def fetch_offers(self, limit: int = 100) -> list[CanonicalOffer]:
        if self.feed_format == "jsonl":
            _, ofrs = self.parse_jsonl_feed(self.feed_content)
        else:
            _, ofrs = self.parse_csv_feed(self.feed_content)
        return ofrs[:limit]

    def sync_catalog(self) -> SyncResult:
        start = time.perf_counter()
        products = self.fetch_products()
        offers = self.fetch_offers()
        duration = (time.perf_counter() - start) * 1000.0

        return SyncResult(
            platform_type="catalog_feed",
            merchant_id=self.merchant_id,
            products_imported=len(products),
            offers_updated=len(offers),
            duration_ms=round(duration, 2),
            status="success",
        )

    def push_order(self, order_id: str, checkout_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "logged",
            "order_id": order_id,
            "merchant_id": self.merchant_id,
            "platform_type": "catalog_feed",
            "message": "Order appended to merchant feed dispatch ledger.",
        }
