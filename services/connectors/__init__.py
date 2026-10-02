"""Platform-agnostic E-Commerce Connector Architecture exports."""

from services.connectors.base import (
    CanonicalOffer,
    CanonicalProduct,
    PlatformConnector,
    PlatformType,
    SyncResult,
)
from services.connectors.ecommerce_platform import ShopifyWooConnector
from services.connectors.feed import CatalogFeedConnector
from services.connectors.generic_rest import GenericRestConnector
from services.connectors.internal import InternalSeedConnector
from services.connectors.models import (
    ChannelConnection,
    ChannelOrderPush,
    ChannelSyncRun,
    OperatorAccount,
    decrypt_secret,
    encrypt_secret,
)
from services.connectors.registry import GLOBAL_CONNECTOR_REGISTRY, ConnectorRegistry

__all__ = [
    "PlatformConnector",
    "PlatformType",
    "CanonicalProduct",
    "CanonicalOffer",
    "SyncResult",
    "InternalSeedConnector",
    "GenericRestConnector",
    "ShopifyWooConnector",
    "CatalogFeedConnector",
    "ConnectorRegistry",
    "GLOBAL_CONNECTOR_REGISTRY",
    "OperatorAccount",
    "ChannelConnection",
    "ChannelSyncRun",
    "ChannelOrderPush",
    "encrypt_secret",
    "decrypt_secret",
]
