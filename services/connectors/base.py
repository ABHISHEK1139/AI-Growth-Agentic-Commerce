"""Base abstract definitions for external e-commerce platform connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

PlatformType = Literal["internal_seed", "shopify", "woocommerce", "generic_rest", "catalog_feed"]


@dataclass(frozen=True, slots=True)
class CanonicalProduct:
    product_id: str
    merchant_id: str
    title: str
    category: str
    brand: str
    description: str
    attributes: dict[str, Any] = field(default_factory=dict)
    image_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CanonicalOffer:
    offer_id: str
    product_id: str
    merchant_id: str
    unit_price_minor: int
    currency: str
    available_stock: int
    delivery_days: int = 2
    return_period_days: int = 14
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class SyncResult:
    platform_type: PlatformType
    merchant_id: str
    products_imported: int
    offers_updated: int
    duration_ms: float
    status: str = "success"
    errors: list[str] = field(default_factory=list)


class PlatformConnector(ABC):
    """Abstract connector interfacing external e-commerce platforms to AgentPay."""

    def __init__(
        self, merchant_id: str, platform_type: PlatformType, config: dict[str, Any] | None = None
    ):
        self.merchant_id = merchant_id
        self.platform_type = platform_type
        self.config = config or {}

    @abstractmethod
    def fetch_products(self, limit: int = 100) -> list[CanonicalProduct]:
        """Fetch and convert external platform products into AgentPay CanonicalProduct."""

    @abstractmethod
    def fetch_offers(self, limit: int = 100) -> list[CanonicalOffer]:
        """Fetch and convert external platform prices and inventory into CanonicalOffer."""

    @abstractmethod
    def sync_catalog(self) -> SyncResult:
        """Execute full or incremental synchronization of products and offers."""

    @abstractmethod
    def push_order(self, order_id: str, checkout_payload: dict[str, Any]) -> dict[str, Any]:
        """Notify external merchant platform of a confirmed AgentPay AI-agent order."""

    def get_policies(self) -> dict[str, Any]:
        """Return return, shipping, and cancellation policies."""
        return {
            "return_period_days": self.config.get("return_period_days", 14),
            "shipping_sla_days": self.config.get("shipping_sla_days", 2),
            "max_order_amount_minor": self.config.get("max_order_amount_minor", 20000000),
            "currency": self.config.get("currency", "INR"),
        }
