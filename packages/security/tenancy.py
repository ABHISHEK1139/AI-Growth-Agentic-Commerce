"""The tenant scope every query is built from (Requirement 24.3, 24.4).

One value object, and the whole point of it is that it is *not optional*.
:class:`~packages.db.repository.TenantScopedRepository` takes a
:class:`TenantScope` as a positional constructor argument and offers no other way
in, so "I forgot the tenant filter" is not a mistake a caller can make: there is
no repository instance to call a method on until the scope exists.

A platform administrator gets no ambient reach either. There is no
``TenantScope.all()`` and no ``merchant_id=None``. An operator who needs to look
at another merchant's data names that merchant explicitly (see
:meth:`packages.security.principals.Principal.acting_on`), which keeps the
audit question answerable: every query was scoped to exactly one tenant, and the
scope is in the call.

These errors are ``RuntimeError``s rather than
:class:`~packages.errors.exceptions.DomainError`s on purpose. A missing tenant
scope is a bug in our code, not a condition a client caused, so it must surface
as ``INTERNAL_ERROR`` with a logged traceback rather than as a tidy 403 that a
reviewer could mistake for the control working.
"""

from __future__ import annotations

from dataclasses import dataclass


class TenantScopeError(RuntimeError):
    """A tenant scope was absent, malformed, or the wrong shape."""


class TenantScopeRequiredError(TenantScopeError):
    """A repository was constructed without the scope it requires."""


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise TenantScopeError(f"{name} must be a non-empty string")
    if value != value.strip():
        # Surrounding whitespace is how "merchant_a " silently fails to match
        # "merchant_a" and a scoped query quietly returns nothing.
        raise TenantScopeError(f"{name} must not carry leading or trailing whitespace")


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The tenant a query is allowed to see.

    ``merchant_id`` is always present. ``buyer_id`` is present when the caller is
    a buyer, and a repository over a buyer-owned aggregate refuses to be built
    without it, which is how Requirement 24.6 is enforced at the query rather
    than only at the handler.
    """

    merchant_id: str
    buyer_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier("merchant_id", self.merchant_id)
        if self.buyer_id is not None:
            _validate_identifier("buyer_id", self.buyer_id)

    @property
    def is_buyer_scoped(self) -> bool:
        return self.buyer_id is not None

    def with_buyer(self, buyer_id: str) -> TenantScope:
        """Narrow this scope to one buyer."""
        return TenantScope(merchant_id=self.merchant_id, buyer_id=buyer_id)

    def without_buyer(self) -> TenantScope:
        """Widen to the merchant tenant. Never widens past it."""
        return TenantScope(merchant_id=self.merchant_id)

    def covers(self, *, merchant_id: str, buyer_id: str | None = None) -> bool:
        """Whether a row carrying these identifiers is visible in this scope."""
        if merchant_id != self.merchant_id:
            return False
        if self.buyer_id is None:
            return True
        return buyer_id == self.buyer_id

    def as_log_fields(self) -> dict[str, str]:
        """Identifiers for a log line. Both are opaque ids, neither is a secret."""
        fields = {"merchant_id": self.merchant_id}
        if self.buyer_id is not None:
            fields["buyer_id"] = self.buyer_id
        return fields


def require_tenant_scope(scope: object) -> TenantScope:
    """Return ``scope`` if it is a real :class:`TenantScope`, else raise.

    The type annotation already says a scope is required. This is the runtime
    half, for the untyped edges: a dict from a cache, a ``None`` threaded through
    a factory, a positional argument in the wrong order.
    """
    if not isinstance(scope, TenantScope):
        raise TenantScopeRequiredError(
            f"a TenantScope is required, got {type(scope).__name__}; "
            "a query may not be constructed without one"
        )
    return scope
