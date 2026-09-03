"""Campaign Orchestrator API Router (Track 01 Merchant Growth Agent)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.auth import require_roles
from apps.api.envelope import success
from packages.security.principals import Principal, Role
from services.campaigns.models import Campaign
from services.campaigns.service import CampaignService

router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])
MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN)),
]


class ProposeCampaignRequest(BaseModel):
    goal_prompt: str
    max_discount_pct: float = Field(default=10.0, ge=1.0, le=50.0)
    duration_days: int = Field(default=3, ge=1, le=14)
    budget_minor: int = Field(default=5000000, ge=100000)
    category: str | None = None


class RejectCampaignRequest(BaseModel):
    reason: str = Field(default="Merchant declined proposal")


def _campaign_to_dict(c: Campaign) -> dict[str, Any]:
    return {
        "campaign_id": c.campaign_id,
        "merchant_id": c.merchant_id,
        "title": c.title,
        "goal": c.goal,
        "target_category": c.target_category,
        "status": c.status.value,
        "max_discount_pct": c.max_discount_pct,
        "duration_days": c.duration_days,
        "budget_minor": c.budget_minor,
        "products": [
            {
                "product_id": p.product_id,
                "offer_id": p.offer_id,
                "title": p.title,
                "category": p.category,
                "original_price_minor": p.original_price_minor,
                "discount_pct": p.discount_pct,
                "promotional_price_minor": p.promotional_price_minor,
                "available_inventory": p.available_inventory,
                "margin_pct_preserved": p.margin_pct_preserved,
                "cross_sell_pairings": p.cross_sell_pairings,
                "selection_rationale": p.selection_rationale,
            }
            for p in c.products
        ],
        "policy_check": {
            "decision": c.policy_check.decision.value,
            "passed_rules": c.policy_check.passed_rules,
            "violated_rules": c.policy_check.violated_rules,
            "reason": c.policy_check.reason,
        },
        "estimated_sales_lift_pct": c.estimated_sales_lift_pct,
        "estimated_revenue_minor": c.estimated_revenue_minor,
        "estimated_discount_cost_minor": c.estimated_discount_cost_minor,
        "created_at": c.created_at,
        "reviewed_at": c.reviewed_at,
        "approved_at": c.approved_at,
        "activated_at": c.activated_at,
        "paused_at": c.paused_at,
        "completed_at": c.completed_at,
        "rejection_reason": c.rejection_reason,
    }


def _get_optional_db() -> Any:
    """Yield a DB session when the database is reachable, or None otherwise."""
    from contextlib import suppress

    try:
        from apps.api.db import get_db

        gen = get_db()
        session = next(gen)
    except Exception:
        yield None
        return

    try:
        yield session
    finally:
        with suppress(Exception):
            next(gen, None)


OptionalDatabaseSession = Annotated[Any, Depends(_get_optional_db)]


@router.post("/propose")
def propose_campaign(
    request: ProposeCampaignRequest,
    principal: MerchantPrincipal,
    session: OptionalDatabaseSession = None,
) -> dict[str, Any]:
    """Generate a structured campaign proposal with AI candidate identification & deterministic policy checks."""
    service = CampaignService()
    campaign = service.propose_campaign(
        session,
        merchant_id=principal.merchant_id,
        goal_prompt=request.goal_prompt,
        max_discount_pct=request.max_discount_pct,
        duration_days=request.duration_days,
        budget_minor=request.budget_minor,
        category=request.category,
    )
    return success(_campaign_to_dict(campaign))


@router.get("")
def list_campaigns(
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """List all proposed, approved, active, and completed campaigns for this merchant."""
    service = CampaignService()
    campaigns = service.list_campaigns(merchant_id=principal.merchant_id)
    return success({"campaigns": [_campaign_to_dict(c) for c in campaigns]})


@router.get("/analytics")
def get_campaign_analytics(
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Aggregate campaign performance, sales lift, incremental revenue, and ROI metrics."""
    service = CampaignService()
    return success(service.get_analytics(merchant_id=principal.merchant_id))


@router.get("/{campaign_id}")
def get_campaign(
    campaign_id: str,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Retrieve detailed campaign proposal breakdown."""
    service = CampaignService()
    c = service.get_campaign(campaign_id, merchant_id=principal.merchant_id)
    if not c or c.merchant_id != principal.merchant_id:
        from packages.errors.exceptions import DomainError
        from packages.errors.registry import ErrorCode

        raise DomainError("Campaign not found", code=ErrorCode.NOT_FOUND)
    return success(_campaign_to_dict(c))


@router.post("/{campaign_id}/approve")
def approve_campaign(
    campaign_id: str,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Approve a proposed promotional campaign (Merchant authorization gate)."""
    service = CampaignService()
    c = service.approve_campaign(campaign_id, merchant_id=principal.merchant_id)
    return success(_campaign_to_dict(c))


@router.post("/{campaign_id}/submit-for-review")
def submit_campaign_for_review(
    campaign_id: str,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Submit a draft / proposed campaign for human review (DRAFT|PROPOSED → REVIEW).

    The AI is allowed to *propose*; the merchant operator must explicitly
    *submit* a campaign into the review queue. Once in REVIEW, the campaign
    can still be approved, rejected, or sent back to DRAFT.
    """
    service = CampaignService()
    c = service.submit_for_review(campaign_id, merchant_id=principal.merchant_id)
    return success(_campaign_to_dict(c))


@router.post("/{campaign_id}/reject")
def reject_campaign(
    campaign_id: str,
    request: RejectCampaignRequest,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Reject a proposed campaign."""
    service = CampaignService()
    c = service.reject_campaign(
        campaign_id, reason=request.reason, merchant_id=principal.merchant_id
    )
    return success(_campaign_to_dict(c))


@router.post("/{campaign_id}/activate")
def activate_campaign(
    campaign_id: str,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Activate an approved campaign into live merchant production."""
    service = CampaignService()
    c = service.activate_campaign(campaign_id, merchant_id=principal.merchant_id)
    return success(_campaign_to_dict(c))


@router.post("/{campaign_id}/pause")
def pause_campaign(
    campaign_id: str,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Pause an active campaign temporarily (ACTIVE → PAUSED)."""
    service = CampaignService()
    c = service.pause_campaign(campaign_id, merchant_id=principal.merchant_id)
    return success(_campaign_to_dict(c))


@router.post("/{campaign_id}/complete")
def complete_campaign(
    campaign_id: str,
    principal: MerchantPrincipal,
) -> dict[str, Any]:
    """Mark an active or paused campaign as concluded (ACTIVE/PAUSED → COMPLETED)."""
    service = CampaignService()
    c = service.complete_campaign(campaign_id, merchant_id=principal.merchant_id)
    return success(_campaign_to_dict(c))
