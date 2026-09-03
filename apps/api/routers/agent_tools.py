"""MCP-style machine-readable tool schemas for external autonomous buyers (Phase 6).

This module serves the public tool catalogue an external agent loads *once* on
boot to discover what it can ask for, in what shape, and with what scope.

The format is intentionally close to JSON-Schema 2020-12 + the OpenAI function-
calling dialect because that is the lowest common denominator every major agent
framework already speaks. We do not invent a new shape; the document is
declarative data the agent consumes, not a protocol we run.

The schema is the same :class:`Tool` spec the in-process agent loop already
publishes (see ``services/agent/tools.py``), so what an embedded agent can call
and what a remote agent can call are the same set. The /api/v1/agent/tools
endpoint is the public, versioned view of that set.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth import AppSettings

router = APIRouter(prefix="/api/v1/agent", tags=["agent-tools"])


class ToolParameter(BaseModel):
    """One JSON-Schema-shaped input parameter."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = Field(description="JSON-Schema type, e.g. 'string', 'integer', 'object'")
    required: bool = False
    description: str = ""
    enum: list[Any] | None = None
    minimum: int | None = None
    maximum: int | None = None


class Tool(BaseModel):
    """One callable surface exposed to external autonomous agents.

    The ``name`` field is the string an agent calls, e.g. ``"search_products"``.
    The ``scope`` field is the OAuth-style permission the agent must have on its
    bearer token before the server will accept a call. The ``parameters`` list is
    the full input contract; an agent that does not provide every required entry
    is rejected before the route runs.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    scope: str
    http_method: str = "POST"
    http_path: str
    idempotent: bool = False
    parameters: list[ToolParameter]


#: The public catalogue. Each entry maps a tool name to the HTTP surface an
#: external agent invokes. Keep this aligned with ``services/agent/tools.py``
#: and ``apps/api/routers/agent.py``; if you add a new agent tool there, add a
#: matching entry here. The "public contract" unit test enforces this.
TOOL_CATALOGUE: tuple[Tool, ...] = (
    Tool(
        name="search_products",
        description=(
            "Search the merchant catalog for products matching the buyer's intent. "
            "Returns offers with current price, currency, and inventory."
        ),
        scope="catalog:read",
        http_method="POST",
        http_path="/api/v1/agent/search",
        idempotent=True,
        parameters=[
            ToolParameter(name="category", type="string", required=False),
            ToolParameter(name="max_price_minor", type="integer", required=False, minimum=0),
            ToolParameter(name="min_memory_gb", type="integer", required=False, minimum=0),
            ToolParameter(name="min_storage_gb", type="integer", required=False, minimum=0),
            ToolParameter(name="max_delivery_days", type="integer", required=False, minimum=0),
            ToolParameter(name="limit", type="integer", required=False, minimum=1, maximum=50),
        ],
    ),
    Tool(
        name="get_product",
        description="Fetch a single product by id, including current offers and price.",
        scope="catalog:read",
        http_method="GET",
        http_path="/api/v1/agent/products/{product_id}",
        idempotent=True,
        parameters=[ToolParameter(name="product_id", type="string", required=True)],
    ),
    Tool(
        name="get_active_offers",
        description="List current active offers for a product, including any campaign price.",
        scope="catalog:read",
        http_method="GET",
        http_path="/api/v1/agent/products/{product_id}/offers",
        idempotent=True,
        parameters=[ToolParameter(name="product_id", type="string", required=True)],
    ),
    Tool(
        name="check_inventory",
        description="Confirm current stock for a product at the catalog level.",
        scope="catalog:read",
        http_method="GET",
        http_path="/api/v1/agent/products/{product_id}/inventory",
        idempotent=True,
        parameters=[ToolParameter(name="product_id", type="string", required=True)],
    ),
    Tool(
        name="get_delivery_options",
        description="Return available delivery windows for a product with prices.",
        scope="catalog:read",
        http_method="GET",
        http_path="/api/v1/agent/products/{product_id}/delivery",
        idempotent=True,
        parameters=[ToolParameter(name="product_id", type="string", required=True)],
    ),
    Tool(
        name="get_return_policy",
        description="Return the merchant's return policy for a product.",
        scope="catalog:read",
        http_method="GET",
        http_path="/api/v1/agent/products/{product_id}/returns",
        idempotent=True,
        parameters=[ToolParameter(name="product_id", type="string", required=True)],
    ),
    Tool(
        name="compare_offers",
        description="Compare two offers side-by-side on price, delivery, and warranty.",
        scope="catalog:read",
        http_method="POST",
        http_path="/api/v1/agent/offers/compare",
        idempotent=True,
        parameters=[
            ToolParameter(name="offer_a", type="string", required=True),
            ToolParameter(name="offer_b", type="string", required=True),
        ],
    ),
    Tool(
        name="create_checkout",
        description=(
            "Open a server-side checkout for a chosen offer. Returns a frozen "
            "price snapshot and a checkout id. Always supply an Idempotency-Key."
        ),
        scope="checkout:write",
        http_method="POST",
        http_path="/api/v1/agent/checkout",
        idempotent=True,
        parameters=[
            ToolParameter(name="offer_id", type="string", required=True),
            ToolParameter(name="quantity", type="integer", required=False, minimum=1, maximum=10),
            ToolParameter(
                name="ttl_minutes", type="integer", required=False, minimum=1, maximum=1440
            ),
        ],
    ),
    Tool(
        name="request_authorization",
        description=(
            "Ask the gateway to evaluate a checkout against the buyer's policy. "
            "Returns an authorization id; the agent MUST wait for the human to "
            "approve before creating a payment."
        ),
        scope="checkout:write",
        http_method="POST",
        http_path="/api/v1/agent/authorization",
        idempotent=True,
        parameters=[ToolParameter(name="checkout_id", type="string", required=True)],
    ),
    Tool(
        name="create_payment",
        description=(
            "Create a payment against an authorized checkout. The provider returns "
            "a hosted checkout URL the buyer is redirected to. Always supply an "
            "Idempotency-Key."
        ),
        scope="payment:write",
        http_method="POST",
        http_path="/api/v1/agent/payments",
        idempotent=True,
        parameters=[
            ToolParameter(name="checkout_id", type="string", required=True),
            ToolParameter(name="authorization_id", type="string", required=True),
        ],
    ),
    Tool(
        name="get_payment_status",
        description="Fetch the latest status of a payment by its internal id.",
        scope="payment:write",
        http_method="GET",
        http_path="/api/v1/agent/payments/{payment_id}",
        idempotent=True,
        parameters=[ToolParameter(name="payment_id", type="string", required=True)],
    ),
    Tool(
        name="get_order",
        description="Fetch an order by id. Returns the order line items and current state.",
        scope="payment:write",
        http_method="GET",
        http_path="/api/v1/agent/orders/{order_id}",
        idempotent=True,
        parameters=[ToolParameter(name="order_id", type="string", required=True)],
    ),
)


@router.get("/tools", summary="List tools exposed to external autonomous agents")
@router.get("/tools.json", include_in_schema=False)
def list_agent_tools(_settings: AppSettings) -> dict[str, Any]:
    """Return the machine-readable tool catalogue.

    The shape is a flat list under ``tools``. Each entry's ``http_path`` is
    relative to the gateway base URL. The agent is expected to call this on
    boot and on every policy refresh; the response is small enough to be cached
    for an hour without staleness risk.
    """
    return {
        "schema_version": "1.0",
        "tool_count": len(TOOL_CATALOGUE),
        "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in TOOL_CATALOGUE],
    }


@router.get(
    "/tools/{tool_name}",
    summary="Fetch a single tool's schema by name",
)
def get_agent_tool(tool_name: str, _settings: AppSettings) -> dict[str, Any]:
    """Return a single tool's schema, or 404 if not in the catalogue.

    The 404 distinguishes "tool does not exist" from "gateway down", which an
    agent can act on: a missing tool is a permanent configuration issue, a
    503 is a transient retry target.
    """
    from packages.errors.exceptions import DomainError
    from packages.errors.registry import ErrorCode

    for tool in TOOL_CATALOGUE:
        if tool.name == tool_name:
            return tool.model_dump(mode="json", exclude_none=True)
    raise DomainError(
        f"Tool '{tool_name}' is not in the agent catalogue.", code=ErrorCode.NOT_FOUND
    )
