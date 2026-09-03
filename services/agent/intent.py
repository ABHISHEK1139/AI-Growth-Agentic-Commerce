"""Intent extraction schema validator and normalizer (Task 27, Requirement 22.1, 22.2)."""

from __future__ import annotations

import re
from typing import Any

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.schemas.v1 import CurrencyCode, IntentFinancialConstraintsV1, IntentV1

#: Currency used when the extraction carries none at all.
DEFAULT_CURRENCY: CurrencyCode = "INR"

#: The supported set, for the error detail. The narrowing below compares against
#: the literals themselves so the type checker can prove the return type.
SUPPORTED_CURRENCIES: tuple[CurrencyCode, ...] = ("INR", "USD")


def narrow_currency(value: object) -> CurrencyCode:
    """Narrow an extracted currency onto the supported literal set.

    Model output is untrusted, so an unrecognised currency is refused rather than
    quietly rewritten. Rewriting is the more dangerous option: treating a dollar
    budget as rupees moves the buyer's stated ceiling by a factor of eighty, and
    the buyer would never see that it happened.

    An absent or blank value is not an unsupported currency, it is no currency at
    all, so it takes the configured default.
    """
    if not value:
        return DEFAULT_CURRENCY
    candidate = str(value).strip().upper()
    if not candidate:
        return DEFAULT_CURRENCY
    # Compared literal by literal on purpose: a membership test against a tuple
    # narrows to `str`, and the schema wants the literal type.
    if candidate == "INR":
        return "INR"
    if candidate == "USD":
        return "USD"
    raise DomainError(
        "Extracted intent declares a currency this merchant does not support.",
        code=ErrorCode.VALIDATION_ERROR,
        details={"currency": candidate, "supported": list(SUPPORTED_CURRENCIES)},
    )


#: Standardized system prompt for deterministic JSON intent extraction across all LLM backends.
INTENT_EXTRACTION_SYSTEM_PROMPT = """You are a precise, deterministic shopping intent extractor for an e-commerce catalog.
Extract user constraints from the shopping query and output ONLY a valid JSON object conforming to this schema:
{
  "query": string (normalized search keywords),
  "category": "laptop" | "smartphone" | "accessory" | "audio" | null,
  "max_budget": number | null (maximum price in major units, e.g. 80000 for ₹80,000 or 80k),
  "currency": "INR" | "USD",
  "min_memory_gb": integer | null (e.g. 16 for 16GB RAM),
  "min_storage_gb": integer | null (e.g. 512 for 512GB SSD),
  "max_delivery_days": integer | null,
  "quantity": integer (default 1)
}

Examples:
Query: "I need a laptop under 80,000 INR with 16GB RAM"
JSON: {"query": "laptop", "category": "laptop", "max_budget": 80000, "currency": "INR", "min_memory_gb": 16, "min_storage_gb": null, "max_delivery_days": null, "quantity": 1}

Query: "Best smartphone below 40k with 256GB storage"
JSON: {"query": "smartphone", "category": "smartphone", "max_budget": 40000, "currency": "INR", "min_memory_gb": null, "min_storage_gb": 256, "max_delivery_days": null, "quantity": 1}

Respond with ONLY the JSON object. No explanations, no markdown fences."""


def build_intent_prompt(user_query: str) -> str:
    """Construct structured instruction prompt for intent extraction."""
    return f'{INTENT_EXTRACTION_SYSTEM_PROMPT}\n\nQuery: "{user_query}"\nJSON:'


class IntentValidator:
    """Validates structured intent extracted by AI models against closed strict schema."""

    @staticmethod
    def validate_dict(data: dict[str, Any], prompt: str | None = None) -> IntentV1:
        """Validate and normalize intent shape into strict closed IntentV1 with deterministic fallback."""
        raw = dict(data)
        prompt_text = prompt or str(raw.get("user_prompt") or raw.get("query") or "")

        # If strict raw IntentV1 structure with existing financial dict, validate financial strictly
        fin_obj: IntentFinancialConstraintsV1 | None = None
        if "financial" in raw and isinstance(raw["financial"], dict):
            try:
                fin_obj = IntentFinancialConstraintsV1.model_validate(raw["financial"])
            except Exception as exc:
                raise DomainError(
                    f"Extracted intent violates schema: {exc}",
                    code=ErrorCode.VALIDATION_ERROR,
                ) from exc

        query = str(
            raw.get("query") or raw.get("product") or raw.get("intent") or "general_shopping"
        )[:200]

        # 1. Normalize Category
        category = raw.get("category") or raw.get("product_category") or raw.get("item_category")
        if category in ("electronics", None) and raw.get("product"):
            category = str(raw["product"]).lower()
        if category:
            category = str(category).lower().strip()
            if "laptop" in category or "ultrabook" in category or "notebook" in category:
                category = "laptop"
            elif "phone" in category or "smartphone" in category or "mobile" in category:
                category = "smartphone"
            elif "audio" in category or "headphone" in category or "earbuds" in category:
                category = "audio"
            elif "accessory" in category or "cable" in category or "sleeve" in category:
                category = "accessory"
        elif prompt_text:
            p_lower = prompt_text.lower()
            if any(w in p_lower for w in ("laptop", "notebook", "ultrabook", "macbook")):
                category = "laptop"
            elif any(w in p_lower for w in ("smartphone", "phone", "iphone", "android")):
                category = "smartphone"
            elif any(
                w in p_lower for w in ("audio", "headphone", "earphone", "earbuds", "speaker")
            ):
                category = "audio"
            elif any(
                w in p_lower
                for w in ("accessory", "charger", "cable", "mouse", "keyboard", "sleeve")
            ):
                category = "accessory"

        # 2. Normalize Financial Constraints
        if fin_obj is None:
            budget_minor = None
            currency_raw: object = raw.get("currency")

            # Check direct alias keys
            for key in (
                "max_budget",
                "max_price_limit",
                "budget_limit",
                "budget_max",
                "price_max",
                "max_price",
                "max_cost",
                "budget",
                "price",
                "amount",
            ):
                if key in raw:
                    val = raw[key]
                    if isinstance(val, dict):
                        amt = (
                            val.get("max")
                            or val.get("amount")
                            or val.get("max_price")
                            or val.get("price_max")
                        )
                        if isinstance(amt, int | float):
                            budget_minor = int(amt * 100) if amt < 1_000_000 else int(amt)
                        currency_raw = val.get("currency") or currency_raw
                        break
                    if isinstance(val, int | float):
                        budget_minor = int(val * 100) if val < 1_000_000 else int(val)
                        break

            # Deterministic Regex Fallback for price ceilings from prompt if missing or unparsed
            if budget_minor is None and prompt_text:
                m_budget = re.search(
                    r"(?:under|below|less than|max(?:imum)?\s*(?:price|budget)?|budget\s*of|upto|up to)\s*₹?\s*([\d,]+)\s*(k|thousand|lakh|lac)?",
                    prompt_text,
                    re.IGNORECASE,
                )
                if m_budget:
                    num_str = m_budget.group(1).replace(",", "")
                    multiplier = 1
                    unit = (m_budget.group(2) or "").lower()
                    if unit in ("k", "thousand"):
                        multiplier = 1000
                    elif unit in ("lakh", "lac"):
                        multiplier = 100000
                    try:
                        amt_val = float(num_str) * multiplier
                        budget_minor = int(amt_val * 100) if amt_val < 1_000_000 else int(amt_val)
                    except ValueError:
                        pass

            fin_obj = IntentFinancialConstraintsV1(
                budget_minor=budget_minor,
                currency=narrow_currency(currency_raw),
            )

        # 3. Normalize Hardware Specifications (RAM / Memory)
        min_memory_gb = None
        for ram_key in ("min_memory_gb", "ram_gb", "ram", "memory_gb", "memory"):
            if ram_key in raw and isinstance(raw[ram_key], int | float):
                min_memory_gb = int(raw[ram_key])
                break

        specs = raw.get("specifications")
        if isinstance(specs, dict) and min_memory_gb is None:
            ram_val = specs.get("RAM") or specs.get("memory") or specs.get("ram")
            if ram_val:
                m = re.search(r"(\d+)", str(ram_val))
                if m:
                    min_memory_gb = int(m.group(1))

        if min_memory_gb is None and prompt_text:
            m_ram = re.search(
                r"(\d+)\s*(?:gb|gigabyte)?\s*(?:ram|memory)", prompt_text, re.IGNORECASE
            )
            if not m_ram:
                m_ram = re.search(
                    r"(?:ram|memory)\s*(?:of|at least)?\s*(\d+)\s*gb", prompt_text, re.IGNORECASE
                )
            if m_ram:
                min_memory_gb = int(m_ram.group(1))

        # 4. Normalize Storage Specifications
        min_storage_gb = None
        for storage_key in ("min_storage_gb", "storage_gb", "ssd_gb", "storage", "ssd", "rom"):
            if storage_key in raw and isinstance(raw[storage_key], int | float):
                min_storage_gb = int(raw[storage_key])
                break

        if isinstance(specs, dict) and min_storage_gb is None:
            storage_val = specs.get("storage") or specs.get("SSD") or specs.get("storage_gb")
            if storage_val:
                m = re.search(r"(\d+)", str(storage_val))
                if m:
                    min_storage_gb = int(m.group(1))

        if min_storage_gb is None and prompt_text:
            m_storage = re.search(
                r"(\d+)\s*(?:gb|tb)\s*(?:ssd|storage|rom|hdd)", prompt_text, re.IGNORECASE
            )
            if m_storage:
                val = int(m_storage.group(1))
                if "tb" in m_storage.group(0).lower():
                    val = val * 1024
                min_storage_gb = val

        # 5. Delivery and Quantity
        max_delivery_days = None
        for del_key in ("max_delivery_days", "delivery_days", "delivery_max_days", "delivery_max"):
            if del_key in raw and isinstance(raw[del_key], int | float):
                max_delivery_days = int(raw[del_key])
                break

        delivery_val = raw.get("delivery")
        if delivery_val and max_delivery_days is None:
            if "fast" in str(delivery_val).lower():
                max_delivery_days = 3
            else:
                m = re.search(r"(\d+)", str(delivery_val))
                if m:
                    max_delivery_days = int(m.group(1))

        quantity = int(raw.get("quantity") or 1)
        if quantity < 1:
            quantity = 1

        try:
            return IntentV1(
                schema_version="1.0",
                query=query,
                category=category,
                financial=fin_obj,
                min_memory_gb=min_memory_gb,
                min_storage_gb=min_storage_gb,
                max_delivery_days=max_delivery_days,
                quantity=quantity,
            )
        except Exception as exc:
            raise DomainError(
                f"Extracted intent violates schema: {exc}",
                code=ErrorCode.VALIDATION_ERROR,
            ) from exc
