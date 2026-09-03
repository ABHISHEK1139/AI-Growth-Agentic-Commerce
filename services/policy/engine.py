"""Pure deterministic policy evaluation engine (Requirement 12, Properties 17, 18, 19)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from packages.errors.registry import ErrorCode


@dataclass(frozen=True, slots=True)
class PolicyInputs:
    """Explicit parameters required for a deterministic policy decision."""

    buyer_id: str
    merchant_id: str
    category_id: str
    amount_minor: int
    currency: str
    offer_status: str
    offer_expires_at: datetime
    available_quantity: int
    policy_version: str
    payment_method: str = "card"

    def compute_hash(self) -> str:
        data = {
            "buyer_id": self.buyer_id,
            "merchant_id": self.merchant_id,
            "category_id": self.category_id,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "offer_status": self.offer_status,
            "offer_expires_at": self.offer_expires_at.isoformat(),
            "available_quantity": self.available_quantity,
            "policy_version": self.policy_version,
            "payment_method": self.payment_method,
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MerchantPolicyRules:
    merchant_id: str
    version: str
    max_transaction_minor: int
    auto_approval_limit_minor: int
    max_discount_basis_points: int = 10000
    allowed_categories: tuple[str, ...] = ()
    blocked_categories: tuple[str, ...] = ()
    allowed_payment_methods: tuple[str, ...] = ("card", "upi", "netbanking")
    allow_out_of_stock: bool = False


@dataclass(frozen=True, slots=True)
class BuyerPolicyRules:
    buyer_id: str
    version: str
    max_transaction_minor: int
    auto_approval_limit_minor: int
    allowed_merchants: tuple[str, ...] = ()
    allowed_categories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDecisionResult:
    """Immutable outcome of policy evaluation."""

    decision: str  # ALLOW, REQUIRE_APPROVAL, BLOCK
    reason_code: str
    policy_version: str
    inputs_hash: str


def evaluate_policy(
    inputs: PolicyInputs,
    merchant_rules: MerchantPolicyRules,
    buyer_policy: BuyerPolicyRules,
    now: datetime,
) -> PolicyDecisionResult:
    """Pure evaluation function with no database, clock, or network access.

    Evaluates rules in strict order:
      1. Offer status and expiry
      2. Inventory availability
      3. Currency allowlist
      4. Category allowlist/blocklist
      5. Merchant allowlist
      6. Maximum transaction limit
      7. Policy version match
      8. Auto-approval limit
      9. Allow
    """
    inputs_hash = inputs.compute_hash()
    policy_ver = buyer_policy.version

    # 1. Offer status and expiry
    from datetime import UTC

    exp = inputs.offer_expires_at
    if isinstance(exp, datetime) and exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    cur_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if inputs.offer_status != "active" or cur_now >= exp:
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.OFFER_EXPIRED.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 2. Inventory availability
    if inputs.available_quantity <= 0 and not merchant_rules.allow_out_of_stock:
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.INVENTORY_UNAVAILABLE.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 3. Currency check — only INR is supported in the current catalog
    #    The docstring promised this check; it was previously missing entirely.
    #    `allowed_payment_methods` covers payment rail, not currency; currency
    #    is a separate property of the checkout and must be validated here.
    SUPPORTED_CURRENCIES = frozenset({"INR", "USD"})
    if inputs.currency.upper() not in SUPPORTED_CURRENCIES:
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.VALIDATION_ERROR.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    if (
        merchant_rules.blocked_categories
        and inputs.category_id in merchant_rules.blocked_categories
    ):
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.CATEGORY_NOT_ALLOWED.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    if (
        merchant_rules.allowed_categories
        and inputs.category_id not in merchant_rules.allowed_categories
    ):
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.CATEGORY_NOT_ALLOWED.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    if (
        buyer_policy.allowed_categories
        and inputs.category_id not in buyer_policy.allowed_categories
    ):
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.CATEGORY_NOT_ALLOWED.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 4. Merchant allowlist
    if buyer_policy.allowed_merchants and inputs.merchant_id not in buyer_policy.allowed_merchants:
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.MERCHANT_NOT_ALLOWED.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 5. Maximum transaction limit (lowest ceiling wins)
    effective_max_limit = min(
        merchant_rules.max_transaction_minor, buyer_policy.max_transaction_minor
    )
    if inputs.amount_minor > effective_max_limit:
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.AMOUNT_ABOVE_MAX_LIMIT.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 6. Policy version mismatch
    if (
        inputs.policy_version != buyer_policy.version
        and inputs.policy_version != merchant_rules.version
    ):
        return PolicyDecisionResult(
            decision="BLOCK",
            reason_code=ErrorCode.POLICY_VERSION_MISMATCH.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 7. Auto-approval limit
    effective_auto_limit = min(
        merchant_rules.auto_approval_limit_minor, buyer_policy.auto_approval_limit_minor
    )
    if inputs.amount_minor > effective_auto_limit:
        return PolicyDecisionResult(
            decision="REQUIRE_APPROVAL",
            reason_code=ErrorCode.AMOUNT_ABOVE_AUTO_LIMIT.value,
            policy_version=policy_ver,
            inputs_hash=inputs_hash,
        )

    # 8. Unconditional allow
    return PolicyDecisionResult(
        decision="ALLOW",
        reason_code="OK",
        policy_version=policy_ver,
        inputs_hash=inputs_hash,
    )
