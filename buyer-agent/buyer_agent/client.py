"""Independent AgentPay API Client for external autonomous buyers (Task 26, Requirement 20)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class ClientResponse:
    status_code: int
    data: dict[str, Any]
    is_success: bool


class AgentPayClient:
    """HTTP Client communicating strictly over the public AgentPay REST API."""

    def __init__(
        self, base_url: str = "http://localhost:8000", client: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(base_url=self.base_url, timeout=30.0)
        self._token: str | None = None

    def set_token(self, token: str) -> None:
        self._token = token

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def get_capabilities(self) -> ClientResponse:
        """Fetch the public machine-readable capability document."""
        res = self._client.get("/.well-known/agent-capability.json")
        if res.status_code == 404:
            res = self._client.get("/.well-known/agent-commerce")
        if res.status_code == 404:
            res = self._client.get("/api/v1/capability")
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def authenticate(self, api_key: str) -> ClientResponse:
        """Exchange buyer API key for short-lived scoped bearer token."""
        res = self._client.post(
            "/api/v1/auth/tokens",
            json={"api_key": api_key, "grant_type": "api_key"},
            headers=self._headers(),
        )
        if res.status_code == 404:
            res = self._client.post(
                "/api/v1/agent/auth/token",
                json={
                    "api_key": api_key,
                    "scopes": ["catalog:read", "checkout:write", "payment:write"],
                },
                headers=self._headers(),
            )
        data = res.json() if res.content else {}
        if res.is_success and "data" in data and "access_token" in data["data"]:
            self.set_token(data["data"]["access_token"])
        elif res.is_success and "access_token" in data:
            self.set_token(data["access_token"])
        return ClientResponse(status_code=res.status_code, data=data, is_success=res.is_success)

    def search_offers(
        self,
        *,
        category: str | None = None,
        max_price_minor: int | None = None,
        min_memory_gb: int | None = None,
        min_storage_gb: int | None = None,
        max_delivery_days: int | None = None,
        limit: int = 10,
    ) -> ClientResponse:
        """Query offers meeting structured criteria."""
        payload = {
            "category": category,
            "max_price_minor": max_price_minor,
            "min_memory_gb": min_memory_gb,
            "min_storage_gb": min_storage_gb,
            "max_delivery_days": max_delivery_days,
            "limit": limit,
        }
        res = self._client.post(
            "/api/v1/agent/search",
            json=payload,
            headers=self._headers(),
        )
        if res.status_code == 404:
            res = self._client.post(
                "/api/v1/agent/offers/query",
                json=payload,
                headers=self._headers(),
            )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def create_checkout(
        self,
        *,
        offer_id: str,
        quantity: int = 1,
        ttl_minutes: int = 15,
    ) -> ClientResponse:
        """Initiate checkout with inventory hold and price freeze."""
        payload = {
            "offer_id": offer_id,
            "quantity": quantity,
            "ttl_minutes": ttl_minutes,
        }
        res = self._client.post(
            "/api/v1/agent/checkout",
            json=payload,
            headers=self._headers(),
        )
        if res.status_code == 404:
            res = self._client.post(
                "/api/v1/agent/checkouts",
                json=payload,
                headers=self._headers(),
            )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def request_authorization(
        self,
        *,
        checkout_id: str,
        ttl_minutes: int = 15,
    ) -> ClientResponse:
        """Request human authorization bound to price hash."""
        payload = {
            "checkout_id": checkout_id,
            "ttl_minutes": ttl_minutes,
        }
        res = self._client.post(
            "/api/v1/agent/authorization",
            json=payload,
            headers=self._headers(),
        )
        if res.status_code == 404:
            res = self._client.post(
                "/api/v1/agent/authorizations",
                json=payload,
                headers=self._headers(),
            )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def create_payment(
        self,
        *,
        checkout_id: str,
        authorization_id: str,
        idempotency_key: str | None = None,
    ) -> ClientResponse:
        """Initiate payment with provider."""
        payload = {
            "checkout_id": checkout_id,
            "authorization_id": authorization_id,
        }
        res = self._client.post(
            "/api/v1/agent/payments",
            json=payload,
            headers=self._headers(idempotency_key=idempotency_key),
        )
        if res.status_code == 404:
            res = self._client.post(
                "/api/v1/agent/payment",
                json=payload,
                headers=self._headers(idempotency_key=idempotency_key),
            )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def get_payment_status(self, payment_id: str) -> ClientResponse:
        """Fetch status of payment attempt."""
        res = self._client.get(
            f"/api/v1/agent/payments/{payment_id}",
            headers=self._headers(),
        )
        if res.status_code == 404:
            res = self._client.get(
                f"/api/v1/payments/{payment_id}",
                headers=self._headers(),
            )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def negotiate_offer(
        self,
        *,
        offer_id: str,
        proposed_price_minor: int,
        round_number: int = 1,
    ) -> ClientResponse:
        """Negotiate price for an offer within policy bounds."""
        res = self._client.post(
            f"/api/v1/agent/offers/{offer_id}/negotiate",
            json={"proposed_price_minor": proposed_price_minor, "round": round_number},
            headers=self._headers(),
        )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )

    def get_order(self, order_id: str) -> ClientResponse:
        """Fetch confirmed order."""
        res = self._client.get(
            f"/api/v1/agent/orders/{order_id}",
            headers=self._headers(),
        )
        if res.status_code == 404:
            res = self._client.get(
                f"/api/v1/orders/{order_id}",
                headers=self._headers(),
            )
        return ClientResponse(
            status_code=res.status_code,
            data=res.json() if res.content else {},
            is_success=res.is_success,
        )
