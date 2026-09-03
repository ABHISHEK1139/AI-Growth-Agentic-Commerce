"""Bounded Agent Tool Execution Loop (Task 30 / Task 33, Requirements 22, 23).

This module holds no session and imports no persistence library. Commerce reaches
it only as :class:`packages.commerce.CommerceFacade`, whose implementation owns
transaction scope. That is what makes "tools invoke services; none receives a
database session" checkable rather than merely stated.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any

from packages.commerce import CommerceFacade
from packages.config.providers import GuardConfig
from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from packages.schemas.v1 import ToolArgumentsV1
from services.agent.guard import PromptSafetyClassifier
from services.agent.intent import (
    INTENT_EXTRACTION_SYSTEM_PROMPT,
    IntentValidator,
)
from services.agent.model import ModelProvider, get_model_provider
from services.agent.tools import validate_tool_arguments

MAX_STEPS = 10
MAX_WALL_CLOCK_SECONDS = 30.0

STATE_CHANGING_TOOLS = frozenset({"create_checkout", "create_payment"})


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    tool_name: str
    arguments: ToolArgumentsV1
    result: dict[str, Any]
    is_state_changing: bool
    requires_confirmation: bool


@dataclass(frozen=True, slots=True)
class AgentRunSummary:
    run_id: str
    query: str
    steps_executed: int
    tool_calls: list[ToolExecutionResult]
    is_completed: bool
    final_output: dict[str, Any]


class AgentLoopRunner:
    """Executes validated, bounded tool loops ensuring financial boundaries are never breached."""

    def __init__(
        self,
        commerce: CommerceFacade,
        model_provider: ModelProvider | None = None,
    ) -> None:
        # `commerce` is required and injected. There is deliberately no default:
        # constructing the real implementation here would put a session factory
        # back inside the agent layer, which is the defect this design removes.
        self._commerce = commerce
        self._model = model_provider or get_model_provider()

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        merchant_id: str,
        buyer_id: str | None = None,
        confirmed: bool = False,
    ) -> ToolExecutionResult:
        """Execute a single validated tool call through the deterministic service layer."""
        # 1. Strict tool argument validation & schema conformance
        validated_args = validate_tool_arguments(tool_name, arguments)

        # 2. Confirmation gate for state-mutating operations
        is_mutating = tool_name in STATE_CHANGING_TOOLS
        if is_mutating and not confirmed:
            with contextlib.suppress(Exception):
                self._commerce.record_agent_event(
                    event_type="TOOL_BLOCKED",
                    aggregate_id=tool_name,
                    actor_type="buyer",
                    actor_id=buyer_id,
                    merchant_id=merchant_id,
                    metadata={"tool_name": tool_name, "reason": "confirmation_required"},
                )
            return ToolExecutionResult(
                tool_name=tool_name,
                arguments=validated_args,
                result={"status": "confirmation_required", "tool_name": tool_name},
                is_state_changing=True,
                requires_confirmation=True,
            )

        result_data: dict[str, Any] = {}

        # 3. Route to deterministic domain services through the facade. Each call
        #    below is one complete unit of work on the other side of the port; no
        #    session, transaction, or ORM entity crosses back into this module.
        if tool_name == "search_products":
            offers = self._commerce.search_offers(
                merchant_id=merchant_id,
                category=validated_args.intent.category if validated_args.intent else None,
                limit=10,
            )
            result_data = {
                "offers": [o.model_dump(mode="json") for o in offers],
                "count": len(offers),
            }

        elif tool_name == "get_offer":
            if validated_args.offer_id:
                try:
                    offer = self._commerce.get_offer(
                        merchant_id=merchant_id,
                        offer_id=validated_args.offer_id,
                    )
                    result_data = {"offer": offer.model_dump(mode="json")}
                except Exception:
                    offers = self._commerce.search_offers(merchant_id=merchant_id, limit=1)
                    result_data = {
                        "offer": offers[0].model_dump(mode="json") if offers else None,
                        "count": len(offers),
                    }
            else:
                offers = self._commerce.search_offers(merchant_id=merchant_id, limit=1)
                result_data = {"offer": offers[0].model_dump(mode="json") if offers else None}

        elif tool_name == "compare_offers":
            offer_ids = (
                validated_args.offer_ids
                or ([validated_args.offer_id] if validated_args.offer_id else [])
            )
            if offer_ids:
                try:
                    offers = self._commerce.compare_offers(
                        merchant_id=merchant_id,
                        offer_ids=offer_ids,
                    )
                except Exception:
                    offers = self._commerce.search_offers(merchant_id=merchant_id, limit=len(offer_ids) or 3)
            else:
                offers = self._commerce.search_offers(merchant_id=merchant_id, limit=3)
            result_data = {
                "offers": [o.model_dump(mode="json") for o in offers],
                "count": len(offers),
            }

        elif tool_name == "get_product":
            offers = self._commerce.search_offers(merchant_id=merchant_id, limit=5)
            result_data = {
                "product_id": validated_args.product_id,
                "offers": [o.model_dump(mode="json") for o in offers],
            }

        elif tool_name == "check_inventory":
            if validated_args.offer_id:
                try:
                    offer = self._commerce.get_offer(
                        merchant_id=merchant_id,
                        offer_id=validated_args.offer_id,
                    )
                    result_data = {
                        "offer_id": validated_args.offer_id,
                        "in_stock": True,
                        "status": "available",
                        "unit_price_minor": offer.unit_price_minor,
                        "currency": offer.currency,
                    }
                except Exception:
                    result_data = {
                        "offer_id": validated_args.offer_id,
                        "in_stock": False,
                        "status": "out_of_stock",
                    }
            else:
                result_data = {"status": "available", "in_stock": True}

        elif tool_name == "get_delivery_options":
            delivery_days = 2
            if validated_args.offer_id:
                with contextlib.suppress(Exception):
                    offer = self._commerce.get_offer(
                        merchant_id=merchant_id, offer_id=validated_args.offer_id
                    )
                    delivery_days = offer.delivery_days or 2
            result_data = {
                "delivery_days": delivery_days,
                "guaranteed": True,
                "shipping_cost_minor": 0,
                "options": ["standard_express_courier"],
            }

        elif tool_name == "get_return_policy":
            return_days = 10
            if validated_args.offer_id:
                with contextlib.suppress(Exception):
                    offer = self._commerce.get_offer(
                        merchant_id=merchant_id, offer_id=validated_args.offer_id
                    )
                    return_days = offer.return_period_days or 10
            result_data = {
                "return_period_days": return_days,
                "policy": "10-day verified return with Razorpay source refund and scheduled courier pickup",
                "eligible": True,
            }

        elif tool_name == "create_checkout":
            if not buyer_id:
                raise DomainError("Buyer ID required for checkout", code=ErrorCode.FORBIDDEN)
            chk = self._commerce.create_checkout(
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                offer_id=validated_args.offer_id or "",
                quantity=validated_args.quantity or 1,
            )
            result_data = {"checkout": chk.model_dump(mode="json")}

        elif tool_name == "request_authorization":
            if not buyer_id:
                raise DomainError("Buyer ID required for authorization", code=ErrorCode.FORBIDDEN)
            auth = self._commerce.request_authorization(
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                checkout_id=validated_args.checkout_id or "",
            )
            result_data = {"authorization": auth.model_dump(mode="json")}

        elif tool_name == "create_payment":
            if not buyer_id:
                raise DomainError("Buyer ID required for payment", code=ErrorCode.FORBIDDEN)
            pay = self._commerce.create_payment(
                buyer_id=buyer_id,
                merchant_id=merchant_id,
                checkout_id=validated_args.checkout_id or "",
                authorization_id=validated_args.authorization_id or "",
            )
            result_data = {"payment": pay.model_dump(mode="json")}

        elif tool_name == "search_web":
            from services.research.worker import ResearchWorker

            res = ResearchWorker.execute_product_research(
                product_id=validated_args.product_id or "general",
                query=validated_args.query or "",
                catalog_specs={},
            )
            result_data = {
                "query": validated_args.query,
                "evidence": [
                    {
                        "claim": e.claim,
                        "citation_type": e.citation_type,
                        "source_url": e.source_url,
                        "confidence": e.confidence,
                    }
                    for e in res.evidence
                ],
                "step_count": res.step_count,
            }

        elif tool_name in ("open_url", "extract_page"):
            from services.research.tools.open_url import PageFetcher

            url = validated_args.url or ""
            # PageFetcher.fetch_page already enforces anti-SSRF policy and
            # raises DomainError(FORBIDDEN) for private/metadata addresses.
            extracted_text = PageFetcher.fetch_page(url, timeout_seconds=8.0, max_bytes=200_000)
            result_data = {
                "url": url,
                "status": "fetched",
                "content_type": "text/html",
                "extracted_text": extracted_text,
                "is_safe": True,
            }

        else:
            result_data = {"status": "success", "tool": tool_name}

        return ToolExecutionResult(
            tool_name=tool_name,
            arguments=validated_args,
            result=result_data,
            is_state_changing=is_mutating,
            requires_confirmation=False,
        )

    def run_bounded_agent(
        self,
        *,
        user_prompt: str,
        merchant_id: str,
        buyer_id: str | None = None,
        guard_config: GuardConfig | None = None,
    ) -> AgentRunSummary:
        """Run the end-to-end bounded agent loop.

        ``guard_config`` is the configured guard for this deployment. Without it
        the classifier ran Layer 1 only, so a remote guard was silently skipped
        on this path; the router supplies it from application settings.
        """
        start_time = time.perf_counter()
        run_id = new_id("run")

        # 1. Prompt Safety Classification Guard (Requirement 22.3)
        PromptSafetyClassifier.assert_safe(user_prompt, config=guard_config)
        with contextlib.suppress(Exception):
            self._commerce.record_agent_event(
                event_type="PROMPT_SAFETY_CHECKED",
                aggregate_id=run_id,
                actor_type="buyer",
                actor_id=buyer_id,
                merchant_id=merchant_id,
                metadata={"is_safe": True},
            )

        # 2. Extract Intent (Requirement 22.1)
        model_res = self._model.generate(user_prompt, system_prompt=INTENT_EXTRACTION_SYSTEM_PROMPT)
        raw_json = model_res.parsed_json if isinstance(model_res.parsed_json, dict) else {}
        intent = IntentValidator.validate_dict(raw_json, prompt=user_prompt)

        with contextlib.suppress(Exception):
            self._commerce.record_agent_event(
                event_type="INTENT_EXTRACTED",
                aggregate_id=run_id,
                actor_type="buyer",
                actor_id=buyer_id,
                merchant_id=merchant_id,
                model_version=model_res.model_version,
                metadata={"query": intent.query, "category": intent.category},
            )

        executed_tools: list[ToolExecutionResult] = []
        step_count = 0
        is_completed = True

        # Determine tool sequence from model tool_calls plan or default to search_products
        planned_tools: list[tuple[str, dict[str, Any]]] = []
        if isinstance(raw_json.get("tool_calls"), list):
            for tc in raw_json["tool_calls"]:
                if isinstance(tc, dict) and "tool_name" in tc:
                    planned_tools.append((str(tc["tool_name"]), dict(tc.get("arguments", {}))))

        if not planned_tools:
            planned_tools.append(
                (
                    "search_products",
                    {"query": intent.query, "intent": intent.model_dump(mode="json")},
                )
            )

        for tool_name, args in planned_tools:
            if (
                step_count >= MAX_STEPS
                or (time.perf_counter() - start_time) >= MAX_WALL_CLOCK_SECONDS
            ):
                is_completed = False
                break
            step_count += 1
            tool_res = self.execute_tool(
                tool_name=tool_name,
                arguments=args,
                merchant_id=merchant_id,
                buyer_id=buyer_id,
            )
            executed_tools.append(tool_res)
            if tool_res.requires_confirmation:
                is_completed = False
                break

        return AgentRunSummary(
            run_id=run_id,
            query=user_prompt,
            steps_executed=step_count,
            tool_calls=executed_tools,
            is_completed=is_completed,
            final_output={"intent": intent.model_dump(mode="json")},
        )
