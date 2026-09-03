"""Unit tests for Phase F: Capability document, tool arguments validation, and agent surface (Tasks 23-25)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from apps.api.config import Settings
from apps.api.routers.capability import build_capability_document
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import CapabilityDocumentV1, ToolArgumentsV1
from services.agent.tools import ALLOWLISTED_TOOLS, validate_tool_arguments
from services.catalog.models import MerchantRules

# ---------------------------------------------------------------------------
# Task 23: Capability Document
# ---------------------------------------------------------------------------


def test_capability_document_schema_validation():
    doc = build_capability_document()
    assert isinstance(doc, CapabilityDocumentV1)
    assert doc.schema_version == "1.0"
    assert doc.authentication.method == "api_key_exchange"
    assert doc.limits.currency == "INR"
    assert doc.external_protocol_certification == "none"
    assert "not endorsed or certified by Anthropic, OpenAI, Google" in doc.protocol_notice


def _session_returning(rules: MerchantRules | None) -> MagicMock:
    """A session whose ``execute(...).scalar_one_or_none()`` yields ``rules``."""
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = rules
    return session


def test_capability_document_reflects_live_rules():
    """Stored rules win over configuration, which is what makes the document live."""
    mock_rules = MerchantRules(
        merchant_id="mrc_demo_electronics",
        version="2.0",
        max_transaction_minor=20000000,
        auto_approval_limit_minor=1000000,
        max_discount_basis_points=500,
        allowed_categories=["laptop", "audio"],
        blocked_categories=["weapons"],
    )

    doc = build_capability_document(session=_session_returning(mock_rules))
    assert doc.limits.max_transaction_minor == 20000000
    assert doc.limits.auto_approval_limit_minor == 1000000
    assert doc.policy.allowed_categories == ["laptop", "audio"]
    assert doc.policy.blocked_categories == ["weapons"]


def test_capability_limits_fall_back_to_enforced_configuration():
    """With no stored rules the document must advertise the configured ceilings.

    This is the defect the endpoint shipped with: the fallbacks were the literals
    ``10000000`` and ``500000`` while ``Settings`` carried ``7000000`` and
    ``500000``. An external agent therefore read a transaction ceiling forty
    percent above the one the policy engine would enforce.
    """
    settings = Settings()

    doc = build_capability_document(session=_session_returning(None), settings=settings)

    assert doc.limits.max_transaction_minor == settings.max_transaction_amount_minor
    assert doc.limits.auto_approval_limit_minor == settings.auto_approval_limit_minor
    assert doc.limits.currency == settings.default_currency
    assert doc.limits.max_results == settings.max_search_results


def test_capability_document_queries_the_configured_merchant():
    """The lookup used a merchant id no other part of the system serves, so it
    never matched and every request silently used the fallback figures."""
    settings = Settings()
    session = _session_returning(None)

    build_capability_document(session=session, settings=settings)

    # The statement renders the tenant as a bound placeholder, so the value has
    # to come from the compiled parameters rather than from the SQL text.
    statement = session.execute.call_args.args[0]
    bound = statement.compile().params
    assert settings.default_merchant_id in bound.values()


def test_capability_document_survives_an_unreadable_datastore():
    """A datastore failure must still answer, from configuration, without raising."""
    settings = Settings()
    session = MagicMock()
    session.execute.side_effect = SQLAlchemyError("connection refused")

    doc = build_capability_document(session=session, settings=settings)

    assert doc.limits.max_transaction_minor == settings.max_transaction_amount_minor


def test_capability_document_does_not_mask_a_programming_error():
    """Only datastore failures are recoverable. A bug in the projection must
    surface rather than be answered with plausible-looking defaults."""
    session = MagicMock()
    session.execute.side_effect = TypeError("projection bug")

    with pytest.raises(TypeError):
        build_capability_document(session=session, settings=Settings())


# ---------------------------------------------------------------------------
# Task 24: Tool Argument Validation
# ---------------------------------------------------------------------------


def test_allowlisted_tool_arguments_validation():
    for tool_name in ALLOWLISTED_TOOLS:
        args = {"query": "laptop"} if "search" in tool_name else {}
        if tool_name == "get_product":
            args = {"product_id": "prd_1"}
        elif tool_name == "get_offer":
            args = {"offer_id": "off_1"}
        elif tool_name == "create_checkout":
            args = {"offer_id": "off_1", "quantity": 2}
        elif tool_name == "request_authorization":
            args = {"checkout_id": "chk_1"}
        elif tool_name == "create_payment":
            args = {"checkout_id": "chk_1", "authorization_id": "ath_1"}
        elif tool_name in ("open_url", "extract_page"):
            args = {"url": "https://example.com/product/docs"}

        val = validate_tool_arguments(tool_name, args)
        assert isinstance(val, ToolArgumentsV1)
        assert val.tool_name == tool_name


def test_unallowlisted_tool_is_blocked():
    with pytest.raises(DomainError) as exc_info:
        validate_tool_arguments("execute_arbitrary_shell_command", {})
    assert exc_info.value.code == ErrorCode.TOOL_BLOCKED


def test_missing_required_arguments_rejected():
    with pytest.raises(DomainError) as exc_info:
        validate_tool_arguments("create_checkout", {})
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


# ---------------------------------------------------------------------------
# Task 25 & BUG-33: Bounded Agent Tool Execution & Export Verification
# ---------------------------------------------------------------------------


def test_agent_router_exports_symbols():
    from apps.api.routers import agent

    assert hasattr(agent, "execute_tool")
    assert hasattr(agent, "ALLOWLISTED_TOOLS")
    assert hasattr(agent, "validate_tool_arguments")
    assert len(agent.ALLOWLISTED_TOOLS) == 14
    assert "calculate" not in agent.ALLOWLISTED_TOOLS


def test_execute_tool_reaches_facade_and_enforces_boundaries():
    from apps.api.routers.agent import execute_tool
    from tests.fake_commerce import FakeCommerceFacade, make_offer

    fake_commerce = FakeCommerceFacade(offers=[make_offer("off_100")])

    # 1. Search products tool execution
    res_search = execute_tool(
        tool_name="search_products",
        arguments={"query": "laptop"},
        merchant_id="mrc_1",
        buyer_id="buy_1",
        facade=fake_commerce,
    )
    assert res_search.tool_name == "search_products"
    assert res_search.result["count"] == 1
    assert res_search.is_state_changing is False
    assert res_search.requires_confirmation is False

    # 2. State changing tool without confirmation is gated
    res_unconfirmed = execute_tool(
        tool_name="create_checkout",
        arguments={"offer_id": "off_100", "quantity": 1},
        merchant_id="mrc_1",
        buyer_id="buy_1",
        confirmed=False,
        facade=fake_commerce,
    )
    assert res_unconfirmed.is_state_changing is True
    assert res_unconfirmed.requires_confirmation is True
    assert res_unconfirmed.result["status"] == "confirmation_required"
    assert "create_checkout" not in fake_commerce.call_names

    # 3. Confirmed state changing tool executes through facade
    res_confirmed = execute_tool(
        tool_name="create_checkout",
        arguments={"offer_id": "off_100", "quantity": 1},
        merchant_id="mrc_1",
        buyer_id="buy_1",
        confirmed=True,
        facade=fake_commerce,
    )
    assert res_confirmed.requires_confirmation is False
    assert res_confirmed.result["checkout"]["checkout_id"] == "chk_1"
    assert "create_checkout" in fake_commerce.call_names

    # 4. Calculate tool is blocked
    with pytest.raises(DomainError) as exc_calc:
        execute_tool(
            tool_name="calculate",
            arguments={"expression": "250 * 4"},
            merchant_id="mrc_1",
            buyer_id="buy_1",
            facade=fake_commerce,
        )
    assert exc_calc.value.code == ErrorCode.TOOL_BLOCKED

    # 5. Anti-SSRF URL protection
    with pytest.raises(DomainError) as exc_ssrf:
        execute_tool(
            tool_name="open_url",
            arguments={"url": "http://169.254.169.254/latest/meta-data/"},
            merchant_id="mrc_1",
            buyer_id="buy_1",
            facade=fake_commerce,
        )
    assert exc_ssrf.value.code == ErrorCode.FORBIDDEN


def test_agent_order_lookup_enforces_tenant_and_buyer_scoping():
    """Agent order lookup rejects cross-tenant or cross-buyer order queries (BUG-38)."""
    from apps.api.routers.agent import agent_get_order
    from packages.security.principals import Principal, Role, Scope
    from services.orders.models import Order

    session = MagicMock()
    # Principal is buyer_1 in merchant_1
    principal = Principal(
        subject="sub_1",
        role=Role.BUYER,
        merchant_id="mrc_1",
        buyer_id="buy_1",
        scopes=frozenset({Scope.CHECKOUT_WRITE}),
    )

    # 1. Order belonging to buyer_1 in mrc_1 succeeds
    order_own = Order(
        order_id="ord_own",
        order_number="ORD-OWN",
        checkout_id="chk_1",
        payment_id="pay_1",
        buyer_id="buy_1",
        merchant_id="mrc_1",
        status="confirmed",
        total_minor=500000,
        currency="INR",
    )
    session.execute.return_value.scalars.return_value.all.return_value = [order_own]
    res = agent_get_order("ord_own", principal=principal, session=session)
    assert res["data"]["order"]["order_id"] == "ord_own"

    # 2. Order belonging to rival buyer / rival merchant returns NOT_FOUND (does not leak existence)
    session.execute.return_value.scalars.return_value.all.return_value = []
    with pytest.raises(DomainError) as exc_info:
        agent_get_order("ord_rival", principal=principal, session=session)
    assert exc_info.value.code == ErrorCode.NOT_FOUND

    # 3. Agent without buyer_id raises FORBIDDEN
    principal_no_buyer = Principal(
        subject="sub_2",
        role=Role.MERCHANT_ADMIN,
        merchant_id="mrc_1",
        buyer_id=None,
        scopes=frozenset({Scope.CATALOG_READ}),
    )
    with pytest.raises(DomainError) as exc_info2:
        agent_get_order("ord_own", principal=principal_no_buyer, session=session)
    assert exc_info2.value.code == ErrorCode.FORBIDDEN
