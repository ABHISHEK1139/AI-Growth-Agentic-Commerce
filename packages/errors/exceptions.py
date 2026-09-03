"""Domain exceptions.

The design's error-handling table says services raise typed errors carrying a
registry code and never return ``None`` for a failure. This is the base every one
of those errors derives from: it pairs a raise site with a code, and through the
code with an HTTP status and a retryable flag, so a service never names a status
and the middleware never guesses a code.

A service may attach ``next_actions`` at the raise site, which is how a failure
arrives at the frontend already knowing how the buyer can recover
(Requirement 31.10).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from packages.errors.registry import ErrorCode, ErrorSpec, spec_for
from packages.schemas.envelope import NextAction


class DomainError(Exception):
    """A failure a client is allowed to be told about.

    Subclasses set ``default_code``. Everything a client sees — status, message,
    retryable — comes from the registry entry for that code.
    """

    #: Overridden by each subclass. The base defaults to a generic conflict-free
    #: code rather than something misleadingly specific.
    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
        next_actions: Sequence[NextAction] | None = None,
    ) -> None:
        self.code: ErrorCode = code or type(self).default_code
        spec = spec_for(self.code)
        # The registry message is the default so that a service raising without
        # words still produces a sentence a buyer can read.
        self.message: str = message or spec.message
        self.details: dict[str, Any] = dict(details or {})
        self.next_actions: list[NextAction] = list(next_actions or [])
        super().__init__(self.message)

    @property
    def spec(self) -> ErrorSpec:
        return spec_for(self.code)

    @property
    def http_status(self) -> int:
        return self.spec.http_status

    @property
    def retryable(self) -> bool:
        return self.spec.is_retryable

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r})"


class UnauthenticatedError(DomainError):
    """No usable credential was presented, or the one presented did not verify.

    Distinct from :class:`ForbiddenError` because the client's remedy differs: a
    401 means "authenticate, or refresh and retry", a 403 means "stop". Every
    reason a credential can fail — absent, malformed, tampered with, expired —
    arrives here, and the response never says which record or which tenant was
    being reached for.
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.UNAUTHENTICATED


class ForbiddenError(DomainError):
    """The caller is authenticated but not permitted."""

    default_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN


class NotFoundError(DomainError):
    """The resource does not exist, or does not exist for this tenant.

    The two are answered identically on purpose: distinguishing them tells an
    unauthorised caller that a record exists.
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND


class ValidationError(DomainError):
    """Input that passed schema validation but violates a domain rule."""

    default_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR


class RateLimitedError(DomainError):
    """Too many requests from one actor."""

    default_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
