"""Standard Razorpay Web Checkout endpoints integrated into the commerce core (BUG-03)."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import AppSettings, current_principal
from apps.api.db import get_db
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal
from services.authorization.service import AuthorizationService
from services.checkout.service import CheckoutService
from services.inventory.models import Inventory
from services.offers.models import Offer
from services.payments.models import Payment
from services.payments.service import PaymentService

router = APIRouter(tags=["razorpay-standard-checkout"])

DatabaseSession = Annotated[Session, Depends(get_db)]

logger = logging.getLogger(__name__)


class CreateOrderRequest(BaseModel):
    amount: int = Field(
        ..., ge=100, description="Amount in minor units / paise (minimum 100 paise = 1 INR)"
    )
    currency: str = Field(default="INR", min_length=3, max_length=3)
    receipt: str | None = Field(default=None)
    checkout_id: str | None = None
    offer_id: str | None = None
    buyer_id: str | None = None
    merchant_id: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/api/create-order")
@router.post("/api/v1/payments/razorpay/create-order")
def create_razorpay_order(
    request: CreateOrderRequest,
    settings: AppSettings,
    session: DatabaseSession,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Create a Razorpay order routed through the commerce core."""
    key_id = settings.razorpay_key_id
    key_secret = settings.razorpay_key_secret

    if not key_id or not key_secret:
        raise DomainError(
            "Razorpay payment gateway credentials not configured.",
            code=ErrorCode.INTERNAL_ERROR,
        )

    merchant_id = (principal.merchant_id if principal else None) or settings.default_merchant_id
    buyer_id = (principal.buyer_id if principal else None) or "buy_shopper_demo"

    if session is None:
        import httpx

        auth = (key_id, key_secret)
        payload = {
            "amount": request.amount,
            "currency": request.currency,
            "receipt": request.receipt or f"rcpt_{int(datetime.now(UTC).timestamp())}",
            "notes": request.notes,
        }
        res = httpx.post(
            "https://api.razorpay.com/v1/orders", auth=auth, json=payload, timeout=10.0
        )
        res.raise_for_status()
        rzp_order = res.json()
        return {
            "data": {
                "id": rzp_order["id"],
                "order_id": rzp_order["id"],
                "payment_id": f"pay_{rzp_order['id'].replace('order_', '')}",
                "checkout_id": request.checkout_id
                or f"chk_{rzp_order['id'].replace('order_', '')}",
                "amount": rzp_order["amount"],
                "currency": rzp_order["currency"],
                "key_id": key_id,
                "status": "created",
            }
        }

    try:
        from services.catalog.models import Buyer, Merchant

        # Ensure merchant exists in DB
        merch = session.query(Merchant).filter(Merchant.merchant_id == merchant_id).first()
        if merch is None:
            if settings.is_local:
                merch = Merchant(
                    merchant_id=merchant_id, name="AgentPay Live Store", status="active"
                )
                session.add(merch)
                session.flush()
            else:
                raise DomainError("The merchant does not exist.", code=ErrorCode.NOT_FOUND)

        # Ensure buyer exists in DB
        buyer = session.query(Buyer).filter(Buyer.buyer_id == buyer_id).first()
        if buyer is None:
            if settings.is_local:
                buyer = Buyer(
                    buyer_id=buyer_id,
                    tenant_id=merchant_id,
                    display_name="Demo Shopper",
                    status="active",
                )
                session.add(buyer)
                session.flush()
            else:
                raise DomainError("The buyer does not exist.", code=ErrorCode.NOT_FOUND)

        checkout_id = request.checkout_id or request.notes.get("checkout_id")

        from packages.config.providers import PaymentProviderConfig

        prov_cfg = PaymentProviderConfig(
            provider="razorpay",
            razorpay_key_id=key_id,
            razorpay_key_secret=key_secret,
            razorpay_webhook_secret=settings.razorpay_webhook_secret,
            timeout_seconds=settings.payment_provider_timeout_seconds,
        )

        checkout_service = CheckoutService()
        auth_service = AuthorizationService()
        payment_service = PaymentService(provider_config=prov_cfg)

        if not checkout_id:
            if request.offer_id:
                offer = (
                    session.query(Offer)
                    .filter(
                        Offer.offer_id == request.offer_id,
                        Offer.merchant_id == merchant_id,
                        Offer.status == "active",
                    )
                    .first()
                )
            else:
                offer = (
                    session.query(Offer)
                    .join(Inventory, Inventory.offer_id == Offer.offer_id)
                    .filter(
                        Offer.merchant_id == merchant_id,
                        Offer.status == "active",
                        Offer.unit_price_minor == request.amount,
                        (Inventory.available_quantity - Inventory.reserved_quantity) >= 1,
                    )
                    .first()
                )

            if offer is None:
                raise DomainError(
                    "Active verified offer not found for this merchant.",
                    code=ErrorCode.NOT_FOUND,
                )

            checkout_schema = checkout_service.create_checkout(
                session,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                offer_id=offer.offer_id,
                quantity=1,
                ttl_minutes=15,
            )
            checkout_id = checkout_schema.checkout_id
            if request.amount != checkout_schema.pricing.total_minor:
                raise DomainError(
                    f"Amount mismatch: requested amount {request.amount} does not match verified total {checkout_schema.pricing.total_minor}",
                    code=ErrorCode.VALIDATION_ERROR,
                )
        else:
            from services.checkout.models import Checkout

            chk_record = (
                session.query(Checkout)
                .filter(
                    Checkout.checkout_id == checkout_id,
                    Checkout.merchant_id == merchant_id,
                    Checkout.buyer_id == buyer_id,
                )
                .first()
            )
            if chk_record is None:
                raise DomainError("Checkout not found", code=ErrorCode.NOT_FOUND)
            if request.amount != chk_record.total_minor:
                raise DomainError(
                    f"Amount mismatch: requested amount {request.amount} does not match checkout total {chk_record.total_minor}",
                    code=ErrorCode.VALIDATION_ERROR,
                )

        # 2. Evaluate policy and obtain authorization
        auth_schema = auth_service.request_authorization(
            session,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            checkout_id=checkout_id,
        )
        if auth_schema.status not in ("approved", "pending"):
            raise DomainError(
                f"Authorization denied by policy: status={auth_schema.status}",
                code=ErrorCode.FORBIDDEN,
            )

        # 3. Create payment through PaymentService
        payment_schema = payment_service.create_payment(
            session,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            checkout_id=checkout_id,
            authorization_id=auth_schema.authorization_id,
            idempotency_key=checkout_id,
        )

        session.commit()

        return {
            "data": {
                "id": payment_schema.provider_order_id,
                "order_id": payment_schema.provider_order_id,
                "payment_id": payment_schema.payment_id,
                "checkout_id": checkout_id,
                "amount": payment_schema.amount_minor,
                "currency": payment_schema.currency,
                "key_id": key_id,
                "status": payment_schema.status,
            }
        }
    except DomainError:
        raise
    except Exception as exc:
        logger.error(
            "create_order_unexpected_error",
            extra={"error": str(exc)},
            exc_info=True,
        )
        raise DomainError(
            "Failed to create payment order. Please try again.",
            code=ErrorCode.SERVICE_UNAVAILABLE,
        ) from exc


@router.post("/api/verify-payment")
@router.post("/api/v1/payments/razorpay/verify-signature")
def verify_razorpay_payment(
    request: VerifyPaymentRequest,
    settings: AppSettings,
    session: DatabaseSession,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Verify HMAC-SHA256 signature and finalize order in the commerce core."""
    key_secret = settings.razorpay_key_secret

    if not key_secret:
        raise DomainError(
            "Razorpay key secret not configured.",
            code=ErrorCode.INTERNAL_ERROR,
        )

    message = f"{request.razorpay_order_id}|{request.razorpay_payment_id}"
    generated_sig = hmac.new(
        key_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(generated_sig, request.razorpay_signature):
        raise DomainError(
            "Payment verification failed: HMAC signature mismatch.",
            code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
        )

    if session is not None:
        payment = (
            session.query(Payment)
            .filter(Payment.provider_order_id == request.razorpay_order_id)
            .first()
        )
        if payment is not None:
            payment_service = PaymentService(provider_config=settings.payment_provider_config())
            _, order_res = payment_service.verify_payment(
                session,
                payment_id=payment.payment_id,
                provider_payment_id=request.razorpay_payment_id,
                provider_signature=request.razorpay_signature,
            )
            session.commit()
            return {
                "data": {
                    "verified": True,
                    "order_id": request.razorpay_order_id,
                    "payment_id": request.razorpay_payment_id,
                    "confirmed_order_id": order_res.order_id,
                    "status": "paid",
                }
            }

    return {
        "data": {
            "verified": True,
            "order_id": request.razorpay_order_id,
            "payment_id": request.razorpay_payment_id,
            "confirmed_order_id": f"ord_{request.razorpay_order_id.replace('order_', '')}",
            "status": "paid",
        }
    }


@router.get("/api/v1/payments/razorpay/checkout-url")
def get_razorpay_checkout_url(
    amount: int,
    currency: str = "INR",
    checkout_id: str | None = None,
    offer_id: str | None = None,
    receipt: str | None = None,
    return_url: str | None = None,
    principal: Principal = Depends(current_principal),
    settings: AppSettings = None,  # FastAPI injects via Annotated; None placeholder for Python
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Return a Razorpay payment URL for browser-redirect checkout.

    Instead of opening the Razorpay modal inline via JS, the browser navigates
    directly to this URL. Razorpay redirects back to ``return_url`` when the
    buyer completes or abandons the flow. The ``razorpay_payment_id`` (if any)
    is attached as a query parameter on the redirect so the return page can call
    ``POST /api/v1/payments/razorpay/verify-signature`` to confirm the payment.

    If ``return_url`` is omitted, a default gateway return page is used.
    """
    # FastAPI injects the real AppSettings via the Annotated dependency; the
    # None default is only to satisfy Python's non-defaults-before-defaults rule.
    if settings is None:
        raise DomainError("Settings not configured.", code=ErrorCode.INTERNAL_ERROR)
    key_id = settings.razorpay_key_id
    key_secret = settings.razorpay_key_secret

    if not key_id or not key_secret:
        raise DomainError(
            "Razorpay payment gateway credentials not configured.",
            code=ErrorCode.INTERNAL_ERROR,
        )

    merchant_id = (principal.merchant_id if principal else None) or settings.default_merchant_id
    buyer_id = (principal.buyer_id if principal else None) or "buy_shopper_demo"

    # Resolve checkout / offer like create_razorpay_order does
    resolved_checkout_id = checkout_id
    if not resolved_checkout_id:
        if offer_id:
            from services.offers.models import Offer

            offer = (
                session.query(Offer)
                .filter(
                    Offer.offer_id == offer_id,
                    Offer.merchant_id == merchant_id,
                    Offer.status == "active",
                )
                .first()
            )
            if offer is None:
                raise DomainError("Active offer not found.", code=ErrorCode.NOT_FOUND)
            amount = offer.unit_price_minor
        else:
            from services.inventory.models import Inventory
            from services.offers.models import Offer

            offer = (
                session.query(Offer)
                .join(Inventory, Inventory.offer_id == Offer.offer_id)
                .filter(
                    Offer.merchant_id == merchant_id,
                    Offer.status == "active",
                    Offer.unit_price_minor == amount,
                    (Inventory.available_quantity - Inventory.reserved_quantity) >= 1,
                )
                .first()
            )
            if offer is None:
                raise DomainError(
                    "Active verified offer not found for this merchant.",
                    code=ErrorCode.NOT_FOUND,
                )
            offer_id = offer.offer_id

        from services.checkout.service import CheckoutService

        checkout_service = CheckoutService()
        checkout_schema = checkout_service.create_checkout(
            session,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            offer_id=offer_id,
            quantity=1,
            ttl_minutes=15,
        )
        resolved_checkout_id = checkout_schema.checkout_id

        # Authorize
        from services.authorization.service import AuthorizationService

        auth_service = AuthorizationService()
        auth_schema = auth_service.request_authorization(
            session,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            checkout_id=resolved_checkout_id,
        )
        if auth_schema.status not in ("approved", "pending"):
            raise DomainError(
                f"Authorization denied by policy: status={auth_schema.status}",
                code=ErrorCode.FORBIDDEN,
            )

        # Create payment record
        from packages.config.providers import PaymentProviderConfig

        prov_cfg = PaymentProviderConfig(
            provider="razorpay",
            razorpay_key_id=key_id,
            razorpay_key_secret=key_secret,
            razorpay_webhook_secret=settings.razorpay_webhook_secret,
            timeout_seconds=settings.payment_provider_timeout_seconds,
        )
        payment_service = PaymentService(provider_config=prov_cfg)
        payment_schema = payment_service.create_payment(
            session,
            buyer_id=buyer_id,
            merchant_id=merchant_id,
            checkout_id=resolved_checkout_id,
            authorization_id=auth_schema.authorization_id,
            idempotency_key=resolved_checkout_id,
        )
        session.commit()
        backend_order_id = payment_schema.provider_order_id
    else:
        backend_order_id = f"order_{resolved_checkout_id}"

    # Build the Razorpay checkout URL
    # If live/configured Razorpay credentials are present, attempt to create a standard Payment Link
    checkout_url = None
    if settings.razorpay_is_configured() and not settings.payment_is_test_mode:
        import httpx

        try:
            auth = (key_id, key_secret)
            pl_payload = {
                "amount": amount,
                "currency": currency,
                "accept_partial": False,
                "reference_id": backend_order_id,
                "description": f"AgentPay checkout {resolved_checkout_id}",
                "callback_url": return_url
                or f"/checkout/razorpay-return?checkout_id={resolved_checkout_id}",
                "callback_method": "get",
                "notes": {"checkout_id": resolved_checkout_id},
            }
            res = httpx.post(
                "https://api.razorpay.com/v1/payment_links",
                auth=auth,
                json=pl_payload,
                timeout=settings.payment_provider_timeout_seconds,
            )
            if res.status_code in (200, 201):
                checkout_url = res.json().get("short_url")
        except Exception as exc:
            logger.warning("Failed to create Razorpay payment link: %s", exc)

    if not checkout_url:
        # Standard in-browser checkout return flow
        checkout_url = (
            return_url
            if return_url
            else f"/checkout/razorpay-return?checkout_id={resolved_checkout_id}"
        )

    return {
        "data": {
            "checkout_url": checkout_url,
            "order_id": backend_order_id,
            "checkout_id": resolved_checkout_id,
            "key_id": key_id,
            "amount": amount,
            "currency": currency,
            "redirect_mode": True,
        }
    }


@router.post("/api/v1/payments/razorpay/webhook")
async def razorpay_webhook(
    request: Request,
    settings: AppSettings = None,  # FastAPI injects via Annotated; None placeholder for Python
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Receive Razorpay webhook callbacks.

    Razorpay calls this endpoint when payment events occur (e.g. payment.captured).
    The raw body is verified using HMAC-SHA256 with the webhook secret before any
    processing occurs. Verified events are stored in the ``provider_event`` table
    and deduplicated so duplicate deliveries are safe.

    The webhook secret is configured via ``RAZORPAY_WEBHOOK_SECRET``.
    """
    # This endpoint is called by Razorpay's servers, not by a logged-in browser,
    # so it intentionally has no `current_principal` dependency. The HMAC on the
    # raw body is the authentication mechanism.
    if settings is None:
        raise DomainError("Settings not configured.", code=ErrorCode.INTERNAL_ERROR)

    # Read raw body as bytes (required for deterministic HMAC verification)
    body_bytes = await request.body()

    # Get signature header — Razorpay sends it as X-Razorpay-Signature
    razorpay_signature = request.headers.get("x-razorpay-signature", "")

    # Delegate to the existing WebhookProcessor
    from services.payments.webhooks import WebhookProcessor

    prov_cfg = settings.payment_provider_config()
    processor = WebhookProcessor(provider_config=prov_cfg)

    try:
        result = processor.process_webhook(
            session=session,
            raw_body=body_bytes,
            signature=razorpay_signature,
            provider_name="razorpay",
        )
        return {"ok": True, **result}
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(
            f"Webhook processing failed: {exc}",
            code=ErrorCode.INTERNAL_ERROR,
        ) from exc
