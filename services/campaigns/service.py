"""Campaign Orchestrator and Merchant Growth Domain Service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from packages.observability.logging import get_logger
from services.campaigns.models import (
    Campaign,
    CampaignProductItem,
    CampaignStatus,
    PolicyDecision,
)
from services.campaigns.policy import CampaignPolicyEngine
from services.campaigns.repository import CampaignRepository
from services.catalog.models import CategoryPairing, Product
from services.inventory.models import Inventory
from services.offers.models import Offer

logger = get_logger(__name__)


class CampaignService:
    """Orchestrates campaign proposal, safety validation, approval, and live performance tracking."""

    def __init__(self, repository: CampaignRepository | None = None) -> None:
        self.policy_engine = CampaignPolicyEngine()
        self._repo = repository or CampaignRepository()

    def propose_campaign(
        self,
        session: Session | None = None,
        *,
        merchant_id: str,
        goal_prompt: str,
        max_discount_pct: float = 10.0,
        duration_days: int = 3,
        budget_minor: int = 5000000,
        category: str | None = None,
    ) -> Campaign:
        """Analyze merchant catalog and produce a structured, policy-checked campaign proposal."""
        goal_lower = goal_prompt.lower()

        # Target category detection
        target_cat = category
        if not target_cat:
            if "headphone" in goal_lower or "audio" in goal_lower or "sound" in goal_lower:
                target_cat = "audio"
            elif "phone" in goal_lower or "mobile" in goal_lower:
                target_cat = "smartphone"
            elif "laptop" in goal_lower or "pc" in goal_lower or "computer" in goal_lower:
                target_cat = "laptops"
            elif "accessory" in goal_lower or "keyboard" in goal_lower or "mouse" in goal_lower:
                target_cat = "accessories"
            else:
                target_cat = "audio"

        items: list[CampaignProductItem] = []

        # ── Step 1: Database Catalog & Inventory Discovery ──
        if session is not None:
            try:
                # Query candidate products in merchant's catalog
                query = session.query(Product).filter(Product.merchant_id == merchant_id)
                if category:
                    query = query.filter(Product.category_id == category)
                else:
                    query = query.filter(
                        (Product.category_id.ilike(f"%{target_cat}%"))
                        | (Product.title.ilike(f"%{target_cat}%"))
                    )

                candidates = query.order_by(Product.product_id).limit(3).all()
                for cand in candidates:
                    offer = (
                        session.query(Offer)
                        .filter(
                            Offer.product_id == cand.product_id,
                            Offer.merchant_id == merchant_id,
                            Offer.status == "active",
                        )
                        .order_by(Offer.offer_id)
                        .first()
                    )
                    if not offer:
                        continue

                    inv = (
                        session.query(Inventory)
                        .filter(Inventory.offer_id == offer.offer_id)
                        .first()
                    )
                    avail_qty = 10
                    if inv:
                        avail_qty = max(0, inv.available_quantity - inv.reserved_quantity)

                    # Fetch real cross-sell pairings
                    pairings = (
                        session.query(CategoryPairing)
                        .filter(
                            CategoryPairing.merchant_id == merchant_id,
                            CategoryPairing.source_category_id == cand.category_id,
                            CategoryPairing.enabled.is_(True),
                        )
                        .all()
                    )
                    pairing_names = [p.target_category_id for p in pairings] or ["accessories"]

                    discount = min(max_discount_pct, 10.0)
                    orig_price = offer.unit_price_minor
                    promo_price = int(orig_price * (1 - (discount / 100)))

                    items.append(
                        CampaignProductItem(
                            product_id=cand.product_id,
                            offer_id=offer.offer_id,
                            title=cand.title,
                            category=cand.category_id,
                            original_price_minor=orig_price,
                            discount_pct=discount,
                            promotional_price_minor=promo_price,
                            available_inventory=avail_qty,
                            margin_pct_preserved=round(35.0 - discount, 1),
                            cross_sell_pairings=pairing_names,
                            selection_rationale=f"High catalog fit for '{target_cat}', {avail_qty} units in stock, preserved gross margin.",
                        )
                    )
            except Exception:
                logger.warning(
                    "campaign_catalog_query_failed",
                    extra={"merchant_id": merchant_id},
                    exc_info=True,
                )

        # ── Step 2: Fallback candidates for offline mode / unseeded databases ──
        if not items:
            if target_cat == "audio":
                orig_price = 799000
                discount = min(max_discount_pct, 10.0)
                items.append(
                    CampaignProductItem(
                        product_id="prd_seed_aud_01",
                        offer_id="off_seed_aud_01",
                        title="Sony WH-CH720N Wireless Noise Cancelling Headphones",
                        category="audio",
                        original_price_minor=orig_price,
                        discount_pct=discount,
                        promotional_price_minor=int(orig_price * (1 - (discount / 100))),
                        available_inventory=24,
                        margin_pct_preserved=32.5,
                        cross_sell_pairings=[
                            "Nothing Phone (2a)",
                            "Dell XPS 15",
                            "Keychron K2 Pro",
                        ],
                        selection_rationale="High warehouse inventory (24 units), steady 4.4★ rating, sufficient 32.5% gross margin headroom.",
                    )
                )
            elif target_cat == "laptops":
                orig_price = 5499000
                discount = min(max_discount_pct, 8.0)
                items.append(
                    CampaignProductItem(
                        product_id="prd_seed_lap_02",
                        offer_id="off_seed_lap_02",
                        title="Lenovo IdeaPad Slim 3 15 (Core i5, 16GB RAM, 512GB SSD)",
                        category="laptops",
                        original_price_minor=orig_price,
                        discount_pct=discount,
                        promotional_price_minor=int(orig_price * (1 - (discount / 100))),
                        available_inventory=12,
                        margin_pct_preserved=24.0,
                        cross_sell_pairings=[
                            "USB-C 7-in-1 Hub",
                            "Logitech MX Master 3S",
                            "Laptop Sleeve",
                        ],
                        selection_rationale="Targeted coding demographic, 12 available units, excellent dual-channel performance fit.",
                    )
                )
            elif target_cat == "smartphone":
                orig_price = 2399900
                discount = min(max_discount_pct, 7.5)
                items.append(
                    CampaignProductItem(
                        product_id="prd_seed_phn_01",
                        offer_id="off_seed_phn_01",
                        title="Nothing Phone (2a) 5G (8GB RAM, 128GB Storage)",
                        category="smartphone",
                        original_price_minor=orig_price,
                        discount_pct=discount,
                        promotional_price_minor=int(orig_price * (1 - (discount / 100))),
                        available_inventory=18,
                        margin_pct_preserved=21.0,
                        cross_sell_pairings=[
                            "Samsung 45W Charger",
                            "Sony WH-CH720N",
                            "Armor Case with Kickstand",
                        ],
                        selection_rationale="High search demand, pairs naturally with fast charger and headphones for basket expansion.",
                    )
                )
            else:
                orig_price = 99900
                discount = min(max_discount_pct, 12.0)
                items.append(
                    CampaignProductItem(
                        product_id="prd_seed_acc_01",
                        offer_id="off_seed_acc_01",
                        title="Logitech M330 Silent Plus Wireless Mouse",
                        category="accessories",
                        original_price_minor=orig_price,
                        discount_pct=discount,
                        promotional_price_minor=int(orig_price * (1 - (discount / 100))),
                        available_inventory=45,
                        margin_pct_preserved=40.0,
                        cross_sell_pairings=["UltraBook Pro 16", "Dell XPS 15"],
                        selection_rationale="High margin (40%), massive stock (45 units), ideal universal laptop attach item.",
                    )
                )

        # ── Step 3: Deterministic Policy Validation ──
        active_list = self.list_campaigns(merchant_id)
        policy_result = self.policy_engine.evaluate_campaign(
            max_discount_pct=max_discount_pct,
            duration_days=duration_days,
            products=items,
            merchant_policy_max_discount=10.0,
            active_campaigns=active_list,
        )

        campaign_id = new_id("cmp")
        title_tag = target_cat.capitalize()
        campaign = Campaign(
            campaign_id=campaign_id,
            merchant_id=merchant_id,
            title=f"Weekend {title_tag} Velocity Boost ({int(max_discount_pct)}% Off)",
            goal=goal_prompt,
            target_category=target_cat,
            status=CampaignStatus.PROPOSED,
            max_discount_pct=max_discount_pct,
            duration_days=duration_days,
            budget_minor=budget_minor,
            products=items,
            policy_check=policy_result,
            estimated_sales_lift_pct=round(max_discount_pct * 2.2, 1),
            estimated_revenue_minor=sum(
                p.promotional_price_minor * min(p.available_inventory, 8) for p in items
            ),
            estimated_discount_cost_minor=sum(
                (p.original_price_minor - p.promotional_price_minor) * min(p.available_inventory, 8)
                for p in items
            ),
            created_at=datetime.now(UTC).isoformat(),
        )

        self._repo.save(campaign)
        return campaign

    def list_campaigns(self, merchant_id: str) -> list[Campaign]:
        return self._repo.list_by_merchant(merchant_id)

    def get_campaign(self, campaign_id: str, merchant_id: str | None = None) -> Campaign | None:
        return self._repo.get(merchant_id, campaign_id)

    def submit_for_review(self, campaign_id: str, merchant_id: str | None = None) -> Campaign:
        """Move a draft / proposed campaign into merchant review (DRAFT|PROPOSED → REVIEW).

        The AI may *propose*; the merchant must explicitly *submit* a campaign
        into the human review queue. The transition is refused from any state
        other than DRAFT or PROPOSED so a campaign in flight (ACTIVE / PAUSED)
        cannot be silently re-submitted.
        """
        c = self.get_campaign(campaign_id, merchant_id)
        if not c:
            raise DomainError(f"Campaign {campaign_id} not found.", code=ErrorCode.NOT_FOUND)

        if c.status not in (CampaignStatus.DRAFT, CampaignStatus.PROPOSED):
            raise DomainError(
                f"Campaign cannot be submitted for review from status {c.status.value}.",
                code=ErrorCode.INVALID_STATUS_TRANSITION,
            )

        submitted = Campaign(
            campaign_id=c.campaign_id,
            merchant_id=c.merchant_id,
            title=c.title,
            goal=c.goal,
            target_category=c.target_category,
            status=CampaignStatus.REVIEW,
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            budget_minor=c.budget_minor,
            products=c.products,
            policy_check=c.policy_check,
            estimated_sales_lift_pct=c.estimated_sales_lift_pct,
            estimated_revenue_minor=c.estimated_revenue_minor,
            estimated_discount_cost_minor=c.estimated_discount_cost_minor,
            created_at=c.created_at,
            reviewed_at=datetime.now(UTC).isoformat(),
            approved_at=c.approved_at,
            activated_at=c.activated_at,
            rejection_reason=c.rejection_reason,
        )
        self._repo.save(submitted)
        return submitted

    def approve_campaign(self, campaign_id: str, merchant_id: str | None = None) -> Campaign:
        c = self.get_campaign(campaign_id, merchant_id)
        if not c:
            raise DomainError(f"Campaign {campaign_id} not found.", code=ErrorCode.NOT_FOUND)

        # Accepting REVIEW alongside PROPOSED so a campaign that goes through
        # the explicit submit-for-review step still reaches APPROVED on the
        # merchant's sign-off.
        if c.status not in (CampaignStatus.PROPOSED, CampaignStatus.REVIEW):
            raise DomainError(
                f"Campaign cannot be approved from status {c.status.value}.",
                code=ErrorCode.INVALID_STATUS_TRANSITION,
            )

        if c.policy_check.decision == PolicyDecision.BLOCK:
            raise DomainError(
                f"Campaign violates policy constraints: {', '.join(c.policy_check.violated_rules)}",
                code=ErrorCode.POLICY_RULE_VIOLATED,
            )

        approved = Campaign(
            campaign_id=c.campaign_id,
            merchant_id=c.merchant_id,
            title=c.title,
            goal=c.goal,
            target_category=c.target_category,
            status=CampaignStatus.APPROVED,
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            budget_minor=c.budget_minor,
            products=c.products,
            policy_check=c.policy_check,
            estimated_sales_lift_pct=c.estimated_sales_lift_pct,
            estimated_revenue_minor=c.estimated_revenue_minor,
            estimated_discount_cost_minor=c.estimated_discount_cost_minor,
            created_at=c.created_at,
            reviewed_at=c.reviewed_at,
            approved_at=datetime.now(UTC).isoformat(),
            activated_at=c.activated_at,
            rejection_reason=c.rejection_reason,
        )
        self._repo.save(approved)
        return approved

    def activate_campaign(self, campaign_id: str, merchant_id: str | None = None) -> Campaign:
        c = self.get_campaign(campaign_id, merchant_id)
        if not c:
            raise DomainError(f"Campaign {campaign_id} not found.", code=ErrorCode.NOT_FOUND)

        # Approval is mandatory. Accepting PROPOSED here let a caller bypass the
        # merchant authorization gate entirely by calling activate directly.
        if c.status != CampaignStatus.APPROVED:
            raise DomainError(
                f"Campaign cannot be activated from status {c.status.value}; "
                "it must be approved first.",
                code=ErrorCode.INVALID_STATUS_TRANSITION,
            )

        # Policy is re-evaluated at activation: a rules change between propose
        # and activate must block the campaign that no longer complies.
        recheck = self.policy_engine.evaluate_campaign(
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            products=c.products,
            merchant_policy_max_discount=10.0,
            active_campaigns=[
                x for x in self.list_campaigns(c.merchant_id) if x.campaign_id != c.campaign_id
            ],
        )
        if recheck.decision == PolicyDecision.BLOCK:
            raise DomainError(
                f"Campaign violates policy constraints: {', '.join(recheck.violated_rules)}",
                code=ErrorCode.POLICY_RULE_VIOLATED,
            )

        active = Campaign(
            campaign_id=c.campaign_id,
            merchant_id=c.merchant_id,
            title=c.title,
            goal=c.goal,
            target_category=c.target_category,
            status=CampaignStatus.ACTIVE,
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            budget_minor=c.budget_minor,
            products=c.products,
            policy_check=c.policy_check,
            estimated_sales_lift_pct=c.estimated_sales_lift_pct,
            estimated_revenue_minor=c.estimated_revenue_minor,
            estimated_discount_cost_minor=c.estimated_discount_cost_minor,
            created_at=c.created_at,
            approved_at=c.approved_at or datetime.now(UTC).isoformat(),
            activated_at=datetime.now(UTC).isoformat(),
            rejection_reason=c.rejection_reason,
        )
        self._repo.save(active)
        return active

    def reject_campaign(
        self,
        campaign_id: str,
        merchant_id: str | None = None,
        reason: str = "Merchant declined proposal",
    ) -> Campaign:
        c = self.get_campaign(campaign_id, merchant_id)
        if not c:
            raise DomainError(f"Campaign {campaign_id} not found.", code=ErrorCode.NOT_FOUND)

        rejected = Campaign(
            campaign_id=c.campaign_id,
            merchant_id=c.merchant_id,
            title=c.title,
            goal=c.goal,
            target_category=c.target_category,
            status=CampaignStatus.REJECTED,
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            budget_minor=c.budget_minor,
            products=c.products,
            policy_check=c.policy_check,
            estimated_sales_lift_pct=c.estimated_sales_lift_pct,
            estimated_revenue_minor=c.estimated_revenue_minor,
            estimated_discount_cost_minor=c.estimated_discount_cost_minor,
            created_at=c.created_at,
            approved_at=c.approved_at,
            activated_at=c.activated_at,
            rejection_reason=reason,
        )
        self._repo.save(rejected)
        return rejected

    def get_analytics(self, merchant_id: str) -> dict[str, Any]:
        campaigns = self.list_campaigns(merchant_id)
        total_rev = sum(c.estimated_revenue_minor for c in campaigns)
        total_cost = sum(c.estimated_discount_cost_minor for c in campaigns)
        net_roi = round((total_rev - total_cost) / total_cost * 100.0, 1) if total_cost > 0 else 0.0

        return {
            "merchant_id": merchant_id,
            "total_campaigns": len(campaigns),
            "active_campaigns": len([c for c in campaigns if c.status == CampaignStatus.ACTIVE]),
            "approved_campaigns": len(
                [c for c in campaigns if c.status == CampaignStatus.APPROVED]
            ),
            "total_promotional_revenue_minor": total_rev,
            "incremental_revenue_minor": total_rev,
            "total_discount_investment_minor": total_cost,
            "net_roi_pct": net_roi,
            "average_sales_lift_pct": round(
                sum(c.estimated_sales_lift_pct for c in campaigns) / len(campaigns), 1
            )
            if campaigns
            else 0.0,
        }

    def pause_campaign(self, campaign_id: str, merchant_id: str | None = None) -> Campaign:
        """Halt an active campaign temporarily (ACTIVE → PAUSED)."""
        c = self.get_campaign(campaign_id, merchant_id)
        if not c:
            raise DomainError(f"Campaign {campaign_id} not found.", code=ErrorCode.NOT_FOUND)

        if c.status != CampaignStatus.ACTIVE:
            raise DomainError(
                f"Campaign cannot be paused from status {c.status.value}; only ACTIVE campaigns can be paused.",
                code=ErrorCode.INVALID_STATUS_TRANSITION,
            )

        paused = Campaign(
            campaign_id=c.campaign_id,
            merchant_id=c.merchant_id,
            title=c.title,
            goal=c.goal,
            target_category=c.target_category,
            status=CampaignStatus.PAUSED,
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            budget_minor=c.budget_minor,
            products=c.products,
            policy_check=c.policy_check,
            estimated_sales_lift_pct=c.estimated_sales_lift_pct,
            estimated_revenue_minor=c.estimated_revenue_minor,
            estimated_discount_cost_minor=c.estimated_discount_cost_minor,
            created_at=c.created_at,
            approved_at=c.approved_at,
            activated_at=c.activated_at,
            rejection_reason=c.rejection_reason,
            paused_at=datetime.now(UTC).isoformat(),
        )
        self._repo.save(paused)
        return paused

    def complete_campaign(self, campaign_id: str, merchant_id: str | None = None) -> Campaign:
        """Mark an active or paused campaign as fully concluded (ACTIVE/PAUSED → COMPLETED)."""
        c = self.get_campaign(campaign_id, merchant_id)
        if not c:
            raise DomainError(f"Campaign {campaign_id} not found.", code=ErrorCode.NOT_FOUND)

        if c.status not in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
            raise DomainError(
                f"Campaign cannot be completed from status {c.status.value}; "
                "only ACTIVE or PAUSED campaigns can be completed.",
                code=ErrorCode.INVALID_STATUS_TRANSITION,
            )

        completed = Campaign(
            campaign_id=c.campaign_id,
            merchant_id=c.merchant_id,
            title=c.title,
            goal=c.goal,
            target_category=c.target_category,
            status=CampaignStatus.COMPLETED,
            max_discount_pct=c.max_discount_pct,
            duration_days=c.duration_days,
            budget_minor=c.budget_minor,
            products=c.products,
            policy_check=c.policy_check,
            estimated_sales_lift_pct=c.estimated_sales_lift_pct,
            estimated_revenue_minor=c.estimated_revenue_minor,
            estimated_discount_cost_minor=c.estimated_discount_cost_minor,
            created_at=c.created_at,
            approved_at=c.approved_at,
            activated_at=c.activated_at,
            rejection_reason=c.rejection_reason,
            completed_at=datetime.now(UTC).isoformat(),
        )
        self._repo.save(completed)
        return completed
