"""Payment and webhook API endpoints (Phase E, Requirements 14, 15, 16, 17)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import (
    AppSettings,
    require_scopes,
    settings_for,
)
from apps.api.db import get_db
from apps.api.envelope import success
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal, Scope
from services.payments.service import PaymentService
from services.payments.webhooks import WebhookProcessor

router = APIRouter(prefix="/api/v1", tags=["payments"])
DatabaseSession = Annotated[Session, Depends(get_db)]
PaymentPrincipal = Annotated[Principal, Depends(require_scopes(Scope.PAYMENT_WRITE))]


class CreatePaymentRequest(BaseModel):
    checkout_id: str = Field(min_length=1, max_length=128)
    authorization_id: str = Field(min_length=1, max_length=128)


@router.post("/payments")
def create_payment(
    request: CreatePaymentRequest,
    principal: PaymentPrincipal,
    session: DatabaseSession,
    settings: AppSettings,
    idempotency_key: Annotated[str | None, Field(max_length=128)] = Header(
        default=None, alias="Idempotency-Key"
    ),
) -> dict[str, Any]:
    """Initiate a payment following the 12-step sequence.

    The provider comes from this application's settings rather than from the
    cached process singleton, so which provider can move money is decided by the
    configuration the application was built with.
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
def get_payment(
    payment_id: str,
    session: DatabaseSession,
    principal: PaymentPrincipal,
) -> dict[str, Any]:
    """Fetch payment attempt details.

    Scoped to the caller's tenant: a payment identifier from another merchant
    is answered NOT_FOUND, identical to an identifier that never existed.
    """
    if principal.buyer_id is None:
        raise DomainError("Buyer ID required for payment lookup", code=ErrorCode.FORBIDDEN)
    service = PaymentService()
    payment = service.get_payment_by_id(
        session,
        payment_id=payment_id,
        merchant_id=principal.merchant_id,
        buyer_id=principal.buyer_id,
    )
    return success({"payment": payment.model_dump(mode="json")})


class RefundRequest(BaseModel):
    payment_id: str
    amount_minor: int | None = Field(
        default=None,
        ge=0,
        description="Partial refund amount in minor units. Omit to refund the full payment.",
    )
    reason: str = Field(default="Customer requested refund")


class RefundResponse(BaseModel):
    refund_id: str
    payment_id: str
    amount_minor: int
    status: str


@router.post("/payments/{payment_id}/refund")
def refund_payment(
    payment_id: str,
    request: RefundRequest,
    session: DatabaseSession,
    principal: PaymentPrincipal,
    settings: AppSettings,
) -> dict[str, Any]:
    """Issue a full or partial refund for a verified payment.

    The refund is processed by the configured payment provider. Partial refunds
    are supported by passing ``amount_minor``; omit it to refund the full original
    charge.
    """
    if request.payment_id != payment_id:
        raise DomainError(
            "The payment ID in the path and the request body do not match.",
            code=ErrorCode.VALIDATION_ERROR,
        )
    service = PaymentService(provider_config=settings.payment_provider_config())
    result = service.refund_payment(
        session,
        payment_id=payment_id,
        amount_minor=request.amount_minor,
        reason=request.reason,
        merchant_id=principal.merchant_id,
    )
    return success({"refund": RefundResponse(**result).model_dump()})


@router.post("/webhooks/razorpay")
@router.post("/webhooks/fake")
async def handle_webhook(
    request: Request,
    session: DatabaseSession,
    x_razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
) -> dict[str, Any]:
    signature = x_razorpay_signature or x_signature
    if not signature:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError(
            "Webhook signature header is required (X-Razorpay-Signature or X-Signature)",
            code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
        )

    raw_body = await request.body()

    # Resolved from the application's settings, not the process singleton: the
    # provider decides which signing secret this callback is verified against, so
    # picking it from the environment instead is the difference between rejecting a
    # forged webhook and accepting one. The resolved provider name travels with the
    # event so the ledger records which gateway it came from rather than a default.
    settings = settings_for(request)
    prov_cfg = settings.payment_provider_config()
    processor = WebhookProcessor(provider_config=prov_cfg)
    return processor.process_webhook(
        session,
        raw_body=raw_body,
        signature=signature,
        provider_name=str(getattr(prov_cfg, "provider", "fake")),
    )
