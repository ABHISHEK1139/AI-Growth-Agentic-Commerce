"""Task 5 contract tests for exact money and versioned public schemas."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from packages.money import (
    MoneyValueError,
    calculate_total_minor,
    format_currency,
    format_minor_units,
    parse_major_units,
)
from packages.schemas import IntentV1, ToolArgumentsV1, export_json_schemas, schema_for


@given(st.integers(min_value=0, max_value=10**14))
def test_minor_unit_format_and_parse_round_trip(amount_minor: int) -> None:
    """Formatting never loses a paise, regardless of amount size."""
    rendered = format_minor_units(amount_minor)

    assert parse_major_units(rendered) == amount_minor


@given(st.integers(min_value=0, max_value=10**14))
def test_currency_format_and_parse_round_trip(amount_minor: int) -> None:
    """Display formatting with a currency and grouping is also lossless."""
    rendered = format_currency(amount_minor, "INR")

    assert parse_major_units(rendered, currency="INR") == amount_minor


@given(
    unit_price_minor=st.integers(min_value=0, max_value=10**10),
    quantity=st.integers(min_value=1, max_value=10**6),
    shipping_minor=st.integers(min_value=0, max_value=10**10),
    tax_minor=st.integers(min_value=0, max_value=10**10),
)
def test_checkout_totals_use_exact_integer_arithmetic(
    unit_price_minor: int,
    quantity: int,
    shipping_minor: int,
    tax_minor: int,
) -> None:
    """Property 1: a checkout total is the exact integer equation."""
    gross = unit_price_minor * quantity + shipping_minor + tax_minor
    discount_minor = gross // 3

    assert (
        calculate_total_minor(
            unit_price_minor,
            quantity,
            shipping_minor=shipping_minor,
            tax_minor=tax_minor,
            discount_minor=discount_minor,
        )
        == gross - discount_minor
    )


@pytest.mark.parametrize("value", [0.0, 1.25, float("inf"), True])
def test_parser_has_no_float_or_boolean_path(value: float | bool) -> None:
    with pytest.raises(TypeError):
        parse_major_units(value)


@pytest.mark.parametrize("value", ["0.001", "1e3", "-1", "INR 1.00"])
def test_parser_refuses_precision_loss_and_non_decimal_input(value: str) -> None:
    with pytest.raises(MoneyValueError):
        parse_major_units(value)


def test_intent_financial_shape_rejects_unknown_fields() -> None:
    """Requirement 22.2: financial intent cannot smuggle unknown instructions."""
    payload = {
        "schema_version": "1.0",
        "query": "A lightweight laptop",
        "category": "laptops",
        "financial": {"budget_minor": 75000, "currency": "INR", "ignore_budget": True},
        "min_memory_gb": 16,
        "min_storage_gb": 512,
        "max_delivery_days": 4,
        "quantity": 1,
    }

    with pytest.raises(ValidationError, match="ignore_budget"):
        IntentV1.model_validate(payload)

    financial_schema = schema_for("intent")["$defs"]["IntentFinancialConstraintsV1"]
    assert financial_schema["additionalProperties"] is False


def test_tool_arguments_are_closed_and_versioned() -> None:
    payload = {
        "schema_version": "1.0",
        "tool_name": "search_products",
        "intent": None,
        "query": "laptop",
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
        "unexpected": "blocked",
    }

    with pytest.raises(ValidationError, match="unexpected"):
        ToolArgumentsV1.model_validate(payload)


def test_every_public_schema_exports_as_deterministic_json(tmp_path) -> None:  # noqa: ANN001
    expected = {
        "intent",
        "offer",
        "checkout",
        "authorization",
        "payment",
        "order",
        "capability_document",
        "tool_arguments",
    }

    written = export_json_schemas(tmp_path)

    assert {path.name.removesuffix(".v1.schema.json") for path in written} == expected
    for name in expected:
        artifact = tmp_path / f"{name}.v1.schema.json"
        assert json.loads(artifact.read_text(encoding="utf-8")) == schema_for(name)


def test_payment_critical_orm_models_have_all_required_columns():
    """Verify ORM models define all payment-critical columns to prevent schema drift (BUG-22)."""
    from services.orders.models import Order
    from services.payments.models import IdempotencyRecord, Payment, ProviderEvent

    payment_cols = {c.name for c in Payment.__table__.columns}
    assert {
        "payment_id",
        "checkout_id",
        "merchant_id",
        "buyer_id",
        "authorization_id",
        "amount_minor",
        "currency",
        "status",
        "test_mode",
        "created_at",
        "verified_at",
        "updated_at",
        "provider_signature",
        "idempotency_key",
    } <= payment_cols

    order_cols = {c.name for c in Order.__table__.columns}
    assert {
        "order_id",
        "order_number",
        "payment_id",
        "checkout_id",
        "buyer_id",
        "merchant_id",
        "total_minor",
        "amount_minor",
        "currency",
        "status",
        "shipping_address",
        "confirmed_at",
        "created_at",
    } <= order_cols

    idempotency_cols = {c.name for c in IdempotencyRecord.__table__.columns}
    assert {
        "idempotency_record_id",
        "actor_type",
        "actor_id",
        "endpoint",
        "idempotency_key",
        "request_hash",
        "status",
        "response_status",
        "response_status_code",
        "response_body",
        "resource_type",
        "resource_id",
        "created_at",
        "completed_at",
        "expires_at",
    } <= idempotency_cols

    provider_cols = {c.name for c in ProviderEvent.__table__.columns}
    assert {
        "provider_event_id",
        "payment_id",
        "provider",
        "event_type",
        "signature",
        "signature_valid",
        "raw_body_hash",
        "payload",
        "status",
        "received_at",
        "processed_at",
        "created_at",
    } <= provider_cols
