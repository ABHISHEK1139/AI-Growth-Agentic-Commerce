"""Roles, scopes, and the principal a request resolves to (Requirement 24.1, 24.2).

Four roles, from the design: a buyer, the two merchant-side roles, and a platform
administrator. Three scopes, from the design's public agent surface table:
``catalog:read``, ``checkout:write``, ``payment:write``.

Roles and scopes answer different questions and are kept apart on purpose. A role
says *who* the caller is and drives the web surface; a scope says *what a
particular credential may do* and is what an external agent's token carries. A
buyer signing in through the web application gets the full set their role allows;
an agent asks for a narrower set and gets no more than its role permits, so a
leaked catalog-reading token cannot create a payment.

A :class:`Principal` is immutable and carries its own tenant. Every scoped query
in the system is built from :meth:`Principal.tenant_scope`, so a request cannot
reach data outside the tenant it authenticated into.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType

from packages.errors.exceptions import ForbiddenError
from packages.security.tenancy import TenantScope


class Role(StrEnum):
    """Who the caller is. Wire spelling is stable; clients and audit rows use it."""

    BUYER = "buyer"
    MERCHANT_ADMIN = "merchant_admin"
    MERCHANT_OPERATOR = "merchant_operator"
    PLATFORM_ADMIN = "platform_admin"


class Scope(StrEnum):
    """What a credential may do. Values match the design's scope column exactly."""

    CATALOG_READ = "catalog:read"
    CHECKOUT_WRITE = "checkout:write"
    PAYMENT_WRITE = "payment:write"
    SETTLEMENT_READ = "settlement:read"


#: The merchant-side roles. Grouped because most authorization questions are
#: "is this someone from the merchant" rather than "which of the two".
MERCHANT_ROLES: frozenset[Role] = frozenset({Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR})

#: The ceiling on what a credential for each role may carry.
#:
#: Only a buyer can spend. A merchant administrator configures the catalog and the
#: policy through the web surface; neither merchant role may hold
#: ``checkout:write`` or ``payment:write``, because a merchant-side credential
#: that can create a payment is a merchant-side credential that can charge a
#: buyer. The platform administrator is deliberately in the same position: an
#: operations role that can read is far less dangerous than one that can pay.
ROLE_SCOPES: Mapping[Role, frozenset[Scope]] = MappingProxyType(
    {
        Role.BUYER: frozenset({Scope.CATALOG_READ, Scope.CHECKOUT_WRITE, Scope.PAYMENT_WRITE}),
        Role.MERCHANT_ADMIN: frozenset({Scope.CATALOG_READ}),
        Role.MERCHANT_OPERATOR: frozenset({Scope.CATALOG_READ}),
        Role.PLATFORM_ADMIN: frozenset({Scope.CATALOG_READ, Scope.SETTLEMENT_READ}),
    }
)


class AuthMethod(StrEnum):
    """How the caller proved who they are.

    Recorded on the principal because some surfaces care: the public agent surface
    accepts a token and never a session cookie, so a browser that happens to be
    signed in cannot drive it, and a session-only administrative action cannot be
    performed with a long-lived agent credential (Requirement 20.5).
    """

    SESSION = "session"
    TOKEN = "token"  # noqa: S105 - authentication method label, not a credential


def scopes_for_role(role: Role) -> frozenset[Scope]:
    """Everything ``role`` is permitted to hold."""
    return ROLE_SCOPES[role]


def grant_scopes(role: Role, requested: Iterable[Scope] | None = None) -> frozenset[Scope]:
    """Resolve the scopes a credential is issued with.

    ``None`` means "everything this role allows", which is what a session gets.
    A requested set is validated against the role ceiling. If an unauthorized/unsupported
    scope is requested, raise ForbiddenError rather than silently granting a narrowed set.
    """
    permitted = scopes_for_role(role)
    if requested is None:
        return permitted
    requested_set = frozenset(requested)
    excess = requested_set - permitted
    if excess:
        raise ForbiddenError(
            f"Requested scopes {sorted(s.value for s in excess)} exceed role ceiling for {role.value}",
            details={
                "requested": sorted(s.value for s in requested_set),
                "permitted": sorted(s.value for s in permitted),
                "excess": sorted(s.value for s in excess),
            },
        )
    return requested_set


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    Immutable, and validated at construction: a buyer without a ``buyer_id``
    would make every ownership check vacuous, and scopes beyond the role ceiling
    would make the ceiling decorative.
    """

    subject: str
    role: Role
    merchant_id: str
    buyer_id: str | None = None
    scopes: frozenset[Scope] = frozenset()
    method: AuthMethod = AuthMethod.TOKEN
    #: Epoch seconds. ``None`` for a principal that was not built from a credential.
    expires_at: int | None = None
    #: The API client a token was exchanged for, for the audit trail.
    client_id: str | None = None

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("a principal must have a subject")
        if not self.merchant_id:
            raise ValueError("a principal must belong to a merchant tenant")
        if self.role is Role.BUYER and not self.buyer_id:
            raise ValueError("a buyer principal must carry a buyer_id")
        excess = self.scopes - scopes_for_role(self.role)
        if excess:
            raise ValueError(
                f"role {self.role.value} may not hold {sorted(scope.value for scope in excess)}"
            )

    # --- Tenancy ----------------------------------------------------------

    def tenant_scope(self) -> TenantScope:
        """The scope every repository for this request is built from."""
        return TenantScope(merchant_id=self.merchant_id, buyer_id=self.buyer_id)

    def acting_on(self, merchant_id: str) -> Principal:
        """A principal for a different tenant. Platform administrators only.

        This is the *only* way to cross a tenant boundary, it is explicit at the
        call site, and it still produces a scope naming exactly one merchant. A
        raise here rather than a silent widening is what stops "the admin screen
        needs to see everything" from becoming an unfiltered query.
        """
        if merchant_id == self.merchant_id:
            return self
        if self.role is not Role.PLATFORM_ADMIN:
            raise ForbiddenError(
                "This account cannot act on another tenant.",
                details={"reason": "cross_tenant"},
            )
        return replace(self, merchant_id=merchant_id)

    # --- Scopes -----------------------------------------------------------

    def has_scope(self, scope: Scope) -> bool:
        return scope in self.scopes

    def missing_scopes(self, required: Iterable[Scope]) -> frozenset[Scope]:
        return frozenset(required) - self.scopes

    # --- Roles ------------------------------------------------------------

    @property
    def is_buyer(self) -> bool:
        return self.role is Role.BUYER

    @property
    def is_merchant_side(self) -> bool:
        return self.role in MERCHANT_ROLES

    @property
    def is_platform_admin(self) -> bool:
        return self.role is Role.PLATFORM_ADMIN

    # --- Observability ----------------------------------------------------

    def as_log_fields(self) -> dict[str, str]:
        """Identity for a log line: opaque identifiers and no credential.

        Note what is absent: the token, the session value, and the scope list are
        not here. Scopes are not secret, but they are noise on every line, and the
        authorization decision is logged where it is made.
        """
        fields = {
            "actor_id": self.subject,
            "actor_role": self.role.value,
            "auth_method": self.method.value,
            "merchant_id": self.merchant_id,
        }
        if self.buyer_id is not None:
            fields["buyer_id"] = self.buyer_id
        if self.client_id is not None:
            fields["client_id"] = self.client_id
        return fields
