"""Public agent surface endpoints for external autonomous buyers (Task 25, Requirement 20)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import AppSettings, current_principal, require_scopes
from apps.api.commerce import get_commerce_facade
from apps.api.db import get_db
from apps.api.envelope import success
from packages.commerce import CommerceFacade
from packages.errors.exceptions import ForbiddenError
from packages.security.principals import Principal, Scope
from services.agent.loop import AgentLoopRunner, ToolExecutionResult
from services.agent.model import ModelProvider, get_model_provider
from services.agent.tools import ALLOWLISTED_TOOLS, validate_tool_arguments
from services.authorization.service import AuthorizationService
from services.checkout.service import CheckoutService
from services.offers.models import Offer
from services.offers.service import OfferService
from services.orders.service import OrderService
from services.payments.service import PaymentService

__all__ = [
    "ALLOWLISTED_TOOLS",
    "TOOL_REQUIRED_SCOPES",
    "AgentAuthorizationRequest",
    "AgentCheckoutRequest",
    "AgentConverseRequest",
    "AgentPaymentRequest",
    "AgentSearchRequest",
    "AgentToolExecuteRequest",
    "execute_tool",
    "router",
    "validate_tool_arguments",
]

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])
DatabaseSession = Annotated[Session, Depends(get_db)]

CatalogAgent = Annotated[Principal, Depends(require_scopes(Scope.CATALOG_READ))]
CheckoutAgent = Annotated[Principal, Depends(require_scopes(Scope.CHECKOUT_WRITE))]
PaymentAgent = Annotated[Principal, Depends(require_scopes(Scope.PAYMENT_WRITE))]

TOOL_REQUIRED_SCOPES: dict[str, Scope] = {
    "create_checkout": Scope.CHECKOUT_WRITE,
    "create_payment": Scope.PAYMENT_WRITE,
    "request_authorization": Scope.CHECKOUT_WRITE,
    "search_products": Scope.CATALOG_READ,
    "get_product": Scope.CATALOG_READ,
    "get_product_details": Scope.CATALOG_READ,
    "get_offer": Scope.CATALOG_READ,
    "get_active_offers": Scope.CATALOG_READ,
    "compare_offers": Scope.CATALOG_READ,
    "check_inventory": Scope.CATALOG_READ,
    "get_delivery_options": Scope.CATALOG_READ,
    "get_return_policy": Scope.CATALOG_READ,
}


class AgentSearchRequest(BaseModel):
    category: str | None = None
    max_price_minor: int | None = Field(default=None, ge=0)
    min_memory_gb: int | None = Field(default=None, ge=0)
    min_storage_gb: int | None = Field(default=None, ge=0)
    max_delivery_days: int | None = Field(default=None, ge=0)
    limit: int = Field(default=10, ge=1, le=50)


class AgentCheckoutRequest(BaseModel):
    offer_id: str
    quantity: int = Field(default=1, ge=1)
    ttl_minutes: int = Field(default=15, ge=1, le=1440)


class AgentAuthorizationRequest(BaseModel):
    checkout_id: str
    ttl_minutes: int = Field(default=15, ge=1, le=1440)


class AgentPaymentRequest(BaseModel):
    checkout_id: str
    authorization_id: str


class AgentConverseRequest(BaseModel):
    prompt: str
    limit: int = Field(default=5, ge=1, le=20)


class AgentToolExecuteRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


def execute_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    merchant_id: str,
    buyer_id: str | None = None,
    confirmed: bool = False,
    facade: CommerceFacade | None = None,
    model_provider: ModelProvider | None = None,
) -> ToolExecutionResult:
    """Execute a single bounded, allowlisted agent tool through the Commerce Facade (Requirement 22, 23)."""
    commerce = facade or get_commerce_facade()
    runner = AgentLoopRunner(commerce=commerce, model_provider=model_provider)
    return runner.execute_tool(
        tool_name=tool_name,
        arguments=arguments,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        confirmed=confirmed,
    )


@router.post("/tools/execute")
@router.post("/tool/execute")
def agent_execute_tool(
    request: AgentToolExecuteRequest,
    settings: AppSettings,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Execute a validated, allowlisted tool through the bounded agent loop."""
    required_scope = TOOL_REQUIRED_SCOPES.get(request.tool_name)
    if required_scope and not principal.has_scope(required_scope):
        raise ForbiddenError(
            f"Principal lacks required scope '{required_scope.value}' to execute tool '{request.tool_name}'.",
            details={
                "reason": "insufficient_scope",
                "required_scope": required_scope.value,
                "tool_name": request.tool_name,
            },
        )

    provider = get_model_provider(settings.model_gateway_config())
    merchant_id = principal.merchant_id or settings.default_merchant_id
    res = execute_tool(
        tool_name=request.tool_name,
        arguments=request.arguments,
        merchant_id=merchant_id,
        buyer_id=principal.buyer_id,
        confirmed=request.confirmed,
        model_provider=provider,
    )
    return success(
        {
            "tool_name": res.tool_name,
            "arguments": res.arguments.model_dump(mode="json"),
            "result": res.result,
            "is_state_changing": res.is_state_changing,
            "requires_confirmation": res.requires_confirmation,
        }
    )


@router.post("/converse")
def agent_converse(
    request: AgentConverseRequest,
    principal: CatalogAgent,
    settings: AppSettings,
) -> dict[str, Any]:
    """Conversational natural language commerce endpoint guarded by GuardLLM and bounded tool loop."""
    from services.agent.guard import PromptSafetyClassifier

    # 1. GuardLLM Safety & Prompt Injection Check.
    # The configured guard travels with the call: without it the classifier ran
    # Layer 1 only, so a deployment that selected a remote guard was silently
    # downgraded to heuristics on this surface while /api/explore honoured it.
    PromptSafetyClassifier.assert_safe(request.prompt, config=settings.guard_config())

    # 2. Run Bounded Agent Loop with Facade and configured Model Provider
    facade = get_commerce_facade()
    provider = get_model_provider(settings.model_gateway_config())
    runner = AgentLoopRunner(commerce=facade, model_provider=provider)

    summary = runner.run_bounded_agent(
        user_prompt=request.prompt,
        merchant_id=principal.merchant_id or settings.default_merchant_id,
        buyer_id=principal.buyer_id,
        guard_config=settings.guard_config(),
    )

    first_tool_result = summary.tool_calls[0].result if summary.tool_calls else {}
    offers = first_tool_result.get("offers", [])

    return success(
        {
            "run_id": summary.run_id,
            "intent": summary.final_output.get("intent", {}),
            "model_version": getattr(provider, "model_version", "mock-v1"),
            "tool_calls": [
                {
                    "tool_name": tc.tool_name,
                    "arguments": tc.arguments.model_dump(mode="json"),
                    "result": tc.result,
                    "is_state_changing": tc.is_state_changing,
                    "requires_confirmation": tc.requires_confirmation,
                }
                for tc in summary.tool_calls
            ],
            "offers": offers,
            "count": len(offers),
            "steps_executed": summary.steps_executed,
            "is_completed": summary.is_completed,
        }
    )


@router.post("/search")
@router.post("/offers/query")
def agent_search(
    request: AgentSearchRequest,
    principal: CatalogAgent,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Agent discovery surface for querying bounded offers."""
    service = OfferService()
    offers = service.search_offers(
        session,
        merchant_id=principal.merchant_id,
        category=request.category,
        max_price_minor=request.max_price_minor,
        min_memory_gb=request.min_memory_gb,
        min_storage_gb=request.min_storage_gb,
        max_delivery_days=request.max_delivery_days,
        limit=request.limit,
    )
    return success({"offers": [o.model_dump(mode="json") for o in offers], "count": len(offers)})


@router.post("/checkout")
@router.post("/checkouts")
def agent_checkout(
    request: AgentCheckoutRequest,
    principal: CheckoutAgent,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Agent surface for initiating a checkout with server-calculated price integrity."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for agent operation", code=ErrorCode.FORBIDDEN)

    service = CheckoutService()
    checkout = service.create_checkout(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        offer_id=request.offer_id,
        quantity=request.quantity,
        ttl_minutes=request.ttl_minutes,
    )
    return success({"checkout": checkout.model_dump(mode="json")})


@router.post("/authorization")
@router.post("/authorizations")
def agent_authorization(
    request: AgentAuthorizationRequest,
    principal: CheckoutAgent,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Agent surface for requesting authorization and evaluating policy."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)

    service = AuthorizationService()
    auth = service.request_authorization(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        checkout_id=request.checkout_id,
        ttl_minutes=request.ttl_minutes,
    )
    return success({"authorization": auth.model_dump(mode="json")})


@router.post("/payments")
@router.post("/payment")
def agent_payment(
    request: AgentPaymentRequest,
    principal: PaymentAgent,
    session: DatabaseSession,
    settings: AppSettings,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Agent surface for initiating payment.

    The provider is resolved from this application's settings. It came from the
    process singleton, which meant the one call on this path that can move money
    was selected by the environment rather than by the configuration the
    application was handed.
    """
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for payment", code=ErrorCode.FORBIDDEN)

    service = PaymentService(provider_config=settings.payment_provider_config())
    payment = service.create_payment(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        checkout_id=request.checkout_id,
        authorization_id=request.authorization_id,
        idempotency_key=idempotency_key,
    )
    return success({"payment": payment.model_dump(mode="json")})


@router.get("/payments/{payment_id}")
def agent_get_payment(
    payment_id: str,
    principal: PaymentAgent,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Agent surface for checking payment status."""
    service = PaymentService()
    payment = service.get_payment_by_id(
        session, payment_id=payment_id, merchant_id=principal.merchant_id
    )
    return success({"payment": payment.model_dump(mode="json")})


@router.get("/orders/{order_id}")
def agent_get_order(
    order_id: str,
    principal: CheckoutAgent,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Agent surface for order lookup with tenant and buyer scoping (BUG-38)."""
    if principal.buyer_id is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Buyer ID required for agent order lookup", code=ErrorCode.FORBIDDEN)

    service = OrderService()
    order = service.get_order_for_buyer(
        session,
        buyer_id=principal.buyer_id,
        merchant_id=principal.merchant_id,
        order_id=order_id,
    )
    return success({"order": order.model_dump(mode="json")})


class AgentNegotiateRequest(BaseModel):
    proposed_price_minor: int = Field(ge=1)
    round: int = Field(default=1, ge=1, le=3)


@router.post("/offers/{offer_id}/negotiate")
def agent_negotiate_offer(
    offer_id: str,
    request: AgentNegotiateRequest,
    principal: CatalogAgent,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Bounded, deterministic offer negotiation (v2 addendum §6).

    The floor price comes from merchant policy, never from the LLM. The engine
    accepts, counters at the floor, or rejects — never invents a price.
    """
    from services.negotiation.engine import MAX_NEGOTIATION_ROUNDS, NegotiationEngine

    offer = (
        session.query(Offer)
        .filter(
            Offer.offer_id == offer_id,
            Offer.merchant_id == principal.merchant_id,
            Offer.status == "active",
        )
        .first()
    )
    if offer is None:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Offer not found", code=ErrorCode.NOT_FOUND)

    # Merchant-configured max discount (default 10% = 1000 bps)
    max_discount_bps = getattr(offer, "max_discount_basis_points", 1000)

    result = NegotiationEngine.evaluate_bid(
        round_number=request.round,
        proposed_price_minor=request.proposed_price_minor,
        list_price_minor=offer.unit_price_minor,
        max_discount_basis_points=max_discount_bps,
    )
    return success(
        {
            "offer_id": offer_id,
            "result": result.status,
            "agreed_price_minor": result.agreed_price_minor,
            "counter_price_minor": result.counter_price_minor,
            "round": result.round_number,
            "max_rounds": MAX_NEGOTIATION_ROUNDS,
            "message": result.message,
        }
    )
