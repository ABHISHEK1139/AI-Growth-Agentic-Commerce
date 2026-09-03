"""Interactive Natural Language AI Search and Product Exploration endpoint.

The pipeline is unchanged in shape — guard, then intent extraction, then catalog
search, then research — but the third step now goes through the same deterministic,
tenant-scoped, database-backed offer query the agent surface and the merchant
surface use. It used to search a list of product dictionaries held in a Python
module, which produced three failures at once:

* the hero query returned nothing, because that list held no laptop under ₹70,000
* ``min_memory_gb`` was accepted and then not enforced for any listing whose
  memory string did not parse, so a buyer could be shown a machine that fails a
  requirement they stated
* ``min_storage_gb``, ``max_delivery_days``, and ``quantity`` were extracted from
  the prompt and then dropped on the floor

The underlying defect was one system with two search implementations. There is now
one set of filter semantics, in :mod:`services.offers.constraints`, with a SQL
evaluator and an offline evaluator driven from it, and the response says which one
answered.

The first step also answers differently now. A guard refusal used to be HTTP 200
with ``{"ok": false, "guard_blocked": true, ...}`` in a body shaped like a result,
so a client checking only the status read a security block as a search that found
nothing. It raises ``PROMPT_INJECTION_SUSPECTED`` and leaves through the same error
envelope as every other failure.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.auth import AppSettings, optional_principal
from apps.api.catalog_source import SEED_FALLBACK_NOTE, search_catalog
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.logging import get_logger
from packages.schemas.v1 import OfferV1
from packages.security.principals import Principal
from services.agent.guard import PromptSafetyClassifier
from services.agent.intent import (
    INTENT_EXTRACTION_SYSTEM_PROMPT,
    IntentValidator,
)
from services.agent.model import get_model_provider
from services.offers.constraints import (
    OfferCandidate,
    OfferConstraints,
    constraints_from_intent,
)
from services.research.worker import ResearchWorker

router = APIRouter(tags=["explore-and-search"])
logger = get_logger(__name__)

#: The audit event a guard refusal writes. Exactly one line per block, carrying
#: the evaluator that produced the verdict and the category it assigned, so the
#: record says both *what* was refused and *what it cost* to find out.
GUARD_BLOCK_EVENT = "PROMPT_BLOCKED"

#: Declared in the response when intent extraction failed and the search ran on
#: the explicit request fields alone. Without it a filterless result set is
#: indistinguishable from a filterless query, which is how a model returning prose
#: instead of JSON would look like a successful broad search.
INTENT_FALLBACK_NOTE = (
    "Intent extraction failed; only the filters supplied directly on the request were applied."
)


class ExploreQueryRequest(BaseModel):
    prompt: str = Field(
        ..., description="Natural language shopping query or product specifications"
    )
    category: str | None = None
    max_price_minor: int | None = Field(default=None, ge=0)
    limit: int = Field(default=10, ge=1, le=50)


def _offer_payload(candidate: OfferCandidate) -> dict[str, Any]:
    """Render one offer for a buyer surface.

    Every monetary value is the integer minor unit straight off the offer record.
    No arithmetic happens here — not even a discount percentage, which the previous
    version computed in floating point on a price.
    """
    offer: OfferV1 = candidate.offer
    return {
        "offer_id": offer.offer_id,
        "product_id": offer.product_id,
        "merchant_id": offer.merchant_id,
        "title": candidate.title,
        "category": candidate.category_id,
        "unit_price_minor": offer.unit_price_minor,
        "currency": offer.currency,
        "available_stock": offer.available_quantity,
        "delivery_days": offer.delivery_days,
        "return_period_days": offer.return_period_days,
        "expires_at": offer.expires_at,
        "offer_version": offer.offer_version,
        # Surfaced rather than hidden: a reviewer should be able to see that a
        # price was configured or generated, never scraped from a market.
        "pricing_source": offer.pricing_source,
        "rating": candidate.average_rating,
        "reviews_count": candidate.rating_number,
        "image_url": candidate.image_url,
        "specs": candidate.specifications,
    }


@router.post("/api/explore")
@router.post("/api/v1/agent/explore")
def explore_products(
    request: ExploreQueryRequest,
    settings: AppSettings,
    principal: Principal | None = Depends(optional_principal),
) -> dict[str, Any]:
    """GuardLLM -> Intent Extraction -> Deterministic Catalog Query -> Research.

    Configuration arrives as ``settings``, resolved from the application this
    request is being served by. It used to be read from the cached process
    singleton, so the endpoint answered according to the environment no matter what
    the application had been built with. That is not a cosmetic difference: a probe
    that handed ``create_app`` a loopback model endpoint still sent its completion
    request to the hosted provider named in ``.env``, and was billed for it before
    anyone noticed.
    """
    # 1. GuardLLM Safety & Prompt Injection Check
    #
    # A refusal is a failure, so it leaves through the error envelope with the
    # registry code for it. It used to answer HTTP 200 with `ok: false` in a body
    # shaped like a result, which meant a client that only checked the status saw
    # a security block as a successful search that happened to find nothing.
    safety = PromptSafetyClassifier.evaluate(request.prompt, config=settings.guard_config())
    if not safety.is_safe:
        # One line, written before the raise, because the error middleware sees
        # only the code and never the assessment that produced it.
        logger.warning(
            "prompt refused by the guard",
            extra={
                "event": GUARD_BLOCK_EVENT,
                "error_code": ErrorCode.PROMPT_INJECTION_SUSPECTED.value,
                "evaluator": safety.evaluator,
                "threat_category": safety.threat_category,
            },
        )
        raise DomainError(
            f"Prompt rejected by the guard: {safety.threat_category}",
            code=ErrorCode.PROMPT_INJECTION_SUSPECTED,
            details={
                "threat_category": safety.threat_category,
                "evaluator": safety.evaluator,
            },
        )

    # 2. Extract Intent using LLM
    #
    # Every field the extractor produces is carried into the query below. A field
    # that reached this dictionary and not the constraint set would be a filter the
    # buyer stated and the search ignored, which is the defect this endpoint had.
    intent_summary: dict[str, Any]
    constraints: OfferConstraints
    intent_degraded = False

    try:
        provider = get_model_provider(settings.model_gateway_config())
        model_res = provider.generate(
            request.prompt,
            system_prompt=INTENT_EXTRACTION_SYSTEM_PROMPT,
            schema={"type": "json_object"},
        )
        raw_intent = model_res.parsed_json or {}
        if "query" not in raw_intent:
            raw_intent["query"] = request.prompt[:100]

        validated = IntentValidator.validate_dict(raw_intent, prompt=request.prompt)

        # Raises rather than dropping a constraint it cannot honour — a budget in
        # a currency this catalog is not priced in, for instance.
        constraints = constraints_from_intent(
            validated,
            category=request.category,
            max_price_minor=request.max_price_minor,
            limit=request.limit,
        )
        intent_summary = {
            "query": validated.query,
            "category": validated.category,
            "budget_minor": validated.financial.budget_minor,
            "currency": validated.financial.currency or settings.default_currency,
            "min_memory_gb": validated.min_memory_gb,
            "min_storage_gb": validated.min_storage_gb,
            "max_delivery_days": validated.max_delivery_days,
            "quantity": validated.quantity,
        }
    except DomainError:
        # A typed refusal from the constraint layer (e.g. a budget in a currency
        # the catalog is not priced in) is an answer, not a degradation.
        # Swallowing it here downgraded a deliberate refusal into an
        # unfiltered search presented as a result.
        raise
    except Exception as exc:
        # The model timing out must not take the catalog down with it. The explicit
        # request fields are still honoured; nothing is invented to replace the
        # fields the extractor would have produced.
        #
        # It is declared, though. A local model answering with prose rather than
        # JSON lands here, and an undeclared fallback would present the resulting
        # unfiltered result set as an answer to a question it never parsed.
        intent_degraded = True
        logger.warning(
            "intent extraction failed; searching on the request fields alone",
            extra={
                "event": "INTENT_EXTRACTION_DEGRADED",
                "error_kind": type(exc).__name__,
            },
        )
        constraints = OfferConstraints(
            category=request.category,
            max_price_minor=request.max_price_minor,
            limit=request.limit,
        )
        intent_summary = {
            "query": request.prompt,
            "category": request.category,
            "budget_minor": request.max_price_minor,
            "currency": settings.default_currency,
            "min_memory_gb": None,
            "min_storage_gb": None,
            "max_delivery_days": None,
            "quantity": 1,
        }

    # 3. Deterministic catalog query. Filtering happens in SQL when the published
    #    catalog is reachable, and in the mirrored Python evaluator when it is not.
    merchant_id = (principal.merchant_id if principal else None) or settings.default_merchant_id
    outcome = search_catalog(
        merchant_id=merchant_id,
        constraints=constraints,
    )
    products = [_offer_payload(candidate) for candidate in outcome.candidates]

    warnings: list[str] = []
    if outcome.is_degraded:
        warnings.append(SEED_FALLBACK_NOTE)
    if intent_degraded:
        warnings.append(INTENT_FALLBACK_NOTE)

    # 4. Synthesize research evidence for the top result
    top_candidate = outcome.candidates[0] if outcome.candidates else None
    evidence_list: list[dict[str, Any]] = []
    if top_candidate is not None:
        research = ResearchWorker.execute_product_research(
            product_id=top_candidate.offer.product_id,
            query=request.prompt,
            catalog_specs=top_candidate.specifications,
            external_urls=[],
            # The one outbound call on this path that no setting could switch off.
            # `search_provider="null"` is the default and it means "keep this
            # deployment off the network", so a search endpoint was being called
            # from every explore request that found no catalog fact, in a
            # configuration that had asked for no search provider at all.
            enable_web_search=settings.search_provider != "null",
        )
        evidence_list = [
            {
                "claim": ev.claim,
                "citation_type": ev.citation_type,
                "source_url": ev.source_url,
                "confidence": ev.confidence,
            }
            for ev in research.evidence
        ]

    return {
        "ok": True,
        "guard_blocked": False,
        "evaluator": safety.evaluator,
        "intent": intent_summary,
        "products": products,
        "count": len(products),
        # Provenance, not decoration. Without it a reviewer cannot tell a real
        # catalog answer from an offline one, which is how a hardcoded list stood
        # in for the catalog unnoticed.
        "catalog_source": outcome.source,
        "applied_filters": list(constraints.active_filters()),
        "warnings": warnings,
        "research": {
            "evidence": evidence_list,
            "product_id": top_candidate.offer.product_id if top_candidate else None,
            "source_count": len(evidence_list),
        },
    }
