"""Unit tests for the Campaign Orchestrator and Merchant Growth Engine (Track 01)."""

from __future__ import annotations

import pytest

from packages.security.principals import Role, Scope
from packages.security.tokens import issue_access_token
from services.campaigns.models import (
    CampaignProductItem,
    CampaignStatus,
    PolicyDecision,
)
from services.campaigns.policy import CampaignPolicyEngine
from services.campaigns.service import CampaignService


@pytest.fixture
def auth_headers(settings):
    issued = issue_access_token(
        secret=settings.jwt_secret,
        subject="test_merchant",
        role=Role.MERCHANT_ADMIN,
        merchant_id="merch_1",
        buyer_id=None,
        ttl_seconds=3600,
        scopes=[Scope.CATALOG_READ],
    )
    return {"Authorization": f"Bearer {issued.token}"}


def test_propose_campaign_audio_picks():
    service = CampaignService()
    campaign = service.propose_campaign(
        merchant_id="merch_1",
        goal_prompt="Increase sales of slow-moving headphones this weekend without discounting more than 10%",
        max_discount_pct=10.0,
        duration_days=3,
    )

    assert campaign.campaign_id.startswith("cmp_")
    assert campaign.target_category == "audio"
    assert campaign.status == CampaignStatus.PROPOSED
    assert len(campaign.products) > 0
    assert campaign.products[0].discount_pct <= 10.0
    assert campaign.products[0].available_inventory >= 5
    assert campaign.products[0].margin_pct_preserved >= 15.0
    assert campaign.policy_check.decision == PolicyDecision.REQUIRE_APPROVAL
    assert not campaign.policy_check.violated_rules
    assert campaign.estimated_sales_lift_pct > 0


def test_campaign_policy_blocks_excessive_discount():
    policy_engine = CampaignPolicyEngine()

    item = CampaignProductItem(
        product_id="prd_test_1",
        offer_id="off_test_1",
        title="Test Item",
        category="audio",
        original_price_minor=100000,
        discount_pct=25.0,  # 25% > 10%
        promotional_price_minor=75000,
        available_inventory=10,
        margin_pct_preserved=30.0,
        cross_sell_pairings=[],
        selection_rationale="Test",
    )

    result = policy_engine.evaluate_campaign(
        max_discount_pct=25.0,
        duration_days=3,
        products=[item],
        merchant_policy_max_discount=10.0,
    )

    assert result.decision == PolicyDecision.BLOCK
    assert any("DISCOUNT_CEILING_EXCEEDED" in v for v in result.violated_rules)


def test_campaign_policy_blocks_insufficient_inventory():
    policy_engine = CampaignPolicyEngine()

    item = CampaignProductItem(
        product_id="prd_test_1",
        offer_id="off_test_1",
        title="Low Stock Item",
        category="audio",
        original_price_minor=100000,
        discount_pct=10.0,
        promotional_price_minor=90000,
        available_inventory=1,  # Only 1 unit < MIN_INVENTORY_THRESHOLD (3)
        margin_pct_preserved=30.0,
        cross_sell_pairings=[],
        selection_rationale="Test",
    )

    result = policy_engine.evaluate_campaign(
        max_discount_pct=10.0,
        duration_days=3,
        products=[item],
        merchant_policy_max_discount=10.0,
    )

    assert result.decision == PolicyDecision.BLOCK
    assert any("INSUFFICIENT_INVENTORY" in v for v in result.violated_rules)


def test_campaign_policy_blocks_margin_breach():
    policy_engine = CampaignPolicyEngine()

    item = CampaignProductItem(
        product_id="prd_test_1",
        offer_id="off_test_1",
        title="Thin Margin Item",
        category="audio",
        original_price_minor=100000,
        discount_pct=10.0,
        promotional_price_minor=90000,
        available_inventory=10,
        margin_pct_preserved=8.0,  # 8% < MIN_MARGIN_FLOOR (15%)
        cross_sell_pairings=[],
        selection_rationale="Test",
    )

    result = policy_engine.evaluate_campaign(
        max_discount_pct=10.0,
        duration_days=3,
        products=[item],
        merchant_policy_max_discount=10.0,
    )

    assert result.decision == PolicyDecision.BLOCK
    assert any("MARGIN_FLOOR_BREACH" in v for v in result.violated_rules)


def test_campaign_lifecycle_transitions():
    service = CampaignService()
    campaign = service.propose_campaign(
        merchant_id="merch_1",
        goal_prompt="Promote laptop developer bundle with 5% discount",
        max_discount_pct=5.0,
        duration_days=5,
        category="laptops",
    )

    assert campaign.status == CampaignStatus.PROPOSED
    assert campaign.approved_at is None

    # Merchant approves
    approved = service.approve_campaign(campaign.campaign_id)
    assert approved.status == CampaignStatus.APPROVED
    assert approved.approved_at is not None

    # Merchant activates
    active = service.activate_campaign(campaign.campaign_id)
    assert active.status == CampaignStatus.ACTIVE
    assert active.activated_at is not None


def test_campaign_api_workflow(client, auth_headers):
    # 1. Propose campaign
    res = client.post(
        "/api/v1/campaigns/propose",
        json={
            "goal_prompt": "Boost sales of slow-moving headphones this weekend without discounting more than 10%",
            "max_discount_pct": 10.0,
            "duration_days": 3,
            "budget_minor": 5000000,
        },
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    campaign_id = data["campaign_id"]
    assert data["status"] == "proposed"
    assert data["policy_check"]["decision"] == "require_approval"

    # 2. List campaigns
    res = client.get("/api/v1/campaigns", headers=auth_headers)
    assert res.status_code == 200
    list_data = res.json()["data"]["campaigns"]
    assert any(c["campaign_id"] == campaign_id for c in list_data)

    # 3. Approve campaign
    res = client.post(f"/api/v1/campaigns/{campaign_id}/approve", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "approved"

    # 4. Activate campaign
    res = client.post(f"/api/v1/campaigns/{campaign_id}/activate", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "active"

    # 5. Check analytics
    res = client.get("/api/v1/campaigns/analytics", headers=auth_headers)
    assert res.status_code == 200
    analytics = res.json()["data"]
    assert analytics["active_campaigns"] >= 1
    assert analytics["incremental_revenue_minor"] > 0


def test_get_campaign_returns_404_when_missing_or_out_of_tenant(client, auth_headers, settings):
    # Missing campaign
    res = client.get("/api/v1/campaigns/cmp_nonexistent_12345", headers=auth_headers)
    assert res.status_code == 404
    assert res.json()["ok"] is False
    assert res.json()["error"]["code"] == "NOT_FOUND"

    # Out of tenant campaign
    other_auth = issue_access_token(
        secret=settings.jwt_secret,
        subject="other_merchant",
        role=Role.MERCHANT_ADMIN,
        merchant_id="merch_other_999",
        buyer_id=None,
        ttl_seconds=3600,
        scopes=[Scope.CATALOG_READ],
    )
    res_other = client.post(
        "/api/v1/campaigns/propose",
        json={"goal_prompt": "Audio promo", "max_discount_pct": 5.0},
        headers={"Authorization": f"Bearer {other_auth.token}"},
    )
    other_cid = res_other.json()["data"]["campaign_id"]

    # merch_1 accessing merch_other_999 campaign gets 404
    res_unscoped = client.get(f"/api/v1/campaigns/{other_cid}", headers=auth_headers)
    assert res_unscoped.status_code == 404
    assert res_unscoped.json()["ok"] is False


def test_buyer_role_cannot_access_campaigns(client, settings):
    buyer_token = issue_access_token(
        secret=settings.jwt_secret,
        subject="buyer_unauthorized",
        role=Role.BUYER,
        merchant_id="merch_1",
        buyer_id="buyer_1",
        ttl_seconds=3600,
        scopes=[Scope.CATALOG_READ],
    )
    res = client.get(
        "/api/v1/campaigns",
        headers={"Authorization": f"Bearer {buyer_token.token}"},
    )
    assert res.status_code == 403
    assert res.json()["ok"] is False
    assert res.json()["error"]["code"] == "FORBIDDEN"


def test_campaign_repository_memory_store_tenant_isolation():
    from services.campaigns.repository import CampaignRepository

    repo = CampaignRepository(redis_url=None)
    service = CampaignService(repository=repo)
    c1 = service.propose_campaign(merchant_id="merch_alpha", goal_prompt="Audio test")

    # Correct merchant retrieves
    assert repo.get("merch_alpha", c1.campaign_id) is not None
    # Cross tenant lookup returns None
    assert repo.get("merch_beta", c1.campaign_id) is None
