"""Unit tests for deterministic policy evaluation (Task 16, Requirement 12, Properties 17, 18, 19)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.errors.registry import ErrorCode
from services.policy.engine import (
    BuyerPolicyRules,
    MerchantPolicyRules,
    PolicyInputs,
    evaluate_policy,
)


def _base_inputs(
    amount_minor: int = 4999900,
    category_id: str = "laptop",
    merchant_id: str = "merch_1",
    buyer_id: str = "buy_1",
    offer_status: str = "active",
    expires_in_hours: int = 24,
    available_quantity: int = 10,
    policy_version: str = "1.0",
) -> PolicyInputs:
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    return PolicyInputs(
        buyer_id=buyer_id,
        merchant_id=merchant_id,
        category_id=category_id,
        amount_minor=amount_minor,
        currency="INR",
        offer_status=offer_status,
        offer_expires_at=now + timedelta(hours=expires_in_hours),
        available_quantity=available_quantity,
        policy_version=policy_version,
    )


def _base_rules(
    merchant_max: int = 10000000,
    merchant_auto: int = 500000,
    buyer_max: int = 10000000,
    buyer_auto: int = 500000,
    blocked_categories: tuple[str, ...] = (),
    allowed_categories: tuple[str, ...] = (),
    allowed_merchants: tuple[str, ...] = (),
    version: str = "1.0",
) -> tuple[MerchantPolicyRules, BuyerPolicyRules]:
    m_rules = MerchantPolicyRules(
        merchant_id="merch_1",
        version=version,
        max_transaction_minor=merchant_max,
        auto_approval_limit_minor=merchant_auto,
        blocked_categories=blocked_categories,
        allowed_categories=allowed_categories,
    )
    b_rules = BuyerPolicyRules(
        buyer_id="buy_1",
        version=version,
        max_transaction_minor=buyer_max,
        auto_approval_limit_minor=buyer_auto,
        allowed_merchants=allowed_merchants,
    )
    return m_rules, b_rules


# ---------------------------------------------------------------------------
# Property 17: Pure evaluation is idempotent and deterministic
# ---------------------------------------------------------------------------


def test_property_17_pure_evaluation_is_deterministic():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(amount_minor=300000)
    m_rules, b_rules = _base_rules()

    d1 = evaluate_policy(inputs, m_rules, b_rules, now=now)
    d2 = evaluate_policy(inputs, m_rules, b_rules, now=now)

    assert d1.decision == d2.decision
    assert d1.reason_code == d2.reason_code
    assert d1.inputs_hash == d2.inputs_hash


# ---------------------------------------------------------------------------
# Property 18 & 19: Limits and reason codes
# ---------------------------------------------------------------------------


def test_amount_within_auto_limit_allows():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(amount_minor=300000)  # 3,000 INR (auto limit is 5,000)
    m_rules, b_rules = _base_rules(merchant_auto=500000, buyer_auto=500000)

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "ALLOW"
    assert result.reason_code == "OK"


def test_amount_above_auto_limit_requires_approval():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(amount_minor=600000)  # 6,000 INR (auto limit is 5,000)
    m_rules, b_rules = _base_rules(merchant_auto=500000, buyer_auto=500000)

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "REQUIRE_APPROVAL"
    assert result.reason_code == ErrorCode.AMOUNT_ABOVE_AUTO_LIMIT.value


def test_property_19_amount_above_max_limit_is_never_allow():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(amount_minor=15000000)  # 1.5 lakhs (max is 1 lakh)
    m_rules, b_rules = _base_rules(merchant_max=10000000, buyer_max=10000000)

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "BLOCK"
    assert result.reason_code == ErrorCode.AMOUNT_ABOVE_MAX_LIMIT.value


def test_blocked_category_is_never_require_approval():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(category_id="weapons", amount_minor=300000)
    m_rules, b_rules = _base_rules(blocked_categories=("weapons",))

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "BLOCK"
    assert result.reason_code == ErrorCode.CATEGORY_NOT_ALLOWED.value


def test_blocked_merchant_blocks():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(merchant_id="unapproved_merchant")
    m_rules, b_rules = _base_rules(allowed_merchants=("trusted_merchant_only",))

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "BLOCK"
    assert result.reason_code == ErrorCode.MERCHANT_NOT_ALLOWED.value


def test_expired_offer_blocks():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(expires_in_hours=-1)
    m_rules, b_rules = _base_rules()

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "BLOCK"
    assert result.reason_code == ErrorCode.OFFER_EXPIRED.value


def test_out_of_stock_blocks():
    now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    inputs = _base_inputs(available_quantity=0)
    m_rules, b_rules = _base_rules()

    result = evaluate_policy(inputs, m_rules, b_rules, now=now)
    assert result.decision == "BLOCK"
    assert result.reason_code == ErrorCode.INVENTORY_UNAVAILABLE.value


def _policy_test_app():
    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import apps.api.db
    from apps.api.db import Base, get_db
    from apps.api.main import create_app

    @compiles(JSONB, "sqlite")
    def _compile_jsonb(type_, compiler, **kw):
        return "JSON"

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            apps.api.db.text(
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
        conn.commit()

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    apps.api.db._SESSION_FACTORY = session_factory

    app = create_app()
    app.state.session_factory = session_factory

    def _override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    return app


def test_merchant_rules_api_get_and_put():
    """Verify GET and PUT /api/v1/merchant/rules read and persist merchant rules."""
    from fastapi.testclient import TestClient

    app = _policy_test_app()
    client = TestClient(app)

    # 1. Start merchant admin session
    session_res = client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "mer_demo_electronics"},
    )
    assert session_res.status_code == 200

    # 2. Read initial rules
    get_res = client.get("/api/v1/merchant/rules")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["ok"] is True
    assert "rules" in data["data"]
    assert data["data"]["rules"]["merchant_id"] == "mer_demo_electronics"

    # 3. Update rules
    put_res = client.put(
        "/api/v1/merchant/rules",
        json={
            "max_transaction_minor": 15000000,
            "auto_approval_limit_minor": 750000,
            "max_discount_basis_points": 1000,
            "allowed_categories": ["laptops", "smartphones"],
            "blocked_categories": ["gift_cards"],
            "allow_out_of_stock": False,
        },
    )
    assert put_res.status_code == 200
    updated_data = put_res.json()
    assert updated_data["ok"] is True
    assert updated_data["data"]["rules"]["max_transaction_minor"] == 15000000
    assert updated_data["data"]["rules"]["auto_approval_limit_minor"] == 750000
    assert updated_data["data"]["rules"]["allowed_categories"] == ["laptops", "smartphones"]
    assert updated_data["data"]["rules"]["blocked_categories"] == ["gift_cards"]

    # 4. Verify GET returns persisted rules
    verify_res = client.get("/api/v1/merchant/rules")
    assert verify_res.status_code == 200
    assert verify_res.json()["data"]["rules"]["max_transaction_minor"] == 15000000


def test_merchant_rules_unauthenticated_refused():
    """Unauthenticated calls to /api/v1/merchant/rules must return 401 Unauthorized (BUG-20, BUG-21)."""
    from fastapi.testclient import TestClient

    from apps.api.main import create_app

    unauth_client = TestClient(create_app())
    get_res = unauth_client.get("/api/v1/merchant/rules")
    assert get_res.status_code == 401

    put_res = unauth_client.put(
        "/api/v1/merchant/rules",
        json={"max_transaction_minor": 999999999, "auto_approval_limit_minor": 999999999},
    )
    assert put_res.status_code == 401


def test_merchant_rules_cross_tenant_refused(client):
    """A merchant admin cannot read or write another merchant's policy rules (BUG-20, BUG-21)."""
    # 1. Start session for merchant A
    session_res = client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "mer_tenant_a"},
    )
    assert session_res.status_code == 200

    # 2. Attempt cross-tenant read of merchant B
    get_cross = client.get("/api/v1/merchant/rules?merchant_id=mer_tenant_b")
    assert get_cross.status_code == 403

    # 3. Attempt cross-tenant write of merchant B
    put_cross = client.put(
        "/api/v1/merchant/rules?merchant_id=mer_tenant_b",
        json={"max_transaction_minor": 999999999, "auto_approval_limit_minor": 999999999},
    )
    assert put_cross.status_code == 403


def test_intent_deterministic_extraction_varied_llm_formats():
    """Verify IntentValidator normalizes varying LLM key outputs and fallback regex deterministically."""
    from services.agent.intent import IntentValidator

    # Hero prompt
    hero_prompt = "I need a laptop under 80,000 INR with 16GB RAM"

    # Format 1: Direct keys
    intent1 = IntentValidator.validate_dict(
        {"query": "laptop", "max_budget": 80000, "min_memory_gb": 16, "category": "laptop"},
        prompt=hero_prompt,
    )
    assert intent1.financial.budget_minor == 8000000
    assert intent1.min_memory_gb == 16
    assert intent1.category == "laptop"

    # Format 2: Varied alias keys (max_price_limit, ram_gb)
    intent2 = IntentValidator.validate_dict(
        {"product": "laptop", "max_price_limit": 80000, "ram_gb": 16},
        prompt=hero_prompt,
    )
    assert intent2.financial.budget_minor == 8000000
    assert intent2.min_memory_gb == 16
    assert intent2.category == "laptop"

    # Format 3: Empty dictionary (relies on deterministic prompt regex extraction)
    intent3 = IntentValidator.validate_dict(
        {},
        prompt=hero_prompt,
    )
    assert intent3.financial.budget_minor == 8000000
    assert intent3.min_memory_gb == 16
    assert intent3.category == "laptop"


def test_merchant_rules_put_returns_503_when_datastore_is_down(client, app):
    """When PostgreSQL is down or unreachable, PUT /merchant/rules must return 503 SERVICE_UNAVAILABLE (BUG-26)."""
    from unittest.mock import MagicMock

    from sqlalchemy.exc import OperationalError

    from apps.api.db import get_db

    client.post(
        "/api/v1/auth/session",
        json={"role": "merchant_admin", "merchant_id": "mer_demo_electronics"},
    )

    # 1. Simulate datastore connection failure / OperationalError during statement execution
    mock_session = MagicMock()
    mock_session.execute.side_effect = OperationalError(
        "SELECT 1", {}, Exception("connection refused")
    )
    mock_session.query.side_effect = OperationalError(
        "SELECT 1", {}, Exception("connection refused")
    )
    mock_session.commit.side_effect = OperationalError(
        "COMMIT", {}, Exception("connection refused")
    )

    def mock_broken_db():
        yield mock_session

    app.dependency_overrides[get_db] = mock_broken_db

    res1 = client.put(
        "/api/v1/merchant/rules",
        json={"max_transaction_minor": 1000000, "auto_approval_limit_minor": 500000},
    )
    assert res1.status_code == 503
    assert res1.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    res_alias = client.put(
        "/merchant/rules",
        json={"max_transaction_minor": 1000000, "auto_approval_limit_minor": 500000},
    )
    assert res_alias.status_code == 503
    assert res_alias.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # 2. Simulate database down at session acquisition time (get_db raises OperationalError)
    def mock_dead_db():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        yield

    app.dependency_overrides[get_db] = mock_dead_db

    res2 = client.put(
        "/api/v1/merchant/rules",
        json={"max_transaction_minor": 1000000, "auto_approval_limit_minor": 500000},
    )
    assert res2.status_code == 503
    assert res2.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
