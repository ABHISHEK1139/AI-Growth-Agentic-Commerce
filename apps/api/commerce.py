"""The composition-layer implementation of the tool-facing commerce port.

This module is the seam where a session factory meets the domain services. It is
the *only* holder of a session on the agent's path to commerce: the agent depends
on :class:`packages.commerce.CommerceFacade`, this class satisfies it, and the
session never travels outward past these method bodies.

Transaction scope
-----------------
Each method is one unit of work. ``_unit_of_work`` opens a session, commits on
success, rolls back on any exception, and always closes. Two consequences worth
stating, because they are the point of the design:

* A state change and its audit event share one transaction. ``CheckoutService``
  writes the checkout row and appends ``CHECKOUT_CREATED`` against the same
  session, and this facade commits once, after both. There is no facade method
  that writes an audit event *for* a state change, so a caller cannot split the
  pair across two transactions even by accident.
* An agent cannot hold a transaction open across a model round trip, because it
  never holds a transaction at all.

Returned values are versioned Pydantic contracts, built by the services before
the commit. They are not ORM instances, so nothing expires or lazy-loads after
the session closes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from apps.api.db import get_session_factory
from packages.schemas.v1 import AuthorizationV1, CheckoutV1, OfferV1, PaymentV1
from services.audit.repository import append_event
from services.authorization.service import AuthorizationService
from services.checkout.service import CheckoutService
from services.offers.service import OfferService
from services.payments.service import PaymentService

__all__ = ["SessionScopedCommerceFacade", "get_commerce_facade"]


def _default_session_factory() -> Session:
    """Resolve the process-wide factory lazily.

    Resolving at call time rather than at construction keeps importing this
    module free of any requirement for a reachable database, which is what lets
    the unit suite import it without Docker.
    """
    return get_session_factory()()


class SessionScopedCommerceFacade:
    """Satisfies :class:`packages.commerce.CommerceFacade` over real services."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        *,
        offer_service: OfferService | None = None,
        checkout_service: CheckoutService | None = None,
        authorization_service: AuthorizationService | None = None,
        payment_service: PaymentService | None = None,
    ) -> None:
        self._session_factory = session_factory or _default_session_factory
        self._offers = offer_service or OfferService()
        self._checkouts = checkout_service or CheckoutService()
        self._authorizations = authorization_service or AuthorizationService()
        self._payments = payment_service or PaymentService()

    @contextmanager
    def _unit_of_work(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            # A partially applied state change is worse than a failed one: it
            # would leave an aggregate advanced with no audit event explaining it.
            session.rollback()
            raise
        finally:
            session.close()

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
        try:
            with self._unit_of_work() as session:
                return self._offers.search_offers(
                    session,
                    merchant_id=merchant_id,
                    category=category,
                    max_price_minor=max_price_minor,
                    min_memory_gb=min_memory_gb,
                    min_storage_gb=min_storage_gb,
                    max_delivery_days=max_delivery_days,
                    limit=limit,
                )
        except Exception:
            from apps.api.catalog_source import search_catalog
            from services.offers.constraints import OfferConstraints

            outcome = search_catalog(
                merchant_id=merchant_id,
                constraints=OfferConstraints(
                    category=category,
                    max_price_minor=max_price_minor,
                    min_memory_gb=min_memory_gb,
                    min_storage_gb=min_storage_gb,
                    max_delivery_days=max_delivery_days,
                    limit=limit,
                ),
            )
            return [c.offer for c in outcome.candidates]

    def get_offer(self, *, merchant_id: str, offer_id: str) -> OfferV1:
        with self._unit_of_work() as session:
            return self._offers.get_offer_by_id(session, merchant_id=merchant_id, offer_id=offer_id)

    def compare_offers(self, *, merchant_id: str, offer_ids: Sequence[str]) -> list[OfferV1]:
        # One unit of work for the whole comparison, so every offer in the set is
        # read at the same point in time.
        with self._unit_of_work() as session:
            return [
                self._offers.get_offer_by_id(session, merchant_id=merchant_id, offer_id=offer_id)
                for offer_id in offer_ids
            ]

    # -- state-changing capabilities ------------------------------------------

    def create_checkout(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        offer_id: str,
        quantity: int = 1,
    ) -> CheckoutV1:
        with self._unit_of_work() as session:
            return self._checkouts.create_checkout(
                session,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                offer_id=offer_id,
                quantity=quantity,
            )

    def request_authorization(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
    ) -> AuthorizationV1:
        with self._unit_of_work() as session:
            return self._authorizations.request_authorization(
                session,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                checkout_id=checkout_id,
            )

    def create_payment(
        self,
        *,
        buyer_id: str,
        merchant_id: str,
        checkout_id: str,
        authorization_id: str,
        idempotency_key: str | None = None,
    ) -> PaymentV1:
        with self._unit_of_work() as session:
            return self._payments.create_payment(
                session,
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                checkout_id=checkout_id,
                authorization_id=authorization_id,
                idempotency_key=idempotency_key,
            )

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
        try:
            with self._unit_of_work() as session:
                return append_event(
                    session,
                    event_type=event_type,
                    aggregate_type="agent_run",
                    aggregate_id=aggregate_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    merchant_id=merchant_id,
                    model_version=model_version,
                    metadata=dict(metadata) if metadata is not None else None,
                )
        except Exception:
            return aggregate_id


def get_commerce_facade() -> SessionScopedCommerceFacade:
    """Composition root for the agent's commerce dependency."""
    return SessionScopedCommerceFacade()
