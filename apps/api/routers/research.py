"""Product Q&A and Web Research Router (Task 29, 36)."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.auth import AppSettings, current_principal
from packages.security.principals import Principal
from services.research.orchestrator import ResearchOrchestrator

router = APIRouter(prefix="/api/v1/research", tags=["research-qa"])


class ProductQuestionRequest(BaseModel):
    product_id: str = Field(..., description="ID of product being queried")
    question: str = Field(..., description="User's technical or factual question")
    product_title: str | None = Field(default=None, description="Title/model of the product")
    catalog_specs: dict[str, Any] | None = Field(
        default=None, description="Known catalog specifications"
    )
    reviews_summary: dict[str, Any] | None = Field(
        default=None, description="Review aggregate facts"
    )
    offer_data: dict[str, Any] | None = Field(
        default=None, description="Current price, stock, and delivery"
    )
    force_refresh: bool = Field(
        default=False, description="Bypass cache and force fresh web research"
    )


@router.post("/ask")
def ask_product_question(
    request: ProductQuestionRequest,
    settings: AppSettings,
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Route question across DB, reviews, or live web research and return verified answer with evidence citations.

    The search provider is named here, from the settings this application was built
    with. The orchestrator used to read the cached process singleton, so the one
    step in the flow that can leave this host was selected by the environment.
    """
    title = request.product_title or request.product_id
    result = ResearchOrchestrator.investigate(
        product_id=request.product_id,
        product_title=title,
        question=request.question,
        catalog_specs=request.catalog_specs,
        reviews_summary=request.reviews_summary,
        offer_data=request.offer_data,
        force_refresh=request.force_refresh,
        search_provider_name=settings.search_provider,
        searxng_base_url=settings.searxng_base_url,
    )

    with contextlib.suppress(Exception):
        from apps.api.db import get_session_factory
        from services.audit.repository import append_event

        with get_session_factory()() as session:
            append_event(
                session,
                event_type="RESEARCH_PERFORMED",
                aggregate_type="research",
                aggregate_id=request.product_id,
                actor_type="buyer",
                actor_id=None,
                metadata={
                    "question": request.question,
                    "source_type": result.source_type,
                    "confidence_score": result.confidence_score,
                    "confidence_level": result.confidence_level,
                    "from_cache": result.from_cache,
                },
            )
            session.commit()

    return {
        "ok": result.ok,
        "product_id": result.product_id,
        "question": result.question,
        "answer": result.answer,
        "source_type": result.source_type,
        "source_label": result.source_label,
        "source_url": result.source_url,
        "confidence_score": result.confidence_score,
        "confidence_level": result.confidence_level,
        "evidence_items": result.evidence_items,
        "reason_for_web_search": result.reason_for_web_search,
        "transparency_steps": result.transparency_steps,
        "from_cache": result.from_cache,
    }
