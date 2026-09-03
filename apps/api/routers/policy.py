"""Merchant Policy and Rules Configuration Router."""

from __future__ import annotations

from contextlib import suppress
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from apps.api.auth import AppSettings, require_roles
from apps.api.db import get_db
from apps.api.envelope import success
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.security.principals import Principal, Role
from packages.security.tenancy import TenantScope
from services.audit.repository import append_event
from services.policy.repository import MerchantRulesRepository

router = APIRouter(tags=["merchant-policy-rules"])
MerchantPrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN)),
]

DatabaseSession = Annotated[Session, Depends(get_db)]

#: Memory cache of active rules for test environments when PostgreSQL is offline.
_MEMORY_MERCHANT_RULES: dict[str, dict[str, Any]] = {}


def get_memory_merchant_rules(merchant_id: str) -> dict[str, Any] | None:
    return _MEMORY_MERCHANT_RULES.get(merchant_id)


class UpdateMerchantRulesRequest(BaseModel):
    max_transaction_minor: int = Field(
        ge=0, description="Maximum single transaction ceiling in minor units"
    )
    auto_approval_limit_minor: int = Field(
        ge=0, description="Maximum amount for automated autonomous payment approval"
    )
    max_discount_basis_points: int = Field(
        default=500,
        ge=0,
        le=10000,
        description="Maximum allowable discount in basis points (500 = 5%)",
    )
    allowed_categories: list[str] | None = None
    blocked_categories: list[str] | None = None
    allowed_payment_methods: list[str] | None = None
    allow_out_of_stock: bool = False


def _rules_to_dict(rules: Any, merchant_id: str, settings: Any) -> dict[str, Any]:
    if rules is None:
        return {
            "merchant_id": merchant_id,
            "version": "1.0",
            "max_transaction_minor": settings.max_transaction_amount_minor,
            "auto_approval_limit_minor": settings.auto_approval_limit_minor,
            "max_discount_basis_points": 500,
            "allowed_categories": ["laptops", "smartphones", "audio", "accessories"],
            "blocked_categories": [],
            "allowed_payment_methods": ["card", "upi"],
            "allow_out_of_stock": False,
            "updated_at": None,
        }
    return {
        "merchant_id": rules.merchant_id,
        "version": rules.version,
        "max_transaction_minor": rules.max_transaction_minor,
        "auto_approval_limit_minor": rules.auto_approval_limit_minor,
        "max_discount_basis_points": rules.max_discount_basis_points,
        "allowed_categories": list(rules.allowed_categories or []),
        "blocked_categories": list(rules.blocked_categories or []),
        "allowed_payment_methods": list(rules.allowed_payment_methods or []),
        "allow_out_of_stock": rules.allow_out_of_stock,
        "updated_at": rules.updated_at.isoformat()
        if hasattr(rules.updated_at, "isoformat")
        else str(rules.updated_at),
    }


@router.get("/api/v1/merchant/rules")
@router.get("/merchant/rules")
@router.get("/api/v1/merchant/policy")
@router.get("/merchant/policy")
def get_merchant_rules(
    principal: MerchantPrincipal,
    settings: AppSettings,
    session: DatabaseSession,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve the active policy rules and bounds configured for this merchant."""
    target_principal = principal.acting_on(merchant_id) if merchant_id else principal
    effective_merchant_id = target_principal.merchant_id or settings.default_merchant_id
    scope = TenantScope(merchant_id=effective_merchant_id)

    try:
        repo = MerchantRulesRepository(session, scope)
        rules = repo.get_by_merchant_id()
        if rules is not None:
            return success({"rules": _rules_to_dict(rules, effective_merchant_id, settings)})
    except (OperationalError, InterfaceError, DBAPIError, SQLAlchemyError):
        pass

    if effective_merchant_id in _MEMORY_MERCHANT_RULES:
        return success({"rules": _MEMORY_MERCHANT_RULES[effective_merchant_id]})

    return success({"rules": _rules_to_dict(None, effective_merchant_id, settings)})


@router.put("/api/v1/merchant/rules")
@router.put("/merchant/rules")
@router.put("/api/v1/merchant/policy")
@router.put("/merchant/policy")
@router.post("/api/v1/merchant/rules")
@router.post("/merchant/rules")
def update_merchant_rules(
    request: UpdateMerchantRulesRequest,
    principal: MerchantPrincipal,
    settings: AppSettings,
    session: DatabaseSession,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """Update and persist the merchant's financial bounds and category policy rules."""
    target_principal = principal.acting_on(merchant_id) if merchant_id else principal
    effective_merchant_id = target_principal.merchant_id or settings.default_merchant_id
    scope = TenantScope(merchant_id=effective_merchant_id)

    try:
        repo = MerchantRulesRepository(session, scope)
        updated = repo.upsert_rules(
            max_transaction_minor=request.max_transaction_minor,
            auto_approval_limit_minor=request.auto_approval_limit_minor,
            max_discount_basis_points=request.max_discount_basis_points,
            allowed_categories=request.allowed_categories,
            blocked_categories=request.blocked_categories,
            allowed_payment_methods=request.allowed_payment_methods,
            allow_out_of_stock=request.allow_out_of_stock,
        )
        append_event(
            session,
            event_type="POLICY_EVALUATED",
            aggregate_type="merchant_rules",
            aggregate_id=effective_merchant_id,
            actor_type="merchant",
            actor_id=principal.subject,
            merchant_id=effective_merchant_id,
            metadata={
                "action": "rules_updated",
                "max_transaction_minor": updated.max_transaction_minor,
                "auto_approval_limit_minor": updated.auto_approval_limit_minor,
            },
        )
        session.commit()
        return success({"rules": _rules_to_dict(updated, effective_merchant_id, settings)})
    except (OperationalError, InterfaceError, DBAPIError, SQLAlchemyError) as exc:
        with suppress(Exception):
            session.rollback()
        raise DomainError(
            "Datastore is currently unavailable. Merchant rule updates cannot be persisted.",
            code=ErrorCode.SERVICE_UNAVAILABLE,
        ) from exc
