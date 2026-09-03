from __future__ import annotations

from packages.errors.exceptions import DomainError
from packages.errors.registry import ErrorCode


class InventoryUnavailableError(DomainError):
    """Raised when an inventory reservation fails due to insufficient stock."""

    default_code = ErrorCode.INVENTORY_UNAVAILABLE


class VersionConflictError(DomainError):
    """Raised when an optimistic concurrency check fails."""

    default_code = ErrorCode.VERSION_CONFLICT
