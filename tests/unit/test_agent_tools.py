"""Unit tests for Tool Argument Validation, Allowlist Enforcement, and anti-SSRF protections."""

from __future__ import annotations

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.agent.tools import ALLOWLISTED_TOOLS, validate_tool_arguments


def test_core_tools_allowlist():
    assert "search_products" in ALLOWLISTED_TOOLS
    assert "get_product" in ALLOWLISTED_TOOLS
    assert "get_offer" in ALLOWLISTED_TOOLS
    assert "check_inventory" in ALLOWLISTED_TOOLS
    assert "create_checkout" in ALLOWLISTED_TOOLS
    assert "request_authorization" in ALLOWLISTED_TOOLS
    assert "create_payment" in ALLOWLISTED_TOOLS
    assert "check_payment" in ALLOWLISTED_TOOLS
    assert "search_web" in ALLOWLISTED_TOOLS

    # Calculate tool is deliberately removed in v1
    assert "calculate" not in ALLOWLISTED_TOOLS


def test_calculate_tool_is_blocked():
    with pytest.raises(DomainError) as exc:
        validate_tool_arguments("calculate", {})
    assert exc.value.code == ErrorCode.TOOL_BLOCKED


def test_validate_tool_arguments_core_tools():
    # Valid product lookup
    val = validate_tool_arguments("get_product", {"product_id": "prd_123"})
    assert val.tool_name == "get_product"
    assert val.product_id == "prd_123"

    # Missing required argument
    with pytest.raises(DomainError) as exc:
        validate_tool_arguments("get_product", {})
    assert exc.value.code == ErrorCode.VALIDATION_ERROR

    # Valid checkout creation
    chk_val = validate_tool_arguments("create_checkout", {"offer_id": "off_123", "quantity": 2})
    assert chk_val.tool_name == "create_checkout"
    assert chk_val.offer_id == "off_123"
    assert chk_val.quantity == 2


def test_validate_tool_arguments_ssRF_blocking():
    # Cloud metadata address blocked
    with pytest.raises(DomainError) as exc:
        validate_tool_arguments("open_url", {"url": "http://169.254.169.254/latest/meta-data/"})
    assert exc.value.code == ErrorCode.FORBIDDEN

    # Localhost / loopback blocked
    with pytest.raises(DomainError) as exc:
        validate_tool_arguments("open_url", {"url": "http://localhost:8000/internal"})
    assert exc.value.code == ErrorCode.FORBIDDEN

    with pytest.raises(DomainError) as exc:
        validate_tool_arguments("extract_page", {"url": "http://127.0.0.1:22"})
    assert exc.value.code == ErrorCode.FORBIDDEN

    # Safe public URL passes
    safe_val = validate_tool_arguments(
        "open_url", {"url": "https://developer.lenovo.com/psref/ideapad5.pdf"}
    )
    assert safe_val.url == "https://developer.lenovo.com/psref/ideapad5.pdf"
