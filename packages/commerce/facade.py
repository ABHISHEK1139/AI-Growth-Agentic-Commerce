"""The commerce capability port the agent layer is allowed to hold (Requirement 23.1).

Why this exists
---------------
``services.agent`` must not import ``sqlalchemy``, directly or through any chain,
because "the model cannot move money" is enforced by an import contract rather
than by convention. A tool that receives a :class:`~sqlalchemy.orm.Session` has,
in practice, unbounded write access: it can reach any table, skip any service,
and commit whenever it likes. Removing the session from the agent's vocabulary
is what makes the boundary mechanical.

So the agent depends on this Protocol, and the composition layer supplies the
implementation. Every method here takes and returns plain domain values or
versioned Pydantic contracts. No ``Session``, no ``Result``, no ORM entity, and
no SQLAlchemy type appears in any signature or return type -- a rule the test
suite pins by inspecting signatures, not by reading this docstring.

Transaction scope belongs to the implementation. Each method is one complete
unit of work: it commits on success and rolls back on failure. A caller cannot
compose two methods into one transaction, which is deliberate -- an agent must
not be able to hold a transaction open across a model round trip.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from packages.schemas.v1 import AuthorizationV1, CheckoutV1, OfferV1, PaymentV1

__all__ = ["CommerceFacade"]


@runtime_checkable
class CommerceFacade(Protocol):
    """Every commerce capability reachable from a tool call.

    ``runtime_checkable`` is here so a test double can be asserted against the
    Protocol at run time; it only checks that the methods exist, so the static
    signature check performed by mypy remains the real guarantee.
    """

    # -- read-only capabilities ------------------------------------------------

    def search_offers(
        self,
        *,
        merchant_id: str,
        category: str | None = None,
        max_price_minor: int | None = None,
        min_memory_gb: int | None = None,
        min_storage_gb: int | None = None,
        max_delivery_days: int | None = None,
        limit: int = 10,
    ) -> list[OfferV1]:
        """Deterministically filtered, ranked, tenant-scoped offers."""
        ...

    def get_offer(self, *, merchant_id: str, offer_id: str) -> OfferV1:
        """One offer within the merchant's tenant scope."""
        ...

    def compare_offers(self, *, merchant_id: str, offer_ids: Sequence[str]) -> list[OfferV1]:
        """Several offers resolved in a single read, for side-by-side comparison."""
        ...

    # -- state-changing capabilities ------------------------------------------
    #
    # Each of these writes its aggregate and that aggregate's audit event inside
    # one transaction. There is deliberately no separate "write the audit event"
    # method for them: splitting a state change from its audit write into two
    # facade calls would put them in two transactions, and they could then
    # diverge.

    def create_checkout(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        offer_id: str,
        quantity: int = 1,
    ) -> CheckoutV1:
        """Reserve inventory and freeze a server-computed price snapshot."""
        ...

    def request_authorization(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
    ) -> AuthorizationV1:
        """Evaluate policy and open an approval bound to one checkout."""
        ...

    def create_payment(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
        authorization_id: str,
        idempotency_key: str | None = None,
    ) -> PaymentV1:
        """Revalidate the gate, then create a payment attempt at the provider."""
        ...

    # -- observability ---------------------------------------------------------

    def record_agent_event(
        self,
        *,
        event_type: str,
        aggregate_id: str,
        actor_type: str,
        actor_id: str | None,
        merchant_id: str | None = None,
        model_version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Append one agent-run audit event and return its identifier.

        This is for run-level events such as intent extraction, which have no
        accompanying aggregate row. Aggregate state changes carry their own audit
        event inside their own transaction and never come through here.
        """
        ...
