"""Deterministic policy engine for Merchant Campaign Orchestrator.

Enforces strict financial, margin, inventory, and conflict invariants before any
campaign can be presented for approval or activated.
"""

from __future__ import annotations

from services.campaigns.models import (
    Campaign,
    CampaignProductItem,
    PolicyCheckResult,
    PolicyDecision,
)


class CampaignPolicyEngine:
    """Deterministic validator for promotional campaigns."""

    MIN_INVENTORY_THRESHOLD = 3
    MIN_MARGIN_FLOOR_PCT = 15.0
    MAX_DURATION_DAYS = 14

    def evaluate_campaign(
        self,
        *,
        max_discount_pct: float,
        duration_days: int,
        products: list[CampaignProductItem],
        merchant_policy_max_discount: float = 10.0,
        active_campaigns: list[Campaign] | None = None,
    ) -> PolicyCheckResult:
        passed: list[str] = []
        violated: list[str] = []

        # 1. Discount Ceiling Check
        if max_discount_pct > merchant_policy_max_discount:
            violated.append(
                f"DISCOUNT_CEILING_EXCEEDED: Requested {max_discount_pct}% exceeds merchant policy limit of {merchant_policy_max_discount}%"
            )
        else:
            passed.append(
                f"DISCOUNT_CEILING_SATISFIED: Max discount {max_discount_pct}% <= {merchant_policy_max_discount}%"
            )

        # 2. Product-level checks
        active_skus: set[str] = set()
        if active_campaigns:
            for ac in active_campaigns:
                if ac.status in ("active", "approved"):
                    for p in ac.products:
                        active_skus.add(p.product_id)

        for p in products:
            if p.discount_pct > merchant_policy_max_discount:
                violated.append(
                    f"ITEM_DISCOUNT_EXCEEDED ({p.product_id}): {p.discount_pct}% > {merchant_policy_max_discount}%"
                )
            if p.available_inventory < self.MIN_INVENTORY_THRESHOLD:
                violated.append(
                    f"INSUFFICIENT_INVENTORY ({p.product_id}): {p.available_inventory} units < {self.MIN_INVENTORY_THRESHOLD}"
                )
            if p.margin_pct_preserved < self.MIN_MARGIN_FLOOR_PCT:
                violated.append(
                    f"MARGIN_FLOOR_BREACH ({p.product_id}): Preserved margin {p.margin_pct_preserved}% < {self.MIN_MARGIN_FLOOR_PCT}%"
                )
            if p.product_id in active_skus:
                violated.append(
                    f"CAMPAIGN_CONFLICT ({p.product_id}): Already part of an active campaign"
                )

        if not any("ITEM_DISCOUNT_EXCEEDED" in v for v in violated):
            passed.append("ITEM_DISCOUNTS_VALID: All items within limit")
        if not any("INSUFFICIENT_INVENTORY" in v for v in violated):
            passed.append("INVENTORY_ADEQUATE: All items meet minimum stock threshold")
        if not any("MARGIN_FLOOR_BREACH" in v for v in violated):
            passed.append("MARGIN_FLOORS_PRESERVED: All items preserve >= 15% unit margin")
        if not any("CAMPAIGN_CONFLICT" in v for v in violated):
            passed.append("NO_CONFLICTS: No overlap with existing active campaigns")

        # 3. Duration check
        if duration_days < 1 or duration_days > self.MAX_DURATION_DAYS:
            violated.append(
                f"INVALID_DURATION: Duration {duration_days}d must be between 1 and {self.MAX_DURATION_DAYS} days"
            )
        else:
            passed.append(
                f"DURATION_VALID: {duration_days} days is within {self.MAX_DURATION_DAYS}-day window"
            )

        # Determine verdict
        if violated:
            # If critical margin or ceiling breach -> BLOCK
            decision = PolicyDecision.BLOCK
            reason = f"Campaign violates {len(violated)} safety policy rules: " + "; ".join(
                violated
            )
        else:
            # Normal compliant campaigns require merchant confirmation before launch
            decision = PolicyDecision.REQUIRE_APPROVAL
            reason = "Campaign passes all deterministic safety rules and requires merchant sign-off"

        return PolicyCheckResult(
            decision=decision,
            passed_rules=passed,
            violated_rules=violated,
            reason=reason,
        )
