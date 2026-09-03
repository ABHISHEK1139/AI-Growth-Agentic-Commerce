"""Real-service integration tests for Groq and Razorpay test mode (Task 44, Requirement 38)."""

import os

import pytest

pytestmark = pytest.mark.integration

from services.agent.model import GroqModelProvider
from services.payments.razorpay_adapter import RazorpayPaymentProvider


@pytest.mark.skipif(
    not (os.environ.get("GROQ_API_KEY") or os.environ.get("MODEL_API_KEY")),
    reason="GROQ_API_KEY / MODEL_API_KEY not configured in environment, skipping live model test.",
)
def test_live_groq_structured_output():
    api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("MODEL_API_KEY")
    base_url = os.environ.get("MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    model_name = os.environ.get("MODEL_NAME", "llama-3.3-70b-versatile")
    provider = GroqModelProvider(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )
    res = provider.generate(
        "Extract intent: I need a laptop under 50000 INR",
        schema={"type": "object"},
    )
    assert res.content is not None
    assert res.latency_ms > 0


@pytest.mark.skipif(
    not (os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")),
    reason="RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not configured, skipping live Razorpay test.",
)
def test_live_razorpay_test_mode_order():
    provider = RazorpayPaymentProvider(
        key_id=os.environ["RAZORPAY_KEY_ID"],
        key_secret=os.environ["RAZORPAY_KEY_SECRET"],
        webhook_secret=os.environ.get("RAZORPAY_WEBHOOK_SECRET"),
    )
    order = provider.create_order(
        amount_minor=50000,  # 500 INR
        currency="INR",
        receipt="chk_live_test_receipt",
        notes={"test_mode": "true", "purpose": "real_integration_test"},
    )
    assert order.provider_order_id is not None
    assert order.provider_order_id.startswith("order_")
    assert order.amount_minor == 50000
    assert order.currency == "INR"
