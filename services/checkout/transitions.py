"""The sole validated state-mutation path for commerce aggregates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from services.audit.repository import append_transition_event


class TransitionEvent(StrEnum):
    SELECT_OFFER = "select_offer"
    CREATE_CHECKOUT = "create_checkout"
    CHECK_POLICY = "check_policy"
    REQUIRE_APPROVAL = "require_approval"
    USE_EXISTING_AUTHORIZATION = "use_existing_authorization"
    BLOCK_POLICY = "block_policy"
    APPROVE_AUTHORIZATION = "approve_authorization"
    REJECT_AUTHORIZATION = "reject_authorization"
    EXPIRE_AUTHORIZATION = "expire_authorization"
    CREATE_PAYMENT = "create_payment"
    DETECT_PRICE_CHANGE = "detect_price_change"
    DETECT_INVENTORY_CHANGE = "detect_inventory_change"
    EXPIRE_CHECKOUT = "expire_checkout"
    CANCEL_CHECKOUT = "cancel_checkout"
    PROVIDER_ORDER_CREATED = "provider_order_created"
    VERIFY_PAYMENT = "verify_payment"
    FAIL_PAYMENT = "fail_payment"
    TIMEOUT_PAYMENT = "timeout_payment"
    MARK_UNKNOWN = "mark_unknown"
    VERIFY_UNKNOWN = "verify_unknown"
    FAIL_UNKNOWN = "fail_unknown"
    EXHAUST_UNKNOWN = "exhaust_unknown"
    CONFIRM_ORDER = "confirm_order"
    COMPLETE_ORDER = "complete_order"


TERMINAL_STATES = frozenset(
    {
        "PRICE_CHANGED",
        "INVENTORY_CHANGED",
        "POLICY_BLOCKED",
        "CHECKOUT_EXPIRED",
        "AUTHORIZATION_EXPIRED",
        "CANCELLED",
        "PAYMENT_FAILED",
        "MANUAL_REVIEW",
        "COMPLETED",
    }
)


@dataclass(frozen=True)
class TransitionRule:
    source: str
    event: TransitionEvent
    target: str
    actors: frozenset[str]
    required_fields: frozenset[str] = frozenset()
    checks_price_hash: bool = False
    checks_authorization: bool = False
    checks_expiry: bool = False


def _rule(
    source: str,
    event: TransitionEvent,
    target: str,
    *actors: str,
    required: tuple[str, ...] = (),
    price: bool = False,
    authorization: bool = False,
    expiry: bool = False,
) -> TransitionRule:
    return TransitionRule(
        source, event, target, frozenset(actors), frozenset(required), price, authorization, expiry
    )


TRANSITIONS: tuple[TransitionRule, ...] = (
    _rule("DISCOVERED", TransitionEvent.SELECT_OFFER, "OFFER_SELECTED", "buyer", "agent"),
    _rule(
        "OFFER_SELECTED",
        TransitionEvent.CREATE_CHECKOUT,
        "CHECKOUT_CREATED",
        "buyer",
        "agent",
        required=("quantity",),
    ),
    _rule("CHECKOUT_CREATED", TransitionEvent.CHECK_POLICY, "POLICY_CHECKED", "system"),
    _rule(
        "CHECKOUT_CREATED",
        TransitionEvent.REQUIRE_APPROVAL,
        "AUTHORIZATION_PENDING",
        "system",
        "buyer",
    ),
    _rule(
        "CHECKOUT_CREATED",
        TransitionEvent.USE_EXISTING_AUTHORIZATION,
        "AUTHORIZED",
        "system",
        "buyer",
        authorization=True,
    ),
    _rule("CHECKOUT_CREATED", TransitionEvent.BLOCK_POLICY, "POLICY_BLOCKED", "system"),
    _rule("CHECKOUT_CREATED", TransitionEvent.CANCEL_CHECKOUT, "CANCELLED", "buyer", "system"),
    _rule("CHECKOUT_CREATED", TransitionEvent.REJECT_AUTHORIZATION, "CANCELLED", "buyer", "system"),
    _rule(
        "CHECKOUT_CREATED",
        TransitionEvent.EXPIRE_CHECKOUT,
        "CHECKOUT_EXPIRED",
        "system",
        expiry=True,
    ),
    _rule("POLICY_CHECKED", TransitionEvent.REQUIRE_APPROVAL, "AUTHORIZATION_PENDING", "system"),
    _rule(
        "POLICY_CHECKED",
        TransitionEvent.USE_EXISTING_AUTHORIZATION,
        "AUTHORIZED",
        "system",
        authorization=True,
    ),
    _rule("POLICY_CHECKED", TransitionEvent.BLOCK_POLICY, "POLICY_BLOCKED", "system"),
    _rule("POLICY_CHECKED", TransitionEvent.CANCEL_CHECKOUT, "CANCELLED", "buyer", "system"),
    _rule(
        "AUTHORIZATION_PENDING",
        TransitionEvent.APPROVE_AUTHORIZATION,
        "AUTHORIZED",
        "buyer",
        authorization=True,
    ),
    _rule(
        "AUTHORIZATION_PENDING",
        TransitionEvent.REJECT_AUTHORIZATION,
        "CANCELLED",
        "buyer",
        "system",
    ),
    _rule(
        "AUTHORIZATION_PENDING",
        TransitionEvent.CANCEL_CHECKOUT,
        "CANCELLED",
        "buyer",
        "system",
    ),
    _rule("AUTHORIZATION_PENDING", TransitionEvent.BLOCK_POLICY, "POLICY_BLOCKED", "system"),
    _rule(
        "AUTHORIZATION_PENDING",
        TransitionEvent.EXPIRE_AUTHORIZATION,
        "AUTHORIZATION_EXPIRED",
        "system",
        expiry=True,
    ),
    # A checkout waiting on buyer approval past its TTL must be expirable like
    # any other: without this rule the worker's expiry sweep raised
    # ILLEGAL_TRANSITION and stale pending checkouts leaked inventory holds
    # indefinitely.
    _rule(
        "AUTHORIZATION_PENDING",
        TransitionEvent.EXPIRE_CHECKOUT,
        "CHECKOUT_EXPIRED",
        "system",
        expiry=True,
    ),
    _rule(
        "AUTHORIZED",
        TransitionEvent.CREATE_PAYMENT,
        "PAYMENT_CREATED",
        "buyer",
        "agent",
        price=True,
        authorization=True,
        expiry=True,
    ),
    _rule("AUTHORIZED", TransitionEvent.DETECT_PRICE_CHANGE, "PRICE_CHANGED", "system"),
    _rule("AUTHORIZED", TransitionEvent.DETECT_INVENTORY_CHANGE, "INVENTORY_CHANGED", "system"),
    _rule("AUTHORIZED", TransitionEvent.EXPIRE_CHECKOUT, "CHECKOUT_EXPIRED", "system", expiry=True),
    _rule("AUTHORIZED", TransitionEvent.COMPLETE_ORDER, "COMPLETED", "system"),
    _rule("AUTHORIZED", TransitionEvent.CONFIRM_ORDER, "ORDER_CONFIRMED", "system"),
    _rule("AUTHORIZED", TransitionEvent.FAIL_PAYMENT, "PAYMENT_FAILED", "system", "provider"),
    _rule("AUTHORIZED", TransitionEvent.CANCEL_CHECKOUT, "CANCELLED", "buyer", "system"),
    _rule("AUTHORIZED", TransitionEvent.REJECT_AUTHORIZATION, "CANCELLED", "buyer", "system"),
    _rule(
        "PAYMENT_CREATED",
        TransitionEvent.PROVIDER_ORDER_CREATED,
        "PAYMENT_PENDING",
        "system",
        "provider",
    ),
    _rule("PAYMENT_CREATED", TransitionEvent.FAIL_PAYMENT, "PAYMENT_FAILED", "system", "provider"),
    _rule(
        "PAYMENT_PENDING", TransitionEvent.VERIFY_PAYMENT, "PAYMENT_VERIFIED", "system", "provider"
    ),
    _rule("PAYMENT_PENDING", TransitionEvent.FAIL_PAYMENT, "PAYMENT_FAILED", "system", "provider"),
    _rule("PAYMENT_PENDING", TransitionEvent.TIMEOUT_PAYMENT, "PAYMENT_TIMEOUT", "system"),
    _rule("PAYMENT_TIMEOUT", TransitionEvent.MARK_UNKNOWN, "PAYMENT_UNKNOWN", "system"),
    _rule(
        "PAYMENT_UNKNOWN", TransitionEvent.VERIFY_UNKNOWN, "PAYMENT_VERIFIED", "system", "provider"
    ),
    _rule("PAYMENT_UNKNOWN", TransitionEvent.FAIL_UNKNOWN, "PAYMENT_FAILED", "system", "provider"),
    _rule("PAYMENT_UNKNOWN", TransitionEvent.EXHAUST_UNKNOWN, "MANUAL_REVIEW", "system"),
    _rule("PAYMENT_VERIFIED", TransitionEvent.CONFIRM_ORDER, "ORDER_CONFIRMED", "system"),
    _rule("ORDER_CONFIRMED", TransitionEvent.COMPLETE_ORDER, "COMPLETED", "system"),
)
RULES = {(rule.source, rule.event): rule for rule in TRANSITIONS}

_NORMALIZE_STATUS = {
    # Checkout
    "CREATED": "CHECKOUT_CREATED",
    "CHECKOUT_CREATED": "CHECKOUT_CREATED",
    "REQUIRES_APPROVAL": "AUTHORIZATION_PENDING",
    "AUTHORIZATION_PENDING": "AUTHORIZATION_PENDING",
    "AUTHORIZED": "AUTHORIZED",
    "PRICE_CHANGED": "PRICE_CHANGED",
    "INVENTORY_CHANGED": "INVENTORY_CHANGED",
    "CHECKOUT_EXPIRED": "CHECKOUT_EXPIRED",
    "EXPIRED": "CHECKOUT_EXPIRED",
    "CANCELLED": "CANCELLED",
    "COMPLETED": "COMPLETED",
    "PAYMENT_FAILED": "PAYMENT_FAILED",
    "POLICY_BLOCKED": "POLICY_BLOCKED",
    # Payment
    "PAYMENT_CREATED": "PAYMENT_CREATED",
    "PENDING": "PAYMENT_PENDING",
    "PAYMENT_PENDING": "PAYMENT_PENDING",
    "VERIFIED": "PAYMENT_VERIFIED",
    "PAYMENT_VERIFIED": "PAYMENT_VERIFIED",
    "FAILED": "PAYMENT_FAILED",
    "PAYMENT_TIMEOUT": "PAYMENT_TIMEOUT",
    "TIMEOUT": "PAYMENT_TIMEOUT",
    "PAYMENT_UNKNOWN": "PAYMENT_UNKNOWN",
    "UNKNOWN": "PAYMENT_UNKNOWN",
    "MANUAL_REVIEW": "MANUAL_REVIEW",
    # Authorization
    "APPROVED": "AUTHORIZED",
    "REJECTED": "CANCELLED",
    "CONSUMED": "CONSUMED",
}


def format_target_status(aggregate_type: str, rule_target: str, original_status: str) -> str:
    """Format rule target to match the aggregate's casing and domain conventions."""
    if not original_status.islower():
        return rule_target
    if aggregate_type == "checkout":
        return {
            "CHECKOUT_CREATED": "created",
            "AUTHORIZATION_PENDING": "authorization_pending",
            "AUTHORIZED": "authorized",
            "PRICE_CHANGED": "price_changed",
            "INVENTORY_CHANGED": "inventory_changed",
            "CHECKOUT_EXPIRED": "expired",
            "CANCELLED": "cancelled",
            "COMPLETED": "completed",
            "PAYMENT_FAILED": "payment_failed",
            "POLICY_BLOCKED": "policy_blocked",
        }.get(rule_target, rule_target.lower())
    if aggregate_type == "payment":
        return {
            "PAYMENT_CREATED": "created",
            "PAYMENT_PENDING": "pending",
            "PAYMENT_VERIFIED": "verified",
            "ORDER_CONFIRMED": "verified",
            "PAYMENT_FAILED": "failed",
            "PAYMENT_TIMEOUT": "timeout",
            "PAYMENT_UNKNOWN": "unknown",
            "MANUAL_REVIEW": "manual_review",
        }.get(rule_target, rule_target.lower())
    if aggregate_type == "authorization":
        return {
            "AUTHORIZATION_PENDING": "pending",
            "AUTHORIZED": "approved",
            "CANCELLED": "rejected",
            "AUTHORIZATION_EXPIRED": "expired",
        }.get(rule_target, rule_target.lower())
    return rule_target.lower()


class Aggregate(Protocol):
    @property
    def aggregate_id(self) -> str: ...

    @property
    def aggregate_type(self) -> str: ...

    status: Any


@dataclass(frozen=True)
class TransitionContext:
    actor_type: str
    actor_id: str | None
    merchant_id: str | None = None
    values: dict[str, object] = field(default_factory=dict)
    supplied_price_hash: str | None = None
    persisted_price_hash: str | None = None
    authorization_valid: bool = True
    authorization_consumed: bool = False
    expires_at: datetime | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class TransitionResult:
    previous_status: str
    status: str
    audit_event_id: str


def normalize_source_status(aggregate_type: str, status: str) -> str:
    """Normalize status string to canonical uppercase rule token based on aggregate type."""
    status_upper = status.upper()
    if aggregate_type == "payment":
        if status_upper in ("CREATED", "PAYMENT_CREATED"):
            return "PAYMENT_CREATED"
        if status_upper in ("PENDING", "PAYMENT_PENDING"):
            return "PAYMENT_PENDING"
        if status_upper in ("VERIFIED", "PAYMENT_VERIFIED"):
            return "PAYMENT_VERIFIED"
        if status_upper in ("FAILED", "PAYMENT_FAILED"):
            return "PAYMENT_FAILED"
        if status_upper in ("TIMEOUT", "PAYMENT_TIMEOUT"):
            return "PAYMENT_TIMEOUT"
        if status_upper in ("UNKNOWN", "PAYMENT_UNKNOWN"):
            return "PAYMENT_UNKNOWN"
    elif aggregate_type == "authorization":
        if status_upper in ("PENDING", "AUTHORIZATION_PENDING"):
            return "AUTHORIZATION_PENDING"
        if status_upper in ("APPROVED", "AUTHORIZED"):
            return "AUTHORIZED"
        if status_upper in ("REJECTED", "CANCELLED"):
            return "CANCELLED"
        if status_upper in ("EXPIRED", "AUTHORIZATION_EXPIRED"):
            return "AUTHORIZATION_EXPIRED"
    elif aggregate_type == "checkout":
        if status_upper in ("CREATED", "CHECKOUT_CREATED"):
            return "CHECKOUT_CREATED"
        if status_upper in ("REQUIRES_APPROVAL", "AUTHORIZATION_PENDING"):
            return "AUTHORIZATION_PENDING"
        if status_upper in ("AUTHORIZED",):
            return "AUTHORIZED"
        if status_upper in ("EXPIRED", "CHECKOUT_EXPIRED"):
            return "CHECKOUT_EXPIRED"
        if status_upper in ("CANCELLED",):
            return "CANCELLED"
    return _NORMALIZE_STATUS.get(status_upper, status_upper)


def transition(
    aggregate: Aggregate, event: TransitionEvent, context: TransitionContext, session: Session
) -> TransitionResult:
    """Validate and apply exactly one state change; the caller owns commit/rollback."""
    agg_type = getattr(aggregate, "aggregate_type", "checkout")
    source_status = normalize_source_status(agg_type, aggregate.status)
    rule = RULES.get((source_status, event))
    if rule is None:
        if source_status in TERMINAL_STATES:
            raise DomainError(code=ErrorCode.ALREADY_FINALIZED)
        raise DomainError(code=ErrorCode.ILLEGAL_TRANSITION)
    missing = rule.required_fields - context.values.keys()
    if missing:
        raise DomainError(code=ErrorCode.VALIDATION_ERROR, details={"missing": sorted(missing)})
    if rule.checks_price_hash and context.supplied_price_hash != context.persisted_price_hash:
        raise DomainError(code=ErrorCode.PRICE_CHANGED)
    if rule.checks_authorization:
        if context.authorization_consumed:
            raise DomainError(code=ErrorCode.AUTHORIZATION_ALREADY_CONSUMED)
        if not context.authorization_valid:
            raise DomainError(code=ErrorCode.AUTHORIZATION_EXPIRED)
    if rule.checks_expiry and context.expires_at is not None and context.now >= context.expires_at:
        raise DomainError(code=ErrorCode.CHECKOUT_EXPIRED)
    if context.actor_type not in rule.actors:
        raise DomainError(code=ErrorCode.FORBIDDEN)

    previous = aggregate.status
    target_status = format_target_status(agg_type, rule.target, aggregate.status)
    aggregate.status = target_status
    audit_event_id = append_transition_event(
        session,
        aggregate_type=aggregate.aggregate_type,
        aggregate_id=aggregate.aggregate_id,
        event_type=event.value,
        actor_type=context.actor_type,
        actor_id=context.actor_id,
        merchant_id=context.merchant_id,
        metadata={"from_status": previous, "to_status": target_status},
    )
    return TransitionResult(previous, target_status, audit_event_id)
