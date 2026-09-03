"""The authorization checks themselves (Requirement 24.2, 24.5, 24.6).

Every function here raises :class:`~packages.errors.exceptions.ForbiddenError` on
denial, which the middleware renders as 403 ``FORBIDDEN`` from the existing
registry. None of them returns a boolean, because a check whose result can be
ignored eventually is.

Two things are deliberately absent from every denial message and every ``details``
payload: the identifier of the resource, and the identifier of whoever does own it.
"You do not have access to this resource" plus a reason code is everything a
legitimate caller needs, and telling a probing caller that checkout ``chk_123``
exists but belongs to someone else is exactly the leak these checks exist to
prevent. The reason code is machine-readable so the frontend can still say
something useful.
"""

from __future__ import annotations

from collections.abc import Iterable

from packages.errors.exceptions import ForbiddenError
from packages.security.principals import Principal, Role, Scope


def _deny(reason: str, message: str) -> ForbiddenError:
    return ForbiddenError(message, details={"reason": reason})


def require_role(principal: Principal, *allowed: Role) -> None:
    """The caller must hold one of ``allowed``."""
    if not allowed:
        raise ValueError("require_role needs at least one role")
    if principal.role not in allowed:
        raise _deny("role", "This account cannot perform this action.")


def require_scopes(principal: Principal, *required: Scope) -> None:
    """The credential must carry every scope in ``required``.

    The missing scopes *are* named in ``details``: they are a property of the
    caller's own credential, not of anyone else's data, and naming them is what
    lets an agent request a correct token instead of guessing.
    """
    if not required:
        raise ValueError("require_scopes needs at least one scope")
    missing = principal.missing_scopes(required)
    if missing:
        raise ForbiddenError(
            "This credential does not carry the scope required for this action.",
            details={
                "reason": "scope",
                "missing_scopes": sorted(scope.value for scope in missing),
            },
        )


def require_any_scope(principal: Principal, *acceptable: Scope) -> None:
    """The credential must carry at least one of ``acceptable``."""
    if not acceptable:
        raise ValueError("require_any_scope needs at least one scope")
    if not any(principal.has_scope(scope) for scope in acceptable):
        raise ForbiddenError(
            "This credential does not carry the scope required for this action.",
            details={
                "reason": "scope",
                "acceptable_scopes": sorted(scope.value for scope in acceptable),
            },
        )


def require_same_tenant(principal: Principal, merchant_id: str) -> None:
    """The caller must be acting inside ``merchant_id`` (Requirement 24.5).

    A platform administrator is not exempt. They cross a tenant boundary by
    obtaining a principal for that tenant through
    :meth:`~packages.security.principals.Principal.acting_on`, which is explicit
    and auditable, rather than by being waved through here.
    """
    if principal.merchant_id != merchant_id:
        raise _deny("cross_tenant", "You do not have access to this resource.")


def require_ownership(
    principal: Principal,
    *,
    owner_buyer_id: str | None,
    owner_merchant_id: str | None = None,
) -> None:
    """The caller must own the aggregate they are acting on (Requirement 24.6).

    Applied to a checkout, an authorization, a payment, or an order. Two rules:

    * The tenant must match, when the caller supplies the row's merchant.
    * A **buyer** must be the owner. Merchant-side roles are not owners of a
      buyer's checkout and never will be, but they are legitimately able to act on
      transactions within their own tenant — refunds, support, order fulfilment —
      so ownership is not the control that applies to them; the tenant check is.
      Their access is bounded by role and by tenant, both checked separately.

    An unowned row (``owner_buyer_id`` of ``None``) is refused for a buyer rather
    than allowed. If a caller cannot say who owns a record, a buyer is not the
    caller who should be acting on it.
    """
    if owner_merchant_id is not None:
        require_same_tenant(principal, owner_merchant_id)

    if principal.role is not Role.BUYER:
        return

    if not owner_buyer_id or owner_buyer_id != principal.buyer_id:
        raise _deny("ownership", "You do not have access to this resource.")


def require_all(
    principal: Principal,
    *,
    roles: Iterable[Role] | None = None,
    scopes: Iterable[Scope] | None = None,
    merchant_id: str | None = None,
    owner_buyer_id: str | None = None,
) -> None:
    """Run several checks in one call, in the order a handler should want them.

    Role first (is this the right kind of caller), then scope (does this credential
    permit the action), then tenancy, then ownership — cheapest and most general
    first, so a denial reports the broadest reason rather than an incidental one.
    """
    if roles is not None:
        require_role(principal, *roles)
    if scopes is not None:
        require_scopes(principal, *scopes)
    if merchant_id is not None:
        require_same_tenant(principal, merchant_id)
    if owner_buyer_id is not None:
        require_ownership(principal, owner_buyer_id=owner_buyer_id)
