"""Connector registry managing tenant-isolated platform connections."""

from __future__ import annotations

from typing import Any

from services.connectors.base import PlatformConnector, SyncResult
from services.connectors.internal import InternalSeedConnector


class ConnectorRegistry:
    """Registry coordinating external e-commerce platform connectors per merchant tenant."""

    def __init__(self) -> None:
        self._connectors: dict[str, PlatformConnector] = {}
        # Pre-register default demo seed merchant
        default_seed = InternalSeedConnector("mer_demo_seed")
        self._connectors["mer_demo_seed"] = default_seed
        self._connectors["default"] = default_seed

    def register(self, merchant_id: str, connector: PlatformConnector) -> None:
        """Register or update a merchant's platform connector."""
        self._connectors[merchant_id] = connector

    def get(self, merchant_id: str | None = None) -> PlatformConnector:
        """Retrieve connector for merchant or fallback to default seed."""
        if not merchant_id or merchant_id not in self._connectors:
            return self._connectors.get("default", InternalSeedConnector("mer_demo_seed"))
        return self._connectors[merchant_id]

    def list_connectors(self) -> list[dict[str, Any]]:
        """List all active platform connections."""
        results = []
        for mid, conn in self._connectors.items():
            if mid == "default":
                continue
            results.append(
                {
                    "merchant_id": mid,
                    "platform_type": conn.platform_type,
                    "config": {
                        k: v
                        for k, v in conn.config.items()
                        if "key" not in k.lower() and "token" not in k.lower()
                    },
                    "policies": conn.get_policies(),
                }
            )
        return results

    def sync_merchant(self, merchant_id: str) -> SyncResult:
        """Trigger sync for a specific merchant platform."""
        connector = self.get(merchant_id)
        return connector.sync_catalog()


# Global Singleton Registry
GLOBAL_CONNECTOR_REGISTRY = ConnectorRegistry()
