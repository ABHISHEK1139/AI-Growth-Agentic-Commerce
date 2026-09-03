"""Persistence seam for append-only audit events."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from packages.observability.context import current_ids, new_id


class EventType(StrEnum):
    PROMPT_SAFETY_CHECKED = "PROMPT_SAFETY_CHECKED"
    INTENT_EXTRACTED = "INTENT_EXTRACTED"
    CATALOG_SEARCHED = "CATALOG_SEARCHED"
    OFFERS_RETURNED = "OFFERS_RETURNED"
    OFFER_SELECTED = "OFFER_SELECTED"
    OFFER_REVALIDATED = "OFFER_REVALIDATED"
    CHECKOUT_CREATED = "CHECKOUT_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED"
    AUTHORIZATION_REJECTED = "AUTHORIZATION_REJECTED"
    PAYMENT_CREATED = "PAYMENT_CREATED"
    PAYMENT_STATUS_CHECKED = "PAYMENT_STATUS_CHECKED"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    PRICE_CHANGE_DETECTED = "PRICE_CHANGE_DETECTED"
    INVENTORY_CHANGE_DETECTED = "INVENTORY_CHANGE_DETECTED"
    IDEMPOTENCY_REPLAYED = "IDEMPOTENCY_REPLAYED"
    RESEARCH_PERFORMED = "RESEARCH_PERFORMED"
    TOOL_BLOCKED = "TOOL_BLOCKED"


_SAFE_KEY_EXACT: frozenset[str] = frozenset(
    {
        "authorization_id",
        "authorization_hash",
        "authorization_status",
        "authorization_expiry_seconds",
        "idempotency_key",
        "price_hash",
        "input_hash",
        "inputs_hash",
        "raw_body_hash",
        "extracted_text_hash",
        "storage_key",
        "signature_valid",
        "auth_required",
        "authenticated",
    }
)

_SENSITIVE = re.compile(
    r"secret|token|password|authorization|cookie|credential|signature|key_secret|private_key|api_key",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(?:sk-|gsk_|Bearer\s+|eyJ|rzp_test_|rzp_live_|rzp_sec_)[A-Za-z0-9._-]{8,}",
    re.I,
)


def _safe_metadata(value: Any, key: str = "") -> Any:
    lower_key = key.lower()
    if lower_key not in _SAFE_KEY_EXACT and _SENSITIVE.search(key):
        return "***REDACTED***"
    if isinstance(value, str):
        return "***REDACTED***" if _SECRET_VALUE.search(value) else value
    if isinstance(value, dict):
        return {
            str(item_key): _safe_metadata(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_safe_metadata(item) for item in value]
    return value


def append_event(
    session: Session,
    *,
    event_type: str | EventType,
    aggregate_type: str,
    aggregate_id: str,
    actor_type: str,
    actor_id: str | None,
    merchant_id: str | None = None,
    input_hash: str | None = None,
    decision: str | None = None,
    reason_code: str | None = None,
    policy_version: str | None = None,
    model_version: str | None = None,
    amount_minor: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Append one audit row without committing the caller's unit of work."""
    ids = current_ids()
    event_id = new_id("aud")
    bind = session.get_bind() if hasattr(session, "get_bind") else getattr(session, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") if bind is not None else ""
    stmt = (
        text(
            """
            INSERT INTO audit_event
                (event_id, merchant_id, request_id, trace_id, agent_run_id, actor_type, actor_id,
                 event_type, aggregate_type, aggregate_id, input_hash, decision, reason_code,
                 policy_version, model_version, amount_minor, metadata)
            VALUES
                (:event_id, :merchant_id, :request_id, :trace_id, :agent_run_id, :actor_type, :actor_id,
                 :event_type, :aggregate_type, :aggregate_id, :input_hash, :decision, :reason_code,
                 :policy_version, :model_version, :amount_minor, :metadata)
            """
        )
        if dialect_name == "sqlite"
        else text(
            """
            INSERT INTO audit_event
                (event_id, merchant_id, request_id, trace_id, agent_run_id, actor_type, actor_id,
                 event_type, aggregate_type, aggregate_id, input_hash, decision, reason_code,
                 policy_version, model_version, amount_minor, metadata)
            VALUES
                (:event_id, :merchant_id, :request_id, :trace_id, :agent_run_id, :actor_type, :actor_id,
                 :event_type, :aggregate_type, :aggregate_id, :input_hash, :decision, :reason_code,
                 :policy_version, :model_version, :amount_minor, CAST(:metadata AS jsonb))
            """
        )
    )
    session.execute(
        stmt,
        {
            "event_id": event_id,
            "merchant_id": merchant_id,
            "request_id": ids.request_id,
            "trace_id": ids.trace_id,
            "agent_run_id": ids.agent_run_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "event_type": str(event_type),
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "input_hash": input_hash,
            "decision": decision,
            "reason_code": reason_code,
            "policy_version": policy_version,
            "model_version": model_version,
            "amount_minor": amount_minor,
            "metadata": json.dumps(_safe_metadata(metadata or {}), sort_keys=True),
        },
    )
    return event_id


def append_transition_event(
    session: Session,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str | None,
    merchant_id: str | None = None,
    metadata: dict[str, Any],
) -> str:
    """Append one transition audit row without committing the caller's unit of work."""
    return append_event(
        session,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        actor_type=actor_type,
        actor_id=actor_id,
        merchant_id=merchant_id,
        metadata=metadata,
    )


def list_events(
    session: Session,
    *,
    merchant_id: str,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    event_type: str | EventType | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
    order: str = "asc",
) -> list[dict[str, Any]]:
    """Read a merchant-scoped chronological ledger; callers never see another tenant."""
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset must not be negative")

    query = """
        SELECT event_id, request_id, trace_id, agent_run_id, actor_type, actor_id,
               event_type, aggregate_type, aggregate_id, input_hash, decision, reason_code,
               policy_version, model_version, amount_minor, metadata, created_at
          FROM audit_event
         WHERE merchant_id = :merchant_id
    """
    params: dict[str, Any] = {"merchant_id": merchant_id, "limit": limit, "offset": offset}

    if aggregate_type:
        query += " AND aggregate_type = :aggregate_type"
        params["aggregate_type"] = aggregate_type
    if aggregate_id:
        query += " AND aggregate_id = :aggregate_id"
        params["aggregate_id"] = aggregate_id
    if event_type:
        query += " AND event_type = :event_type"
        params["event_type"] = str(event_type)
    if start_at:
        query += " AND created_at >= :start_at"
        params["start_at"] = start_at
    if end_at:
        query += " AND created_at <= :end_at"
        params["end_at"] = end_at

    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    query += f" ORDER BY created_at {direction}, event_id {direction} LIMIT :limit OFFSET :offset"

    rows = session.execute(text(query), params).mappings()
    return [dict(row) for row in rows]


def get_aggregate_timeline(
    session: Session,
    *,
    merchant_id: str,
    aggregate_type: str,
    aggregate_id: str,
) -> list[dict[str, Any]]:
    """Get the chronological history of a specific aggregate."""
    return list_events(
        session,
        merchant_id=merchant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        limit=200,
    )
