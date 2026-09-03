"""The filter semantics, and the equivalence of the two evaluators.

Three defects motivated this file, and each has a test here that fails if it
returns:

1. the hero query returned nothing, because the searchable set held no laptop
   under the stated budget
2. ``min_memory_gb`` was accepted and then not enforced
3. ``min_storage_gb``, ``max_delivery_days``, and ``quantity`` were extracted and
   then dropped

The tests are written so that *removing* a filter makes them fail. Asserting
"every result satisfies the constraint" alone is not enough: a filter that returns
nothing satisfies every constraint vacuously, and a filter applied to a dataset
with no violating row passes whether it runs or not. So each filter test asserts
both directions — the result set narrows, and a specific offer that violates the
constraint is the one that left.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import IntentFinancialConstraintsV1, IntentV1
from services.offers.constraints import (
    FILTER_PREDICATES,
    MAX_SEARCH_LIMIT,
    SUPPORTED_FILTERS,
    OfferConstraints,
    apply_constraints,
    constraints_from_intent,
    expires_at_of,
    offer_matches,
    ranking_key,
)
from services.offers.seed import load_seed_candidates, search_seed_candidates

MERCHANT = "merchant_demo"

#: The spec's hero scenario, in constraint form: "I need a laptop for programming
#: under 70000 with 16GB RAM".
HERO = OfferConstraints(
    category="laptop",
    max_price_minor=7_000_000,
    min_memory_gb=16,
    limit=10,
)


@pytest.fixture
def candidates():
    return load_seed_candidates(MERCHANT)


def ids_of(results) -> set[str]:
    return {candidate.offer.offer_id for candidate in results}


# --- The hero query ---------------------------------------------------------


def test_hero_query_returns_offers_within_budget_and_memory(candidates) -> None:
    """The scenario that returned `count: 0`. It must return something, and only
    offers that satisfy both stated constraints."""
    results = apply_constraints(candidates, HERO, now=datetime.now(UTC))

    assert results, "the hero query must return at least one offer"
    for candidate in results:
        assert candidate.category_id == "laptop"
        assert candidate.offer.unit_price_minor <= 7_000_000
        assert candidate.offer.specifications.memory_gb is not None
        assert candidate.offer.specifications.memory_gb >= 16


def test_hero_query_is_ranked_cheapest_first(candidates) -> None:
    """Ranking is part of the semantics: `limit` truncates, so order decides which
    offers a buyer is shown at all."""
    results = apply_constraints(candidates, HERO, now=datetime.now(UTC))
    prices = [candidate.offer.unit_price_minor for candidate in results]
    assert prices == sorted(prices)


def test_hero_query_amounts_are_integer_minor_units(candidates) -> None:
    results = apply_constraints(candidates, HERO, now=datetime.now(UTC))
    for candidate in results:
        price = candidate.offer.unit_price_minor
        assert isinstance(price, int)
        assert not isinstance(price, bool)


# --- Baseline conditions ---------------------------------------------------


def test_no_result_is_out_of_stock_or_expired(candidates) -> None:
    """Neither condition is a caller-supplied filter, so both are checked against
    an unconstrained search as well as the hero one."""
    now = datetime.now(UTC)
    for constraints in (OfferConstraints(limit=MAX_SEARCH_LIMIT), HERO):
        results = apply_constraints(candidates, constraints, now=now)
        assert results
        for candidate in results:
            assert candidate.offer.status == "active"
            assert candidate.offer.available_quantity >= 1
            assert expires_at_of(candidate.offer) > now


def test_the_expired_and_unstocked_seed_offers_are_the_ones_excluded(candidates) -> None:
    """Proves the previous test is not passing vacuously: the dataset really does
    contain an expired offer and an unstocked one, and they really are absent."""
    all_ids = ids_of(candidates)
    assert {"off_seed_lap_07", "off_seed_lap_08"} <= all_ids

    visible = ids_of(apply_constraints(candidates, OfferConstraints(limit=MAX_SEARCH_LIMIT)))
    assert "off_seed_lap_07" not in visible  # zero available
    assert "off_seed_lap_08" not in visible  # lapsed in 2020


def test_an_offer_that_expires_exactly_now_is_not_returned(candidates) -> None:
    """The boundary is exclusive in SQL (`expires_at > now`) and must be here too."""
    candidate = next(c for c in candidates if c.offer.offer_id == "off_seed_lap_01")
    at_expiry = expires_at_of(candidate.offer)

    assert offer_matches(candidate, OfferConstraints(), now=at_expiry - timedelta(seconds=1))
    assert not offer_matches(candidate, OfferConstraints(), now=at_expiry)


# --- Each filter narrows ---------------------------------------------------


@pytest.mark.parametrize(
    ("tightened", "expected_excluded"),
    [
        # A stated memory floor must drop the 8GB machine *and* the listing whose
        # memory was never recorded. The second is the case the old search missed:
        # an unparsable spec silently passed the filter.
        ({"min_memory_gb": 16}, {"off_seed_lap_04", "off_seed_lap_09"}),
        # A budget must drop the machine priced above it.
        ({"max_price_minor": 7_000_000}, {"off_seed_lap_05"}),
        # A delivery deadline must drop the nine-day offer.
        ({"max_delivery_days": 3}, {"off_seed_lap_06"}),
        # A storage floor must drop the 256GB machine.
        ({"min_storage_gb": 512}, {"off_seed_lap_10"}),
        # Asking for two units must drop the offer holding one.
        ({"quantity": 2}, {"off_seed_lap_10"}),
    ],
)
def test_each_filter_narrows_the_result_set(candidates, tightened, expected_excluded) -> None:
    """A filter that is accepted and ignored fails here rather than passing."""
    loose = OfferConstraints(category="laptop", limit=MAX_SEARCH_LIMIT)
    tight = replace(loose, **tightened)

    loose_ids = ids_of(apply_constraints(candidates, loose))
    tight_ids = ids_of(apply_constraints(candidates, tight))

    assert tight_ids < loose_ids, f"{tightened} did not narrow the result set"
    assert expected_excluded <= (loose_ids - tight_ids)
    assert tight_ids, "a filter must narrow the set, not empty it"


def test_category_filter_narrows_to_one_category(candidates) -> None:
    unfiltered = apply_constraints(candidates, OfferConstraints(limit=MAX_SEARCH_LIMIT))
    laptops = apply_constraints(
        candidates, OfferConstraints(category="laptop", limit=MAX_SEARCH_LIMIT)
    )

    assert ids_of(laptops) < ids_of(unfiltered)
    assert {c.category_id for c in laptops} == {"laptop"}


def test_every_supported_filter_is_actually_evaluated() -> None:
    """Guards against a filter being declared and then never consulted.

    Both evaluators are written against ``SUPPORTED_FILTERS``, so a name added to
    that list without a predicate is a gap this catches at the declaration.
    """
    assert set(SUPPORTED_FILTERS) == set(FILTER_PREDICATES)
    assert set(SUPPORTED_FILTERS) <= set(OfferConstraints.__dataclass_fields__)


def test_limit_is_capped_and_floored() -> None:
    assert OfferConstraints(limit=1000).capped_limit == MAX_SEARCH_LIMIT
    assert OfferConstraints(limit=0).capped_limit == 1


def test_limit_truncates_after_ranking(candidates) -> None:
    full = apply_constraints(candidates, replace(HERO, limit=MAX_SEARCH_LIMIT))
    assert len(full) > 2
    truncated = apply_constraints(candidates, replace(HERO, limit=2))
    assert [c.offer.offer_id for c in truncated] == [c.offer.offer_id for c in full[:2]]


def test_active_filters_reports_what_was_applied() -> None:
    assert HERO.active_filters() == ("category", "max_price_minor", "min_memory_gb")
    assert OfferConstraints().active_filters() == ()
    assert OfferConstraints(quantity=3).active_filters() == ("quantity",)


# --- The two evaluators agree ---------------------------------------------


def _python_reference(candidates, constraints, now):
    """An independent decision built straight from ``FILTER_PREDICATES``.

    Deliberately not calling ``offer_matches``: comparing that function to itself
    would prove nothing. This composes the per-filter predicates the equivalence
    contract is declared in, plus the baseline conditions, and asserts the
    composed answer matches.
    """
    selected = []
    for candidate in candidates:
        if candidate.offer.status != "active":
            continue
        if expires_at_of(candidate.offer) <= now:
            continue
        if all(FILTER_PREDICATES[name](candidate, constraints) for name in SUPPORTED_FILTERS):
            selected.append(candidate)
    selected.sort(key=ranking_key)
    return selected[: constraints.capped_limit]


CONSTRAINT_MATRIX = [
    OfferConstraints(limit=MAX_SEARCH_LIMIT),
    HERO,
    replace(HERO, limit=MAX_SEARCH_LIMIT),
    replace(HERO, min_storage_gb=512),
    replace(HERO, max_delivery_days=2),
    replace(HERO, quantity=3),
    OfferConstraints(category="smartphone", limit=MAX_SEARCH_LIMIT),
    OfferConstraints(category="laptop", max_price_minor=1, limit=MAX_SEARCH_LIMIT),
    OfferConstraints(category="no_such_category", limit=MAX_SEARCH_LIMIT),
    OfferConstraints(min_memory_gb=64, limit=MAX_SEARCH_LIMIT),
]


@pytest.mark.parametrize("constraints", CONSTRAINT_MATRIX)
def test_declared_filters_and_the_evaluator_agree(candidates, constraints) -> None:
    """The offline evaluator must match the declared per-filter semantics exactly.

    This is the half of the SQL/offline equivalence guarantee that runs without a
    database. The other half — that ``sql_predicates`` produces the same answer
    over the same rows — needs PostgreSQL and lives in
    ``tests/integration/test_offer_search_equivalence.py``.
    """
    now = datetime.now(UTC)
    assert [c.offer.offer_id for c in apply_constraints(candidates, constraints, now=now)] == [
        c.offer.offer_id for c in _python_reference(candidates, constraints, now)
    ]


@pytest.mark.parametrize("constraints", CONSTRAINT_MATRIX)
def test_the_sql_and_offline_evaluators_are_built_from_one_declaration(constraints) -> None:
    """Every active filter contributes a SQL clause.

    A filter honoured in Python and forgotten in SQL is the drift this whole
    module exists to prevent. Counting clauses is a structural check that needs no
    database: three baseline clauses plus one per active filter, except
    ``quantity``, which tightens the baseline stock clause rather than adding one.
    """
    from services.catalog.models import Product
    from services.inventory.models import Inventory
    from services.offers.constraints import sql_predicates
    from services.offers.models import Offer

    clauses = sql_predicates(
        constraints,
        offer=Offer,
        product=Product,
        inventory=Inventory,
        now=datetime.now(UTC),
    )
    added = [name for name in constraints.active_filters() if name != "quantity"]
    assert len(clauses) == 3 + len(added)


# --- Intent projection -----------------------------------------------------


def _intent(**overrides) -> IntentV1:
    fields = {
        "schema_version": "1.0",
        "query": "laptop for programming",
        "category": "laptop",
        "financial": IntentFinancialConstraintsV1(budget_minor=7_000_000, currency="INR"),
        "min_memory_gb": 16,
        "min_storage_gb": None,
        "max_delivery_days": None,
        "quantity": 1,
    }
    fields.update(overrides)
    return IntentV1(**fields)


def test_intent_projection_carries_every_extracted_constraint() -> None:
    """Nothing the extractor produced may be dropped on the way to the query."""
    constraints = constraints_from_intent(
        _intent(min_storage_gb=512, max_delivery_days=3, quantity=2), limit=7
    )
    assert constraints.category == "laptop"
    assert constraints.max_price_minor == 7_000_000
    assert constraints.min_memory_gb == 16
    assert constraints.min_storage_gb == 512
    assert constraints.max_delivery_days == 3
    assert constraints.quantity == 2
    assert constraints.limit == 7


def test_explicit_request_fields_win_over_extracted_ones() -> None:
    constraints = constraints_from_intent(
        _intent(), category="smartphone", max_price_minor=2_000_000
    )
    assert constraints.category == "smartphone"
    assert constraints.max_price_minor == 2_000_000


def test_a_budget_in_an_unpriceable_currency_is_refused_not_dropped() -> None:
    """Silently comparing a dollar ceiling against rupee prices would move the
    buyer's stated limit by roughly eighty times, and look like it worked."""
    intent = _intent(financial=IntentFinancialConstraintsV1(budget_minor=90_000, currency="USD"))
    with pytest.raises(DomainError) as exc_info:
        constraints_from_intent(intent)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
    assert exc_info.value.details["stated_currency"] == "USD"


def test_a_negative_constraint_is_refused() -> None:
    with pytest.raises(DomainError) as exc_info:
        OfferConstraints(max_price_minor=-1)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(DomainError):
        OfferConstraints(quantity=0)


# --- The seed catalog itself ----------------------------------------------


def test_the_seed_catalog_can_answer_the_hero_query() -> None:
    """The offline demo posture must not be the one that returns nothing."""
    results = search_seed_candidates(merchant_id=MERCHANT, constraints=HERO)
    assert len(results) >= 3
    for candidate in results:
        assert candidate.offer.unit_price_minor <= 7_000_000
        assert candidate.offer.specifications.memory_gb is not None
        assert candidate.offer.specifications.memory_gb >= 16


def test_seed_offers_are_stamped_with_the_asking_merchant() -> None:
    for candidate in load_seed_candidates("merchant_other"):
        assert candidate.offer.merchant_id == "merchant_other"


def test_seed_prices_are_labelled_as_not_market_derived() -> None:
    """Requirement 27.8: wherever a price appears, its provenance appears too."""
    for candidate in load_seed_candidates(MERCHANT):
        assert candidate.offer.pricing_source in {
            "merchant_configured",
            "synthetic_band_random",
        }
