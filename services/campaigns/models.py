"""Data structures for Campaign Orchestrator and Merchant Growth Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class PolicyCheckResult:
    decision: PolicyDecision
    passed_rules: list[str]
    violated_rules: list[str]
    reason: str


@dataclass(frozen=True, slots=True)
class CampaignProductItem:
    product_id: str
    offer_id: str
    title: str
    category: str
    original_price_minor: int
    discount_pct: float
    promotional_price_minor: int
    available_inventory: int
    margin_pct_preserved: float
    cross_sell_pairings: list[str]
    selection_rationale: str


@dataclass
class Campaign:
    campaign_id: str
    merchant_id: str
    title: str
    goal: str
    target_category: str
    status: CampaignStatus
    max_discount_pct: float
    duration_days: int
    budget_minor: int
    products: list[CampaignProductItem]
    policy_check: PolicyCheckResult
    estimated_sales_lift_pct: float
    estimated_revenue_minor: int
    estimated_discount_cost_minor: int
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reviewed_at: str | None = None
    approved_at: str | None = None
    activated_at: str | None = None
    paused_at: str | None = None
    completed_at: str | None = None
    rejection_reason: str | None = None
