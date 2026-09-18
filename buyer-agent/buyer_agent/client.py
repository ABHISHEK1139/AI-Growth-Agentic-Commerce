"""Independent AgentPay API Client for external autonomous buyers (Task 26, Requirement 20).

All paths are the canonical public agent surface. The server also registers a
few legacy aliases, but this client never probes for them: a 404 means the
route moved and must fail loudly rather than silently hitting a different
handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

#: Canonical capability document (also served at legacy aliases, unused here).
CAPABILITY_PATH = "/.well-known/agent-commerce"
#: Canonical token exchange (``TokenExchangeRequest``: ``api_key`` + optional ``scopes``).
TOKEN_PATH = "/api/v1/agent/auth/token"  # noqa: S105 - URL path, not a credential
#: Canonical agent commerce paths (see ``apps/api/routers/agent.py``).
SEARCH_PATH = "/api/v1/agent/search"
CHECKOUT_PATH = "/api/v1/agent/checkout"
AUTHORIZATION_PATH = "/api/v1/agent/authorization"
PAYMENTS_PATH = "/api/v1/agent/payments"


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

    def _wrap(self, res: httpx.Response) -> ClientResponse:
        try:
            data = res.json() if res.content else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {"data": data}
        # The gateway uses ``ok: true/false`` envelopes. Three domain codes
        # are "in-band" (HTTP 200 with an error envelope), so HTTP status
        # alone cannot decide success: any ``error`` body or ``ok: false``
        # is a failure even at 200.
        is_success = bool(res.is_success)
        if isinstance(data, dict) and ("error" in data or data.get("ok") is False):
            is_success = False
        return ClientResponse(
            status_code=res.status_code,
            data=data,
            is_success=is_success,
        )

    def get_capabilities(self) -> ClientResponse:
        """Fetch the public machine-readable capability document."""
        return self._wrap(self._client.get(CAPABILITY_PATH))

    def authenticate(self, api_key: str, scopes: list[str] | None = None) -> ClientResponse:
        """Exchange buyer API key for short-lived scoped bearer token."""
        payload: dict[str, Any] = {"api_key": api_key}
        if scopes is not None:
            payload["scopes"] = scopes
        else:
            payload["scopes"] = ["catalog:read", "checkout:write", "payment:write"]
        res = self._client.post(
            TOKEN_PATH,
            json=payload,
            headers=self._headers(),
        )
        wrapped = self._wrap(res)
        data = wrapped.data
        if wrapped.is_success and "data" in data and "access_token" in data["data"]:
            self.set_token(data["data"]["access_token"])
        elif wrapped.is_success and "access_token" in data:
            self.set_token(data["access_token"])
        return wrapped

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
        return self._wrap(
            self._client.post(
                SEARCH_PATH,
                json=payload,
                headers=self._headers(),
            )
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
        return self._wrap(
            self._client.post(
                CHECKOUT_PATH,
                json=payload,
                headers=self._headers(),
            )
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
        return self._wrap(
            self._client.post(
                AUTHORIZATION_PATH,
                json=payload,
                headers=self._headers(),
            )
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
        return self._wrap(
            self._client.post(
                PAYMENTS_PATH,
                json=payload,
                headers=self._headers(idempotency_key=idempotency_key),
            )
        )

    def get_payment_status(self, payment_id: str) -> ClientResponse:
        """Fetch status of payment attempt."""
        return self._wrap(
            self._client.get(
                f"{PAYMENTS_PATH}/{payment_id}",
                headers=self._headers(),
            )
        )

    def negotiate_offer(
        self,
        *,
        offer_id: str,
        proposed_price_minor: int,
        round_number: int = 1,
    ) -> ClientResponse:
        """Negotiate price for an offer within policy bounds."""
        return self._wrap(
            self._client.post(
                f"/api/v1/agent/offers/{offer_id}/negotiate",
                json={"proposed_price_minor": proposed_price_minor, "round": round_number},
                headers=self._headers(),
            )
        )

    def get_order(self, order_id: str) -> ClientResponse:
        """Fetch confirmed order."""
        return self._wrap(
            self._client.get(
                f"/api/v1/agent/orders/{order_id}",
                headers=self._headers(),
            )
        )
