"""Idempotency layer for state-mutating payment operations (Task 20, Requirement 17, Properties 9, 10, 11)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode
from packages.observability.context import new_id
from services.audit.repository import append_event
from services.payments.models import IdempotencyRecord


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def compute_request_hash(body: Any) -> str:
    """Compute deterministic SHA-256 hash over canonical representation of request body."""
    if body is None:
        raw = b""
    elif isinstance(body, bytes):
        raw = body
    elif isinstance(body, str):
        raw = body.encode("utf-8")
    elif isinstance(body, dict):
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    else:
        raw = str(body).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class IdempotencyManager:
    """Manages concurrent deduplication, lock acquisition, and cached replays."""

    @staticmethod
    def acquire_lock(
        session: Session,
        *,
        actor_type: str,
        actor_id: str,
        endpoint: str,
        idempotency_key: str,
        request_hash: str,
        now: datetime | None = None,
        lock_ttl_seconds: int = 300,
    ) -> tuple[bool, IdempotencyRecord | None, dict[str, Any] | None, int | None]:
        """Try to acquire the in-progress idempotency lock.

        Returns:
            (is_replay, record, cached_response_body, cached_status_code)
        Raises:
            DomainError(REQUEST_IN_PROGRESS) if another request is actively executing
            DomainError(IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST) if key reused with differing body
        """
        current_time = now or datetime.now(UTC)
        existing = (
            session.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.actor_type == actor_type,
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .first()
        )

        if existing is not None:
            # 1. Key reused with different payload
            if existing.request_hash != request_hash:
                raise DomainError(
                    "This idempotency key was previously used with a different request payload.",
                    code=ErrorCode.IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST,
                )

            # 2. Key previously failed or lock timed out -> allow retry (BUG-31)
            is_expired = existing.expires_at is not None and _ensure_tz(current_time) >= _ensure_tz(
                existing.expires_at
            )
            if existing.status == "failed" or (existing.status == "in_progress" and is_expired):
                existing.status = "in_progress"
                existing.request_hash = request_hash
                existing.expires_at = current_time + timedelta(seconds=lock_ttl_seconds)
                session.flush()
                return False, existing, None, None

            # 3. Key currently actively in flight
            if existing.status == "in_progress":
                raise DomainError(
                    "A request with this idempotency key is already in progress.",
                    code=ErrorCode.REQUEST_IN_PROGRESS,
                )

            # 4. Completed -> replay
            append_event(
                session,
                event_type="IDEMPOTENCY_REPLAYED",
                aggregate_type="idempotency",
                aggregate_id=existing.idempotency_record_id,
                actor_type=actor_type,
                actor_id=actor_id,
                metadata={
                    "endpoint": endpoint,
                    "idempotency_key": idempotency_key,
                },
            )
            return True, existing, existing.response_body, existing.response_status_code or 200

        # Create new in_progress record guarded against concurrent race conditions
        record_id = new_id("idm")
        record = IdempotencyRecord(
            idempotency_record_id=record_id,
            actor_type=actor_type,
            actor_id=actor_id,
            endpoint=endpoint,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="in_progress",
            created_at=current_time,
            expires_at=current_time + timedelta(seconds=lock_ttl_seconds),
        )
        try:
            if hasattr(session, "begin_nested"):
                with session.begin_nested():
                    session.add(record)
                    session.flush()
            else:
                session.add(record)
                session.flush()
        except Exception:  # Concurrency race: another transaction acquired the lock first
            existing = (
                session.query(IdempotencyRecord)
                .filter(
                    IdempotencyRecord.actor_type == actor_type,
                    IdempotencyRecord.actor_id == actor_id,
                    IdempotencyRecord.endpoint == endpoint,
                    IdempotencyRecord.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise DomainError(
                        "This idempotency key was previously used with a different request payload.",
                        code=ErrorCode.IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST,
                    ) from None
                is_expired = existing.expires_at is not None and _ensure_tz(
                    current_time
                ) >= _ensure_tz(existing.expires_at)
                if existing.status == "failed" or (existing.status == "in_progress" and is_expired):
                    existing.status = "in_progress"
                    existing.request_hash = request_hash
                    existing.expires_at = current_time + timedelta(seconds=lock_ttl_seconds)
                    session.flush()
                    return False, existing, None, None
                if existing.status == "in_progress":
                    raise DomainError(
                        "A request with this idempotency key is already in progress.",
                        code=ErrorCode.REQUEST_IN_PROGRESS,
                    ) from None
                append_event(
                    session,
                    event_type="IDEMPOTENCY_REPLAYED",
                    aggregate_type="idempotency",
                    aggregate_id=existing.idempotency_record_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    metadata={
                        "endpoint": endpoint,
                        "idempotency_key": idempotency_key,
                    },
                )
                return True, existing, existing.response_body, existing.response_status_code or 200
            raise

        return False, record, None, None

    @staticmethod
    def complete(
        session: Session,
        *,
        record_id: str,
        status_code: int,
        response_body: dict[str, Any],
        now: datetime | None = None,
        record: IdempotencyRecord | None = None,
    ) -> None:
        """Mark idempotency record completed with its response."""
        target_record = record or (
            session.query(IdempotencyRecord)
            .filter(IdempotencyRecord.idempotency_record_id == record_id)
            .first()
        )
        if target_record:
            target_record.status = "completed"
            target_record.response_status_code = status_code
            target_record.response_body = response_body
            target_record.completed_at = now or datetime.now(UTC)
            session.flush()

    @staticmethod
    def fail_lock(
        session: Session,
        *,
        record_id: str,
        error_code: str | None = None,
        status_code: int = 500,
        response_body: dict[str, Any] | None = None,
        now: datetime | None = None,
        record: IdempotencyRecord | None = None,
    ) -> None:
        """Mark idempotency record failed so subsequent retries are permitted (BUG-31)."""
        target_record = record or (
            session.query(IdempotencyRecord)
            .filter(IdempotencyRecord.idempotency_record_id == record_id)
            .first()
        )
        if target_record:
            target_record.status = "failed"
            target_record.response_status_code = status_code
            target_record.response_body = response_body or {"error": error_code or "FAILED"}
            target_record.completed_at = now or datetime.now(UTC)
            session.flush()

    @staticmethod
    def release_lock(
        session: Session,
        *,
        record_id: str,
        record: IdempotencyRecord | None = None,
    ) -> None:
        """Release/delete an in-progress idempotency lock on aborted/failed operations (BUG-31)."""
        target_record = record or (
            session.query(IdempotencyRecord)
            .filter(IdempotencyRecord.idempotency_record_id == record_id)
            .first()
        )
        if target_record:
            session.delete(target_record)
            session.flush()
