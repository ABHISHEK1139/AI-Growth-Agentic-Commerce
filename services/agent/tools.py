"""Tool argument validation registry (Task 24, Requirement 21, 22.8-22.10)."""

from __future__ import annotations

from typing import Any

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import ToolArgumentsV1, ToolName

ALLOWLISTED_TOOLS: frozenset[ToolName] = frozenset(
    [
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
)


def validate_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> ToolArgumentsV1:
    """Validate model-selected tool name and arguments against strict closed schema."""
    # 1. Allowlist verification (Requirement 22.8, 22.9)
    if tool_name not in ALLOWLISTED_TOOLS:
        raise DomainError(
            f"The requested tool '{tool_name}' is not in the allowlist.",
            code=ErrorCode.TOOL_BLOCKED,
        )

    # 2. Strict type & closed payload validation
    defaults: dict[str, Any] = {
        "intent": None,
        "query": None,
        "product_id": None,
        "offer_id": None,
        "offer_ids": None,
        "checkout_id": None,
        "authorization_id": None,
        "payment_id": None,
        "quantity": None,
        "proposed_price_minor": None,
        "url": None,
        "confirmation_token": None,
    }
    allowed_keys = {
        "intent",
        "query",
        "product_id",
        "offer_id",
        "offer_ids",
        "checkout_id",
        "authorization_id",
        "payment_id",
        "quantity",
        "proposed_price_minor",
        "url",
        "confirmation_token",
    }
    filtered_args = {k: v for k, v in arguments.items() if k in allowed_keys}
    defaults.update(filtered_args)
    defaults["schema_version"] = "1.0"
    defaults["tool_name"] = tool_name

    try:
        validated = ToolArgumentsV1.model_validate(defaults)
    except Exception as exc:
        raise DomainError(
            f"Invalid arguments for tool '{tool_name}': {exc}",
            code=ErrorCode.VALIDATION_ERROR,
        ) from exc

    # 3. Tool-specific required parameter assertions
    if tool_name == "get_product" and not validated.product_id:
        raise DomainError("product_id is required for get_product", code=ErrorCode.VALIDATION_ERROR)
    if tool_name == "get_offer" and not validated.offer_id:
        raise DomainError("offer_id is required for get_offer", code=ErrorCode.VALIDATION_ERROR)
    if tool_name == "create_checkout" and not validated.offer_id:
        raise DomainError(
            "offer_id is required for create_checkout", code=ErrorCode.VALIDATION_ERROR
        )
    if tool_name == "request_authorization" and not validated.checkout_id:
        raise DomainError(
            "checkout_id is required for request_authorization", code=ErrorCode.VALIDATION_ERROR
        )
    if tool_name == "create_payment" and (
        not validated.checkout_id or not validated.authorization_id
    ):
        raise DomainError(
            "checkout_id and authorization_id are required for create_payment",
            code=ErrorCode.VALIDATION_ERROR,
        )
    if tool_name == "calculate" and not validated.expression:
        raise DomainError("expression is required for calculate", code=ErrorCode.VALIDATION_ERROR)

    # 4. Anti-SSRF URL validation on external network tools (Requirement 27.2)
    if tool_name in ("open_url", "extract_page"):
        if not validated.url:
            raise DomainError(f"url is required for {tool_name}", code=ErrorCode.VALIDATION_ERROR)
        from services.research.worker import is_safe_public_url

        if not is_safe_public_url(validated.url):
            raise DomainError(
                f"URL '{validated.url}' violates anti-SSRF policy: access to local, internal, or cloud metadata addresses is blocked.",
                code=ErrorCode.FORBIDDEN,
            )

    return validated
