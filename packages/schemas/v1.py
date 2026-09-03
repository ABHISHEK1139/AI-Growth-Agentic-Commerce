"""Version 1 public commerce schemas.

All models reject unknown fields and use provider-neutral names. Fields that are
optional in meaning are still required in the JSON shape and accept ``null``;
this is the form expected by strict OpenAI-compatible structured output.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]
CurrencyCode = Literal["INR", "USD"]
SchemaVersion = Literal["1.0"]


class StrictSchemaModel(BaseModel):
    """Base for immutable, strict, closed public contract models."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class IntentFinancialConstraintsV1(StrictSchemaModel):
    """Financial intent fields; unknown financial instructions are rejected."""

    budget_minor: NonNegativeInt | None
    currency: CurrencyCode | None


class IntentV1(StrictSchemaModel):
    schema_version: SchemaVersion
    query: str
    category: str | None
    financial: IntentFinancialConstraintsV1
    min_memory_gb: NonNegativeInt | None
    min_storage_gb: NonNegativeInt | None
    max_delivery_days: NonNegativeInt | None
    quantity: PositiveInt


class ProductSpecificationsV1(StrictSchemaModel):
    memory_gb: NonNegativeInt | None
    storage_gb: NonNegativeInt | None
    weight_grams: NonNegativeInt | None
    length_mm: NonNegativeInt | None
    width_mm: NonNegativeInt | None
    height_mm: NonNegativeInt | None

    @field_validator(
        "memory_gb",
        "storage_gb",
        "weight_grams",
        "length_mm",
        "width_mm",
        "height_mm",
        mode="before",
    )
    @classmethod
    def _coerce_integral_values(cls, value: object) -> object:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError("specification values must be whole numbers")
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return None
            try:
                parsed = float(stripped)
            except ValueError as exc:  # pragma: no cover - defensive branch
                raise TypeError("specification values must be integers or numeric strings") from exc
            if not parsed.is_integer():
                raise ValueError("specification values must be whole numbers")
            return int(parsed)
        return value


class OfferV1(StrictSchemaModel):
    schema_version: SchemaVersion
    offer_id: str
    product_id: str
    merchant_id: str
    status: Literal["active", "inactive", "expired", "needs_review"]
    unit_price_minor: NonNegativeInt
    currency: CurrencyCode
    available_quantity: NonNegativeInt
    delivery_days: NonNegativeInt
    return_period_days: NonNegativeInt
    expires_at: str
    offer_version: PositiveInt
    pricing_source: Literal[
        "synthetic_band_random",
        "merchant_configured",
        "amazon_reviews_2023_usd_fx_100",
    ]
    specifications: ProductSpecificationsV1


class PriceBreakdownV1(StrictSchemaModel):
    unit_price_minor: NonNegativeInt
    quantity: PositiveInt
    subtotal_minor: NonNegativeInt
    shipping_minor: NonNegativeInt
    tax_minor: NonNegativeInt
    discount_minor: NonNegativeInt
    total_minor: NonNegativeInt
    currency: CurrencyCode


class CheckoutV1(StrictSchemaModel):
    schema_version: SchemaVersion
    checkout_id: str
    buyer_id: str
    merchant_id: str
    offer_id: str
    offer_version: PositiveInt
    product_id: str
    status: Literal[
        "created",
        "policy_checked",
        "authorization_pending",
        "authorized",
        "cancelled",
        "expired",
        "price_changed",
    ]
    pricing: PriceBreakdownV1
    price_hash: str
    expires_at: str


class PolicyDecisionV1(StrictSchemaModel):
    decision: Literal["ALLOW", "REQUIRE_APPROVAL", "BLOCK"]
    reason_code: str
    policy_version: str


class AuthorizationV1(StrictSchemaModel):
    schema_version: SchemaVersion
    authorization_id: str
    buyer_id: str
    merchant_id: str
    checkout_id: str
    amount_ceiling_minor: NonNegativeInt
    currency: CurrencyCode
    category: str
    price_hash: str
    status: Literal["pending", "approved", "rejected", "revoked", "consumed", "expired"]
    valid_until: str
    policy: PolicyDecisionV1


class PaymentV1(StrictSchemaModel):
    schema_version: SchemaVersion
    payment_id: str
    checkout_id: str
    authorization_id: str
    provider: str
    provider_order_id: str | None
    provider_payment_id: str | None
    public_key: str | None
    amount_minor: NonNegativeInt
    currency: CurrencyCode
    status: Literal[
        "created",
        "pending",
        "verified",
        "failed",
        "timeout",
        "unknown",
        "manual_review",
    ]
    test_mode: bool


class OrderV1(StrictSchemaModel):
    schema_version: SchemaVersion
    order_id: str
    checkout_id: str
    payment_id: str
    buyer_id: str
    merchant_id: str
    amount_minor: NonNegativeInt
    currency: CurrencyCode
    status: Literal["confirmed", "completed", "cancelled"]
    confirmed_at: str


class CapabilityAuthenticationV1(StrictSchemaModel):
    method: Literal["api_key_exchange"]
    token_endpoint: str
    scopes: list[Literal["catalog:read", "checkout:write", "payment:write"]]


class CapabilityLimitsV1(StrictSchemaModel):
    max_results: PositiveInt
    max_quantity: PositiveInt
    max_transaction_minor: NonNegativeInt
    auto_approval_limit_minor: NonNegativeInt
    currency: CurrencyCode


class CapabilityEndpointsV1(StrictSchemaModel):
    search: str
    offers_query: str
    checkout: str
    authorization: str
    payment: str
    payment_status: str
    order: str


class CapabilityPolicySummaryV1(StrictSchemaModel):
    policy_version: str
    allowed_categories: list[str]
    blocked_categories: list[str]
    explicit_approval_required: bool


class CapabilityDocumentV1(StrictSchemaModel):
    schema_version: SchemaVersion
    authentication: CapabilityAuthenticationV1
    capabilities: list[
        Literal[
            "catalog_search",
            "offer_query",
            "checkout",
            "authorization",
            "payment",
            "payment_status",
            "order_lookup",
        ]
    ]
    limits: CapabilityLimitsV1
    endpoints: CapabilityEndpointsV1
    policy: CapabilityPolicySummaryV1
    payment_provider: str
    test_mode: bool
    external_protocol_certification: Literal["none"]
    protocol_notice: str


ToolName = Literal[
    "search_products",
    "get_product",
    "get_offer",
    "compare_offers",
    "check_inventory",
    "get_delivery_options",
    "get_return_policy",
    "search_web",
    "open_url",
    "extract_page",
    "create_checkout",
    "request_authorization",
    "create_payment",
    "check_payment",
]


class ToolArgumentsV1(StrictSchemaModel):
    """Closed transport shape for model-selected tool arguments.

    Tool-specific validation in the agent registry narrows which nullable fields
    are required for each tool. Keeping the union closed here prevents arbitrary
    instructions or monetary keys entering the deterministic service boundary.
    """

    schema_version: SchemaVersion
    tool_name: ToolName
    intent: IntentV1 | None
    query: str | None
    product_id: str | None
    offer_id: str | None
    offer_ids: list[str] | None
    checkout_id: str | None
    authorization_id: str | None
    payment_id: str | None
    quantity: PositiveInt | None
    proposed_price_minor: NonNegativeInt | None
    url: str | None
    confirmation_token: str | None
