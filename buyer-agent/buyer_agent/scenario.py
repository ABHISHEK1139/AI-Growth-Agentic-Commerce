"""End-to-end scenario runner for independent AI buyers (Task 26, Requirement 20)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from buyer_agent.client import AgentPayClient


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    is_success: bool
    checkout_id: str | None
    authorization_id: str | None
    payment_id: str | None
    order_id: str | None
    total_minor: int | None
    message: str


def run_buyer_purchase_scenario(
    client: AgentPayClient,
    api_key: str,
    *,
    category: str = "laptop",
    max_price_minor: int = 8000000,
    approve_hook: Callable[[dict], bool] | None = None,
    log_hook: Callable[[str], None] | None = None,
) -> PurchaseResult:
    """Execute complete autonomous purchase flow using only documented public endpoints."""

    def log(step: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
        msg = f"[{ts}] {step}"
        if log_hook:
            log_hook(msg)
        else:
            print(msg)  # noqa: T201

    # 1. Capability Discovery
    log("Discovering merchant capabilities...")
    cap_res = client.get_capabilities()
    if not cap_res.is_success:
        return PurchaseResult(False, None, None, None, None, None, "Capability discovery failed.")
    log(f"Merchant supports: {cap_res.data.get('capabilities', [])}")

    # 2. Authentication
    log("Authenticating buyer client via API key exchange...")
    auth_token_res = client.authenticate(api_key)
    if not auth_token_res.is_success:
        return PurchaseResult(False, None, None, None, None, None, "Authentication failed.")
    log("Bearer token acquired successfully.")

    # 3. Search offers
    log(f"Searching offers for category '{category}' under {max_price_minor / 100} INR...")
    search_res = client.search_offers(category=category, max_price_minor=max_price_minor)
    if not search_res.is_success:
        return PurchaseResult(False, None, None, None, None, None, "Offer search failed.")

    offers = search_res.data.get("data", {}).get("offers", [])
    if not offers:
        return PurchaseResult(
            False, None, None, None, None, None, "No offers found matching criteria."
        )
    selected_offer = offers[0]
    offer_id = selected_offer["offer_id"]
    log(f"Selected offer: {offer_id} @ {selected_offer['unit_price_minor'] / 100} INR")

    # 4. Create Checkout
    log(f"Creating checkout for offer {offer_id}...")
    chk_res = client.create_checkout(offer_id=offer_id, quantity=1)
    if not chk_res.is_success:
        return PurchaseResult(False, None, None, None, None, None, "Checkout creation failed.")

    checkout_data = chk_res.data.get("data", {}).get("checkout", {})
    checkout_id = checkout_data["checkout_id"]
    total_minor = checkout_data["total_minor"]
    log(
        f"Checkout created: {checkout_id}, Total: {total_minor / 100} INR, Price Hash: {checkout_data['price_hash']}"
    )

    # 5. Request Authorization
    log(f"Requesting human authorization for checkout {checkout_id}...")
    ath_res = client.request_authorization(checkout_id=checkout_id)
    if not ath_res.is_success:
        return PurchaseResult(
            False, checkout_id, None, None, None, total_minor, "Authorization request failed."
        )

    auth_data = ath_res.data.get("data", {}).get("authorization", {})
    authorization_id = auth_data["authorization_id"]
    log(
        f"Authorization held: {authorization_id}, Policy Decision: {auth_data['policy']['decision']}"
    )

    # 6. Human in the Loop approval
    approved = True
    if approve_hook:
        approved = approve_hook(auth_data)
    if not approved:
        return PurchaseResult(
            False,
            checkout_id,
            authorization_id,
            None,
            None,
            total_minor,
            "Human approval rejected.",
        )
    log("Human approval confirmed.")

    # 7. Create Payment with Idempotency Key
    idempotency_key = f"idk_client_{uuid.uuid4().hex[:12]}"
    log(f"Initiating payment with idempotency key: {idempotency_key}...")
    pay_res = client.create_payment(
        checkout_id=checkout_id,
        authorization_id=authorization_id,
        idempotency_key=idempotency_key,
    )
    if not pay_res.is_success:
        return PurchaseResult(
            False,
            checkout_id,
            authorization_id,
            None,
            None,
            total_minor,
            "Payment initiation failed.",
        )

    pay_data = pay_res.data.get("data", {}).get("payment", {})
    payment_id = pay_data.get("payment_id")
    order_id = pay_data.get("order_id")
    log(
        f"Payment initiated: {payment_id}, Provider Order: {pay_data.get('provider_order_id')}, Status: {pay_data.get('status')}"
    )

    # 8. Check Payment Status & Retrieve Confirmed Order
    if not order_id and payment_id:
        log(f"Checking payment status for {payment_id}...")
        status_res = client.get_payment_status(payment_id)
        if status_res.is_success:
            st_data = status_res.data.get("data", {}).get("payment", {})
            order_id = st_data.get("order_id")

    if order_id:
        log(f"Order confirmed: {order_id}. Fetching full order summary...")
        order_res = client.get_order(order_id)
        if order_res.is_success:
            ord_data = order_res.data.get("data", {}).get("order", {})
            log(
                f"Order successfully verified: {ord_data.get('order_id')} (Status: {ord_data.get('status')})"
            )

    return PurchaseResult(
        is_success=True,
        checkout_id=checkout_id,
        authorization_id=authorization_id,
        payment_id=payment_id,
        order_id=order_id,
        total_minor=total_minor,
        message="Purchase scenario completed successfully.",
    )
