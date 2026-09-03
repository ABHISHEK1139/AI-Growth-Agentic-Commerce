"""E-Commerce Platform Connectors & Ingestion Router."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.auth import require_roles
from packages.security.principals import Principal, Role
from services.connectors.base import PlatformConnector
from services.connectors.ecommerce_platform import ShopifyWooConnector
from services.connectors.feed import CatalogFeedConnector
from services.connectors.generic_rest import GenericRestConnector
from services.connectors.internal import InternalSeedConnector
from services.connectors.registry import GLOBAL_CONNECTOR_REGISTRY

router = APIRouter(prefix="/api/v1/connectors", tags=["ecommerce-connectors"])
MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN)),
]


class RegisterConnectorRequest(BaseModel):
    merchant_id: str = Field(..., description="Merchant tenant ID")
    platform_type: str = Field(
        ..., description="shopify, woocommerce, generic_rest, catalog_feed, or internal_seed"
    )
    store_url: str | None = Field(default=None, description="Platform API base URL or store domain")
    api_key: str | None = Field(default=None, description="API Key or Access Token")
    feed_content: str | None = Field(
        default=None, description="CSV or JSONL content for catalog_feed"
    )
    config: dict[str, Any] | None = Field(default=None, description="Optional extra settings")


class SyncRequest(BaseModel):
    merchant_id: str = Field(default="mer_demo_seed", description="Merchant to sync")


class PlatformWebhookRequest(BaseModel):
    merchant_id: str
    event: str = Field(..., description="e.g. inventory.updated, product.created, price.changed")
    payload: dict[str, Any]


@router.get("/status")
def get_connectors_status(principal: MerchantPrincipal) -> dict[str, Any]:
    """List all registered e-commerce platform connectors and active sync states."""
    active = GLOBAL_CONNECTOR_REGISTRY.list_connectors()
    return {
        "ok": True,
        "total_active_connectors": len(active),
        "connectors": active,
        "supported_platforms": [
            {
                "id": "shopify",
                "name": "Shopify Storefront & Admin API",
                "status": "ready",
                "features": ["Products", "Variants", "Inventory", "Orders", "Webhooks"],
            },
            {
                "id": "woocommerce",
                "name": "WooCommerce REST API v3",
                "status": "ready",
                "features": ["Products", "Stock Quantities", "Categories", "Orders"],
            },
            {
                "id": "generic_rest",
                "name": "Custom Merchant REST API",
                "status": "ready",
                "features": ["Standard JSON Mapping", "Periodic Polling", "Webhook Callbacks"],
            },
            {
                "id": "catalog_feed",
                "name": "Flat File Catalog Feed",
                "status": "ready",
                "features": ["CSV", "JSONL", "Google Merchant Center XML"],
            },
            {
                "id": "internal_seed",
                "name": "AgentPay Demo Seed Store",
                "status": "active",
                "features": ["High-Speed In-Memory", "Pre-built Tech Catalog", "Razorpay Sandbox"],
            },
        ],
    }


@router.post("/register")
def register_connector(
    req: RegisterConnectorRequest,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Register a new e-commerce store connector."""
    target_principal = principal.acting_on(req.merchant_id)
    effective_merchant_id = target_principal.merchant_id or req.merchant_id

    ptype = req.platform_type.lower()

    conn: PlatformConnector
    if ptype == "shopify":
        conn = ShopifyWooConnector(
            merchant_id=effective_merchant_id,
            platform_flavor="shopify",
            store_domain=req.store_url or "mystore.myshopify.com",
            access_token=req.api_key,
            config=req.config,
        )
    elif ptype == "woocommerce":
        conn = ShopifyWooConnector(
            merchant_id=effective_merchant_id,
            platform_flavor="woocommerce",
            store_domain=req.store_url or "https://store.example.com",
            access_token=req.api_key,
            config=req.config,
        )
    elif ptype == "generic_rest":
        conn = GenericRestConnector(
            merchant_id=effective_merchant_id,
            base_url=req.store_url or "https://api.example.com",
            api_key=req.api_key,
            config=req.config,
        )
    elif ptype == "catalog_feed":
        conn = CatalogFeedConnector(
            merchant_id=effective_merchant_id,
            feed_content=req.feed_content or "",
            config=req.config,
        )
    else:
        conn = InternalSeedConnector(merchant_id=effective_merchant_id, config=req.config)

    GLOBAL_CONNECTOR_REGISTRY.register(effective_merchant_id, conn)

    # Trigger initial sync
    sync_res = conn.sync_catalog()

    return {
        "ok": True,
        "message": f"Successfully registered and synchronized {req.platform_type} connector for merchant {effective_merchant_id}",
        "sync_result": {
            "products_imported": sync_res.products_imported,
            "offers_updated": sync_res.offers_updated,
            "duration_ms": sync_res.duration_ms,
            "status": sync_res.status,
        },
    }


@router.post("/sync")
def trigger_sync(
    req: SyncRequest,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Trigger on-demand catalog and inventory synchronization."""
    target_principal = principal.acting_on(req.merchant_id)
    effective_merchant_id = target_principal.merchant_id or req.merchant_id

    sync_res = GLOBAL_CONNECTOR_REGISTRY.sync_merchant(effective_merchant_id)
    return {
        "ok": True,
        "merchant_id": effective_merchant_id,
        "platform_type": sync_res.platform_type,
        "products_imported": sync_res.products_imported,
        "offers_updated": sync_res.offers_updated,
        "duration_ms": sync_res.duration_ms,
        "status": sync_res.status,
    }


@router.post("/webhook")
def handle_platform_webhook(
    req: PlatformWebhookRequest,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Receive real-time catalog or inventory change events from external merchant stores."""
    target_principal = principal.acting_on(req.merchant_id)
    effective_merchant_id = target_principal.merchant_id or req.merchant_id

    conn = GLOBAL_CONNECTOR_REGISTRY.get(effective_merchant_id)
    return {
        "ok": True,
        "merchant_id": effective_merchant_id,
        "platform_type": conn.platform_type,
        "event_received": req.event,
        "action": "catalog_cache_invalidated",
    }
