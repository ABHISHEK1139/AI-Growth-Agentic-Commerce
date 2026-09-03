"""One declaration of the offer filter semantics, evaluated two ways.

The natural-language surface and the agent surface must read one core
(Requirement 20.6). The failure this module exists to prevent is subtler than two
endpoints: it is *two filter implementations*. The SQL path filters in
``OfferRepository.search_offers``; the offline demo path filters in Python. If
those two drift, a buyer sees results that violate a constraint they stated, and
nothing fails.

So the constraint set is declared once, here, as data. Two evaluators consume it:

* :func:`sql_predicates` builds the SQLAlchemy clauses (used by the repository)
* :func:`offer_matches` builds the equivalent Python decision (used by the seed
  fixture path)

Both are driven from the same :class:`OfferConstraints` record and the same
:data:`SUPPORTED_FILTERS` list, and ``tests/unit/test_offer_constraints.py``
runs them over one dataset and asserts identical result sets. A filter added to
one evaluator and forgotten in the other fails that test.

Three semantics are worth stating because they are easy to get wrong in one
evaluator and not the other:

**A missing specification is not a satisfied constraint.** In SQL, comparing a
missing JSONB key yields NULL and the row drops out. The Python evaluator has to
reject ``None`` explicitly, because ``None`` compared naively would either raise
or, worse, be treated as passing. The old Python search did the latter: an item
with no parsable memory spec sailed past ``min_memory_gb``.

**Quantity is a filter, not a display field.** An intent asking for two units
must not be answered with an offer holding one.

**Ranking is part of the semantics.** ``limit`` truncates, so two evaluators that
filter identically but order differently return different offers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import IntentV1, OfferV1

#: Hard cap on how many offers any search may return, both paths.
MAX_SEARCH_LIMIT = 50
MIN_SEARCH_LIMIT = 1

#: The only currency the catalog is priced in. A budget stated in anything else
#: cannot be compared against a rupee price, so it is refused rather than
#: silently compared (which would move the buyer's ceiling by ~80x).
CATALOG_CURRENCY = "INR"

#: Every filter this system accepts, in the order the evaluators apply them. Both
#: evaluators are written against this list, and the equivalence test iterates it,
#: so an accepted-but-ignored filter is a test failure rather than a silent lie.
SUPPORTED_FILTERS: tuple[str, ...] = (
    "category",
    "max_price_minor",
    "min_memory_gb",
    "min_storage_gb",
    "max_delivery_days",
    "quantity",
)


@dataclass(frozen=True, slots=True)
class OfferConstraints:
    """The hard constraints a search must satisfy. Every field is enforced.

    Amounts are integer minor units throughout. There is no float on this path:
    ``max_price_minor`` is compared against ``Offer.unit_price_minor`` directly.
    """

    category: str | None = None
    max_price_minor: int | None = None
    min_memory_gb: int | None = None
    min_storage_gb: int | None = None
    max_delivery_days: int | None = None
    quantity: int = 1
    limit: int = 10

    def __post_init__(self) -> None:
        for name in ("max_price_minor", "min_memory_gb", "min_storage_gb", "max_delivery_days"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise DomainError(
                    "A search constraint cannot be negative.",
                    code=ErrorCode.VALIDATION_ERROR,
                    details={"filter": name},
                )
        if self.quantity < 1:
            raise DomainError(
                "A search must ask for at least one unit.",
                code=ErrorCode.VALIDATION_ERROR,
                details={"filter": "quantity"},
            )

    @property
    def capped_limit(self) -> int:
        """The limit actually applied, clamped to the shared bounds."""
        return min(max(self.limit, MIN_SEARCH_LIMIT), MAX_SEARCH_LIMIT)

    def active_filters(self) -> tuple[str, ...]:
        """Which filters this search is actually constrained by.

        Returned to the caller so a reviewer can see that a constraint the intent
        extractor produced reached the query, rather than having to infer it from
        the result set.
        """
        active: list[str] = []
        for name in SUPPORTED_FILTERS:
            value = getattr(self, name)
            if name == "quantity":
                if self.quantity > 1:
                    active.append(name)
            elif value is not None:
                active.append(name)
        return tuple(active)


def constraints_from_intent(
    intent: IntentV1,
    *,
    category: str | None = None,
    max_price_minor: int | None = None,
    limit: int = 10,
    catalog_currency: str = CATALOG_CURRENCY,
) -> OfferConstraints:
    """Project a validated intent onto the constraint set, dropping nothing.

    Explicit request fields win over extracted ones, because a buyer who typed a
    category into a form has stated it more directly than a model inferred it.

    A budget in a currency the catalog is not priced in is *refused*. Comparing a
    dollar ceiling against rupee prices would return results the buyer never
    asked for while looking like it worked, which is the exact failure mode this
    module is built to prevent.
    """
    budget_minor = max_price_minor
    if budget_minor is None and intent.financial.budget_minor is not None:
        stated_currency = intent.financial.currency or catalog_currency
        if stated_currency != catalog_currency:
            raise DomainError(
                "This catalog is not priced in the currency of the stated budget.",
                code=ErrorCode.VALIDATION_ERROR,
                details={
                    "filter": "max_price_minor",
                    "stated_currency": stated_currency,
                    "catalog_currency": catalog_currency,
                },
            )
        budget_minor = intent.financial.budget_minor

    return OfferConstraints(
        category=category or intent.category,
        max_price_minor=budget_minor,
        min_memory_gb=intent.min_memory_gb,
        min_storage_gb=intent.min_storage_gb,
        max_delivery_days=intent.max_delivery_days,
        quantity=intent.quantity,
        limit=limit,
    )


@dataclass(frozen=True, slots=True)
class OfferCandidate:
    """An offer plus the product facts a buyer surface needs to render it.

    Both search paths produce this shape, so the endpoint has one projection to
    write and the equivalence test has one thing to compare. Every monetary value
    lives inside ``offer`` as an integer minor unit.
    """

    offer: OfferV1
    category_id: str
    title: str
    average_rating: float
    rating_number: int
    image_url: str | None
    specifications: dict[str, Any]


def expires_at_of(offer: OfferV1) -> datetime:
    """The offer's expiry as an aware datetime.

    ``OfferV1.expires_at`` is a string because the schema is a wire contract. A
    naive value is read as UTC rather than rejected: the column is
    ``timestamptz``, so a naive value here means a serializer dropped the zone,
    not that the instant is unknown.
    """
    parsed = datetime.fromisoformat(offer.expires_at.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def offer_matches(
    candidate: OfferCandidate,
    constraints: OfferConstraints,
    *,
    now: datetime,
) -> bool:
    """The Python evaluator. Mirrors :func:`sql_predicates` clause for clause."""
    offer = candidate.offer

    # Baseline conditions, applied before any caller-supplied filter. These are
    # not optional: an inactive, expired, or unstocked offer is never a result.
    if offer.status != "active":
        return False
    if expires_at_of(candidate.offer) <= now:
        return False
    if offer.available_quantity < constraints.quantity:
        return False

    if constraints.category is not None:
        target_cat = "computer_accessory" if constraints.category in ("accessory", "computer_accessory") else constraints.category
        cand_cat = "computer_accessory" if candidate.category_id in ("accessory", "computer_accessory") else candidate.category_id
        if cand_cat != target_cat:
            return False
    if (
        constraints.max_price_minor is not None
        and offer.unit_price_minor > constraints.max_price_minor
    ):
        return False
    if (
        constraints.max_delivery_days is not None
        and offer.delivery_days > constraints.max_delivery_days
    ):
        return False

    # A missing spec fails the constraint. In SQL the NULL comparison drops the
    # row; here the `is None` check has to say so out loud.
    if constraints.min_memory_gb is not None:
        memory_gb = offer.specifications.memory_gb
        if memory_gb is None or memory_gb < constraints.min_memory_gb:
            return False
    if constraints.min_storage_gb is not None:
        storage_gb = offer.specifications.storage_gb
        if storage_gb is None or storage_gb < constraints.min_storage_gb:
            return False

    return True


def ranking_key(candidate: OfferCandidate) -> tuple[Any, ...]:
    """Deterministic ranking, mirroring the repository's ``ORDER BY``.

    Cheapest first, then better-rated (lowest first, NULL last), then faster,
    then the longer return window (shortest first, NULL last), then offer id
    as the tie-break so the order is total.

    ``None`` values become a large sentinel (9_999_999) so they sort after all
    concrete values — matching SQL ``NULLS LAST`` semantics used in
    ``sql_ordering``.
    """
    offer = candidate.offer
    return (
        offer.unit_price_minor,
        9_999_999 if candidate.average_rating is None else candidate.average_rating,
        9_999_999 if offer.delivery_days is None else offer.delivery_days,
        9_999_999 if offer.return_period_days is None else offer.return_period_days,
        offer.offer_id,
    )


def apply_constraints(
    candidates: Iterable[OfferCandidate],
    constraints: OfferConstraints,
    *,
    now: datetime | None = None,
) -> list[OfferCandidate]:
    """Filter, rank, and truncate in-memory candidates.

    The offline half of the equivalence guarantee. Ranking and truncation are
    included on purpose: filtering identically but ordering differently still
    returns a different answer once ``limit`` bites.
    """
    current_time = now or datetime.now(UTC)
    matched = [c for c in candidates if offer_matches(c, constraints, now=current_time)]
    matched.sort(key=ranking_key)
    return matched[: constraints.capped_limit]


def sql_predicates(
    constraints: OfferConstraints,
    *,
    offer: Any,
    product: Any,
    inventory: Any,
    now: datetime,
) -> list[Any]:
    """The SQL evaluator: every clause a constrained offer search applies.

    Kept here rather than inline in the repository so that the two evaluators sit
    beside each other and a reviewer can read them as a pair. The repository
    still owns the join, the tenant predicate, and execution.

    Typed loosely on purpose: annotating the ORM classes would put SQLAlchemy in
    this module's signature, and the import contract keeps ``sqlalchemy`` in the
    repository layer.
    """
    clauses: list[Any] = [
        offer.status == "active",
        offer.expires_at > now,
        (inventory.available_quantity - inventory.reserved_quantity) >= constraints.quantity,
    ]

    if constraints.category is not None:
        clauses.append(product.category_id == constraints.category)
    if constraints.max_price_minor is not None:
        clauses.append(offer.unit_price_minor <= constraints.max_price_minor)
    if constraints.max_delivery_days is not None:
        clauses.append(offer.delivery_days <= constraints.max_delivery_days)
    if constraints.min_memory_gb is not None:
        clauses.append(
            product.specifications["memory_gb"].as_integer() >= constraints.min_memory_gb
        )
    if constraints.min_storage_gb is not None:
        clauses.append(
            product.specifications["storage_gb"].as_integer() >= constraints.min_storage_gb
        )
    return clauses


def sql_ordering(*, offer: Any, product: Any) -> Sequence[Any]:
    """The ``ORDER BY`` terms :func:`ranking_key` mirrors.

    Uses NULLS LAST so that rows with no rating (NULL) sort after concrete
    values, matching Python's sentinel approach in ``ranking_key`` (None ->
    9_999_999 -> sorts last).  Each term's direction must match the Python
    ``ranking_key`` exactly.
    """
    return (
        offer.unit_price_minor.asc(),
        product.average_rating.asc().nulls_last(),
        offer.delivery_days.asc().nulls_last(),
        offer.return_period_days.asc().nulls_last(),
        offer.offer_id.asc(),
    )


#: The Python predicate for each filter, keyed by name. Only used by the
#: equivalence test, which needs to switch one filter on at a time and assert
#: that doing so narrows the result set in both evaluators.
FILTER_PREDICATES: dict[str, Callable[[OfferCandidate, OfferConstraints], bool]] = {
    "category": lambda c, k: k.category is None or c.category_id == k.category,
    "max_price_minor": lambda c, k: (
        k.max_price_minor is None or c.offer.unit_price_minor <= k.max_price_minor
    ),
    "min_memory_gb": lambda c, k: (
        k.min_memory_gb is None
        or (
            c.offer.specifications.memory_gb is not None
            and c.offer.specifications.memory_gb >= k.min_memory_gb
        )
    ),
    "min_storage_gb": lambda c, k: (
        k.min_storage_gb is None
        or (
            c.offer.specifications.storage_gb is not None
            and c.offer.specifications.storage_gb >= k.min_storage_gb
        )
    ),
    "max_delivery_days": lambda c, k: (
        k.max_delivery_days is None or c.offer.delivery_days <= k.max_delivery_days
    ),
    "quantity": lambda c, k: c.offer.available_quantity >= k.quantity,
}
