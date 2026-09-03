"""Structured price negotiation engine bounded by merchant policy (Task 28, Requirement 22)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode

MAX_NEGOTIATION_ROUNDS = 3


@dataclass(frozen=True, slots=True)
class NegotiationResult:
    status: Literal["accepted", "counter_offered", "rejected"]
    agreed_price_minor: int | None
    counter_price_minor: int | None
    round_number: int
    message: str


class NegotiationEngine:
    """Evaluates multi-round discount negotiations against deterministic policy floors."""

    @staticmethod
    def calculate_floor_price(list_price_minor: int, max_discount_basis_points: int) -> int:
        """Calculate minimum allowable price: list_price * (10000 - max_discount_bps) / 10000."""
        capped_bps = min(max(max_discount_basis_points, 0), 10000)
        return list_price_minor * (10000 - capped_bps) // 10000

    @classmethod
    def evaluate_bid(
        cls,
        *,
        round_number: int,
        proposed_price_minor: int,
        list_price_minor: int,
        max_discount_basis_points: int,
    ) -> NegotiationResult:
        """Process a buyer bid, returning accepted, counter_offered, or raising error if bounds exceeded."""
        if round_number > MAX_NEGOTIATION_ROUNDS:
            raise DomainError(
                f"Maximum negotiation rounds ({MAX_NEGOTIATION_ROUNDS}) exceeded.",
                code=ErrorCode.NEGOTIATION_ROUNDS_EXCEEDED,
            )

        floor_price_minor = cls.calculate_floor_price(list_price_minor, max_discount_basis_points)

        # 1. Proposal meets or exceeds floor -> Accept (clamped to list price if over-bid)
        if proposed_price_minor >= floor_price_minor:
            agreed = min(proposed_price_minor, list_price_minor)
            return NegotiationResult(
                status="accepted",
                agreed_price_minor=agreed,
                counter_price_minor=None,
                round_number=round_number,
                message=f"Offer of {agreed} accepted.",
            )

        # 2. Proposal is below floor
        if round_number < MAX_NEGOTIATION_ROUNDS:
            # Offer counter at floor price
            return NegotiationResult(
                status="counter_offered",
                agreed_price_minor=None,
                counter_price_minor=floor_price_minor,
                round_number=round_number,
                message=f"Proposed amount is below discount limit. Counter offer: {floor_price_minor}.",
            )

        # 3. Final round failure
        raise DomainError(
            f"Proposed price is below the merchant floor price of {floor_price_minor}.",
            code=ErrorCode.MAX_DISCOUNT_EXCEEDED,
        )
