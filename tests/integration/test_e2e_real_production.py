"""Run full commerce lifecycle on real Amazon dataset catalog with live Groq and live Razorpay."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

pytestmark = pytest.mark.integration

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.db import Base
from services.agent.model import GroqModelProvider
from services.authorization.service import AuthorizationService
from services.catalog.models import Buyer, CatalogVersion, Merchant, MerchantRules, Product
from services.checkout.service import CheckoutService
from services.inventory.models import Inventory
from services.offers.models import Offer
from services.payments.razorpay_adapter import RazorpayPaymentProvider
from services.payments.service import PaymentService
from services.policy.models import BuyerPolicy


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


def main() -> None:
    print("=" * 75)
    print("AGENTPAY REAL DATASET COMMERCE & LIVE GATEWAY DEMO")
    print("=" * 75)

    # 1. Database Setup
    print("\n[1] Initializing Database Schema...")
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()

    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS audit_event (
                event_id TEXT PRIMARY KEY,
                merchant_id TEXT,
                request_id TEXT,
                trace_id TEXT,
                agent_run_id TEXT,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                input_hash TEXT,
                decision TEXT,
                reason_code TEXT,
                policy_version TEXT,
                model_version TEXT,
                amount_minor INTEGER,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    )
    session.commit()
    print("  -> Schema initialized successfully.")

    # 2. Ingesting Real Amazon Products & Offers from Dataset Pipeline
    print(
        "\n[2] Ingesting Real Products from 'data/out/catalog/products.jsonl' & 'offers.jsonl'..."
    )
    merchant_id = "merchant_demo"
    buyer_id = "buy_priya_sharma"
    cat_ver_id = "cat_ver_real_amazon_2026"

    merchant = Merchant(
        merchant_id=merchant_id,
        name="AgentPay Demo Electronics Store",
        status="active",
    )
    catalog_ver = CatalogVersion(
        catalog_version_id=cat_ver_id,
        merchant_id=merchant_id,
        status="published",
        product_count=100,
        valid_count=100,
    )
    buyer = Buyer(
        buyer_id=buyer_id,
        tenant_id=merchant_id,
        display_name="Priya Sharma",
        status="active",
    )
    merchant_rules = MerchantRules(
        merchant_id=merchant_id,
        version="1.0",
        max_transaction_minor=500000000,  # 5 Crore paise
        auto_approval_limit_minor=250000000,
        max_discount_basis_points=1000,
        allowed_categories=[],  # Empty list means all allowed
        blocked_categories=[],
        allowed_payment_methods=["card", "upi"],
        allow_out_of_stock=False,
    )
    buyer_policy = BuyerPolicy(
        buyer_id=buyer_id,
        version="1.0",
        max_transaction_minor=500000000,
        auto_approval_limit_minor=250000000,
        allowed_merchants=[merchant_id],
        allowed_categories=[],  # Empty list means all allowed
    )
    session.add_all([merchant, catalog_ver, buyer, merchant_rules, buyer_policy])
    session.commit()

    # Load 50 real products and offers from the pipeline output
    products_file = PROJECT_ROOT / "data/out/catalog/products.jsonl"
    offers_file = PROJECT_ROOT / "data/out/catalog/offers.jsonl"

    loaded_products = 0
    loaded_offers = 0

    with open(products_file, encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx >= 50:
                break
            raw_p = json.loads(line)
            p_obj = Product(
                product_id=raw_p["product_id"],
                catalog_version_id=cat_ver_id,
                merchant_id=merchant_id,
                external_product_id=raw_p.get("parent_asin")
                or raw_p.get("external_product_id", f"ASIN_{idx}"),
                category_id=raw_p.get("category") or "laptop",
                title=raw_p["title"],
                description=raw_p.get("description", []),
                specifications=raw_p.get("details", {}),
                status="valid",
                average_rating=float(raw_p.get("average_rating", 4.0)),
                rating_number=int(raw_p.get("rating_number", 10)),
            )
            session.add(p_obj)
            loaded_products += 1

    with open(offers_file, encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx >= 50:
                break
            raw_o = json.loads(line)
            o_obj = Offer(
                offer_id=raw_o["offer_id"],
                catalog_version_id=cat_ver_id,
                product_id=raw_o["product_id"],
                merchant_id=merchant_id,
                unit_price_minor=int(raw_o["unit_price_minor"]),
                currency=raw_o["currency"],
                offer_version=1,
                status="active",
                pricing_source=raw_o.get("pricing_source", "synthetic_band_random"),
                delivery_days=int(raw_o.get("delivery_days", 3)),
                return_period_days=int(raw_o.get("return_period_days", 14)),
                expires_at=datetime(2030, 12, 31, tzinfo=UTC),
            )
            i_obj = Inventory(
                offer_id=raw_o["offer_id"],
                available_quantity=int(raw_o.get("available_quantity", 10)),
                reserved_quantity=0,
                version=1,
            )
            session.add(o_obj)
            session.add(i_obj)
            loaded_offers += 1

    session.commit()
    print(
        f"  -> Ingested {loaded_products} real products and {loaded_offers} offers from datasets."
    )

    # 3. Real SQL Query Execution
    print("\n[3] Executing Real SQL Query Against Ingested Amazon Catalog...")
    sql_res = session.execute(
        text(
            """
            SELECT p.product_id, o.offer_id, p.title, o.unit_price_minor, i.available_quantity
              FROM product p
              JOIN offer o ON o.product_id = p.product_id
              JOIN inventory i ON i.offer_id = o.offer_id
             WHERE o.status = 'active'
               AND i.available_quantity > 0
             LIMIT 5
            """
        )
    ).fetchall()

    for row in sql_res:
        print(f"  * [{row[1]}] {row[2][:65]}...")
        print(f"    Price: INR {row[3] / 100:,.2f} | Stock: {row[4]} units available")

    # Pick the first real item for live purchase
    selected_row = sql_res[0]
    selected_product_id = selected_row[0]
    selected_offer_id = selected_row[1]
    selected_title = selected_row[2]
    selected_price_minor = selected_row[3]

    # 4. Live Groq LLM Intent Extraction
    print("\n[4] Calling Live Groq LLM API for Shopper Intent...")
    groq_provider = GroqModelProvider(
        api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("MODEL_API_KEY"),
        base_url=os.environ.get("MODEL_BASE_URL", "https://api.groq.com/openai/v1"),
        model_name=os.environ.get("MODEL_NAME", "llama-3.3-70b-versatile"),
    )
    llm_res = groq_provider.generate(
        f"Find product matching: {selected_title[:50]} under INR {selected_price_minor // 100 + 1_000}",
        schema={"type": "json_object"},
    )
    print(f"  -> Groq Model: {os.environ.get('MODEL_NAME', 'llama-3.3-70b-versatile')}")
    print(f"  -> Latency: {llm_res.latency_ms:.1f}ms")
    print(f"  -> Extracted Intent: {llm_res.parsed_json or llm_res.content}")

    # 5. Create Real Checkout
    print(f"\n[5] Creating Checkout for Real Dataset Item '{selected_title[:45]}...'...")
    checkout_service = CheckoutService()
    checkout = checkout_service.create_checkout(
        session,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        offer_id=selected_offer_id,
        quantity=1,
    )
    session.commit()
    print(f"  -> Checkout ID: {checkout.checkout_id}")
    print(f"  -> SHA-256 Price Hash: {checkout.price_hash}")
    print(
        f"  -> Total Minor: {checkout.pricing.total_minor} (INR {checkout.pricing.total_minor / 100:,.2f})"
    )

    # 6. Policy & Authorization Gate
    print("\n[6] Evaluating Policy & Pre-Payment Authorization Gate...")
    auth_service = AuthorizationService()
    auth = auth_service.request_authorization(
        session,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        checkout_id=checkout.checkout_id,
    )
    session.commit()
    print(f"  -> Authorization ID: {auth.authorization_id}")
    print(f"  -> Policy Decision: {auth.policy.decision} (Reason: {auth.policy.reason_code})")
    print(f"  -> Status: '{auth.status}'")

    # 7. LIVE Razorpay Sandbox Order Creation (Real HTTP API Request)
    print("\n[7] Calling LIVE Razorpay Sandbox API to Create Real Order...")
    rzp_provider = RazorpayPaymentProvider(
        key_id=os.environ["RAZORPAY_KEY_ID"],
        key_secret=os.environ["RAZORPAY_KEY_SECRET"],
        webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET"),
    )
    payment_service = PaymentService(provider=rzp_provider)
    payment = payment_service.create_payment(
        session,
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        checkout_id=checkout.checkout_id,
        authorization_id=auth.authorization_id,
        idempotency_key=f"idm_{checkout.checkout_id}",
    )
    session.commit()

    print(f"  -> Local Payment Record ID: {payment.payment_id}")
    print(f"  -> REAL RAZORPAY ORDER ID: {payment.provider_order_id}")
    print(f"  -> Amount: INR {payment.amount_minor / 100:,.2f} ({payment.amount_minor} paise)")
    print(f"  -> Status: '{payment.status}'")

    # 8. Verifying Live Order Directly on Razorpay API
    print("\n[8] Querying Razorpay Servers for Order Confirmation...")
    live_rzp_order = rzp_provider.fetch_order(payment.provider_order_id)
    assert live_rzp_order.provider_order_id == payment.provider_order_id
    assert live_rzp_order.amount_minor == payment.amount_minor
    assert live_rzp_order.currency == "INR"
    assert live_rzp_order.status in ("created", "attempted", "paid")
    print("  -> Razorpay API Confirmed:")
    print(f"     Order ID: {live_rzp_order.provider_order_id}")
    print(f"     Amount: {live_rzp_order.amount_minor} paise")
    print(f"     Status: '{live_rzp_order.status}'")

    # 9. Audit Ledger Inspection
    print("\n[9] Inspecting Database Audit Ledger...")
    audit_rows = session.execute(
        text(
            "SELECT event_type, aggregate_type, aggregate_id, decision, reason_code FROM audit_event ORDER BY created_at ASC"
        )
    ).fetchall()
    assert len(audit_rows) >= 5
    for row in audit_rows:
        print(
            f"  [AUDIT] {row[0]:<25} | {row[1]:<12} | ID: {row[2]} | Decision: {str(row[3]):<8} ({row[4]})"
        )

    print("\n" + "=" * 75)
    print("TEST SUCCESS: REAL DATASETS + LIVE GROQ + LIVE RAZORPAY 100% OPERATIONAL")
    print("=" * 75)


@pytest.mark.skipif(
    not (
        (os.environ.get("GROQ_API_KEY") or os.environ.get("MODEL_API_KEY"))
        and os.environ.get("RAZORPAY_KEY_ID")
        and os.environ.get("RAZORPAY_KEY_SECRET")
    ),
    reason="GROQ_API_KEY / MODEL_API_KEY and RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET must be configured for live production flow test.",
)
def test_real_dataset_commerce_lifecycle_production() -> None:
    main()


if __name__ == "__main__":
    main()
