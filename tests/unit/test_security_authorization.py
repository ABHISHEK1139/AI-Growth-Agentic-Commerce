"""Role, tenant, and buyer ownership checks (Requirements 24.2, 24.5, 24.6)."""

from __future__ import annotations

import pytest

from packages.errors.exceptions import ForbiddenError
from packages.security.authorization import (
    require_ownership,
    require_role,
    require_same_tenant,
)
from packages.security.principals import Principal, Role, Scope

MERCHANT = "merchant_demo"
BUYER = "buyer_ada"


def principal(role: Role, *, merchant_id: str = MERCHANT, buyer_id: str | None = None) -> Principal:
    return Principal(
        subject=buyer_id or f"user_{role.value}",
        role=role,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        scopes=frozenset({Scope.CATALOG_READ}),
    )


@pytest.mark.parametrize(
    "role",
    [Role.BUYER, Role.MERCHANT_ADMIN, Role.MERCHANT_OPERATOR, Role.PLATFORM_ADMIN],
)
def test_all_declared_roles_are_accepted_by_rbac(role: Role) -> None:
    actor = principal(role, buyer_id=BUYER if role is Role.BUYER else None)

    require_role(actor, role)


def test_cross_tenant_access_is_denied() -> None:
    actor = principal(Role.BUYER, buyer_id=BUYER)

    with pytest.raises(ForbiddenError) as caught:
        require_same_tenant(actor, "merchant_rival")

    assert caught.value.http_status == 403
    assert caught.value.details == {"reason": "cross_tenant"}


def test_buyer_cannot_act_on_another_buyers_aggregate() -> None:
    actor = principal(Role.BUYER, buyer_id=BUYER)

    with pytest.raises(ForbiddenError) as caught:
        require_ownership(
            actor,
            owner_buyer_id="buyer_grace",
            owner_merchant_id=MERCHANT,
        )

    assert caught.value.http_status == 403
    assert caught.value.details == {"reason": "ownership"}


def test_buyer_ownership_requires_the_same_tenant_and_buyer() -> None:
    actor = principal(Role.BUYER, buyer_id=BUYER)

    require_ownership(actor, owner_buyer_id=BUYER, owner_merchant_id=MERCHANT)

    with pytest.raises(ForbiddenError) as caught:
        require_ownership(
            actor,
            owner_buyer_id=BUYER,
            owner_merchant_id="merchant_rival",
        )

    assert caught.value.details == {"reason": "cross_tenant"}


def test_platform_admin_cross_tenant_access_must_be_explicit() -> None:
    actor = principal(Role.PLATFORM_ADMIN)

    with pytest.raises(ForbiddenError):
        require_same_tenant(actor, "merchant_rival")

    delegated = actor.acting_on("merchant_rival")
    require_same_tenant(delegated, "merchant_rival")
    assert delegated.merchant_id == "merchant_rival"
