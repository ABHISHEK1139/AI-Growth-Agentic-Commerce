"""Persistence layer for Campaign Orchestrator with Redis backing and memory fallback."""

from __future__ import annotations

import json
import os
from typing import Any

from packages.observability.logging import get_logger
from services.campaigns.models import (
    Campaign,
    CampaignProductItem,
    CampaignStatus,
    PolicyCheckResult,
    PolicyDecision,
)

logger = get_logger(__name__)


def _serialize_campaign(c: Campaign) -> str:
    payload: dict[str, Any] = {
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
        "approved_at": c.approved_at,
        "activated_at": c.activated_at,
        "rejection_reason": c.rejection_reason,
    }
    return json.dumps(payload)


def _deserialize_campaign(data_str: str) -> Campaign:
    d = json.loads(data_str)
    products = [
        CampaignProductItem(
            product_id=p["product_id"],
            offer_id=p["offer_id"],
            title=p["title"],
            category=p["category"],
            original_price_minor=p["original_price_minor"],
            discount_pct=p["discount_pct"],
            promotional_price_minor=p["promotional_price_minor"],
            available_inventory=p["available_inventory"],
            margin_pct_preserved=p["margin_pct_preserved"],
            cross_sell_pairings=p.get("cross_sell_pairings", []),
            selection_rationale=p["selection_rationale"],
        )
        for p in d.get("products", [])
    ]
    pc = d.get("policy_check", {})
    policy_check = PolicyCheckResult(
        decision=PolicyDecision(pc.get("decision", "allow")),
        passed_rules=pc.get("passed_rules", []),
        violated_rules=pc.get("violated_rules", []),
        reason=pc.get("reason", ""),
    )
    return Campaign(
        campaign_id=d["campaign_id"],
        merchant_id=d["merchant_id"],
        title=d["title"],
        goal=d["goal"],
        target_category=d["target_category"],
        status=CampaignStatus(d.get("status", "proposed")),
        max_discount_pct=float(d.get("max_discount_pct", 10.0)),
        duration_days=int(d.get("duration_days", 3)),
        budget_minor=int(d.get("budget_minor", 5000000)),
        products=products,
        policy_check=policy_check,
        estimated_sales_lift_pct=float(d.get("estimated_sales_lift_pct", 15.0)),
        estimated_revenue_minor=int(d.get("estimated_revenue_minor", 0)),
        estimated_discount_cost_minor=int(d.get("estimated_discount_cost_minor", 0)),
        created_at=d.get("created_at", ""),
        approved_at=d.get("approved_at"),
        activated_at=d.get("activated_at"),
        rejection_reason=d.get("rejection_reason"),
    )


_SHARED_CAMPAIGNS_STORE: dict[str, Campaign] = {}


class CampaignRepository:
    """Persistent storage for campaigns using Redis with local fallback."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._memory_store = _SHARED_CAMPAIGNS_STORE
        self._redis_client: Any = None
        self._redis_url = redis_url or os.getenv("REDIS_URL")

        if self._redis_url:
            try:
                import redis

                self._redis_client = redis.Redis.from_url(
                    self._redis_url, decode_responses=True, socket_timeout=2.0
                )
                self._redis_client.ping()
            except Exception as e:
                logger.warning(
                    "Redis not available for CampaignRepository; using memory store", exc_info=e
                )
                self._redis_client = None

    def save(self, campaign: Campaign) -> None:
        self._memory_store[campaign.campaign_id] = campaign
        if self._redis_client is not None:
            try:
                key = f"campaign:{campaign.merchant_id}:{campaign.campaign_id}"
                index_key = f"campaigns:{campaign.merchant_id}"
                self._redis_client.set(key, _serialize_campaign(campaign))
                self._redis_client.sadd(index_key, campaign.campaign_id)
            except Exception as e:
                logger.warning("Failed saving campaign to Redis; memory copy intact", exc_info=e)

    def get(self, merchant_id: str | None, campaign_id: str) -> Campaign | None:
        if self._redis_client is not None and merchant_id:
            try:
                key = f"campaign:{merchant_id}:{campaign_id}"
                raw = self._redis_client.get(key)
                if raw:
                    return _deserialize_campaign(raw)
            except Exception as e:
                logger.warning(
                    "Failed fetching campaign from Redis; falling back to memory", exc_info=e
                )

        if campaign_id in self._memory_store:
            c = self._memory_store[campaign_id]
            if merchant_id is None or c.merchant_id == merchant_id:
                return c
            return None

        for c in self._memory_store.values():
            if c.campaign_id == campaign_id:
                if merchant_id is None or c.merchant_id == merchant_id:
                    return c
                return None
        return None

    def list_by_merchant(self, merchant_id: str) -> list[Campaign]:
        if self._redis_client is not None:
            try:
                index_key = f"campaigns:{merchant_id}"
                ids = self._redis_client.smembers(index_key)
                if ids:
                    result: list[Campaign] = []
                    for cid in ids:
                        c = self.get(merchant_id, cid)
                        if c is not None:
                            result.append(c)
                    return sorted(result, key=lambda x: x.created_at, reverse=True)
            except Exception as e:
                logger.warning(
                    "Failed reading campaign index from Redis; falling back to memory", exc_info=e
                )

        return [c for c in self._memory_store.values() if c.merchant_id == merchant_id]
