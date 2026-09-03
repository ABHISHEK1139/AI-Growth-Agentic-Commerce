"""Tenant-scoped repository enforcement (Requirements 24.3-24.6, Property 28)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from packages.db.repository import (
    CrossTenantWriteError,
    TenantScopedRepository,
    TenantScopeMissingBuyerError,
    UnscopedQueryError,
)
from packages.security.tenancy import TenantScope, TenantScopeRequiredError


class Base(DeclarativeBase):
    pass


class CatalogRow(Base):
    __tablename__ = "task4_catalog_rows"

    row_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)


class CheckoutRow(Base):
    __tablename__ = "task4_checkout_rows"

    row_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String, nullable=False)
    buyer_id: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)


class CatalogRepository(TenantScopedRepository[CatalogRow]):
    model = CatalogRow


class CheckoutRepository(TenantScopedRepository[CheckoutRow]):
    model = CheckoutRow
    buyer_column = "buyer_id"
    requires_buyer_scope = True


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_unfiltered_query_raises_instead_of_returning_rows(db_session: Session) -> None:
    repo = CatalogRepository(db_session, TenantScope("merchant_demo"))

    with pytest.raises(UnscopedQueryError):
        repo.execute(select(CatalogRow))


def test_repository_cannot_be_constructed_without_a_tenant_scope(db_session: Session) -> None:
    with pytest.raises(TenantScopeRequiredError):
        CatalogRepository(db_session, None)  # type: ignore[arg-type]


def test_cross_tenant_read_returns_no_row(db_session: Session) -> None:
    db_session.add_all(
        [
            CatalogRow(row_id="row_own", merchant_id="merchant_demo", value="visible"),
            CatalogRow(row_id="row_rival", merchant_id="merchant_rival", value="hidden"),
        ]
    )
    db_session.flush()

    rows = CatalogRepository(db_session, TenantScope("merchant_demo")).list_all()

    assert [row.row_id for row in rows] == ["row_own"]


def test_buyer_owned_repository_requires_and_applies_buyer_scope(db_session: Session) -> None:
    db_session.add_all(
        [
            CheckoutRow(
                row_id="chk_own",
                merchant_id="merchant_demo",
                buyer_id="buyer_ada",
                value="visible",
            ),
            CheckoutRow(
                row_id="chk_other_buyer",
                merchant_id="merchant_demo",
                buyer_id="buyer_grace",
                value="hidden",
            ),
            CheckoutRow(
                row_id="chk_other_tenant",
                merchant_id="merchant_rival",
                buyer_id="buyer_ada",
                value="hidden",
            ),
        ]
    )
    db_session.flush()

    with pytest.raises(TenantScopeMissingBuyerError):
        CheckoutRepository(db_session, TenantScope("merchant_demo"))

    rows = CheckoutRepository(db_session, TenantScope("merchant_demo", "buyer_ada")).list_all()
    assert [row.row_id for row in rows] == ["chk_own"]


def test_cross_tenant_write_is_rejected(db_session: Session) -> None:
    repo = CatalogRepository(db_session, TenantScope("merchant_demo"))
    rival = CatalogRow(row_id="row_rival", merchant_id="merchant_rival", value="hidden")

    with pytest.raises(CrossTenantWriteError):
        repo.add(rival)


_identifier = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=16,
)


@hypothesis_settings(max_examples=40, deadline=None)
@given(merchant_id=_identifier, buyer_id=_identifier)
def test_property_every_query_returns_only_rows_in_the_actor_scope(
    merchant_id: str,
    buyer_id: str,
) -> None:
    """**Validates: Requirements 24.3, 24.4, 24.5**

    Property 28: for generated tenant and buyer identifiers, cross-tenant and
    cross-buyer rows are never observable through the repository.
    """
    rival_merchant = f"{merchant_id}_rival"
    rival_buyer = f"{buyer_id}_rival"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all(
                [
                    CheckoutRow(
                        row_id="own",
                        merchant_id=merchant_id,
                        buyer_id=buyer_id,
                        value="visible",
                    ),
                    CheckoutRow(
                        row_id="other_buyer",
                        merchant_id=merchant_id,
                        buyer_id=rival_buyer,
                        value="hidden",
                    ),
                    CheckoutRow(
                        row_id="other_tenant",
                        merchant_id=rival_merchant,
                        buyer_id=buyer_id,
                        value="hidden",
                    ),
                ]
            )
            session.flush()

            repo = CheckoutRepository(session, TenantScope(merchant_id, buyer_id))
            visible = repo.list_all()

            assert [(row.merchant_id, row.buyer_id) for row in visible] == [(merchant_id, buyer_id)]
            with pytest.raises(UnscopedQueryError):
                repo.execute(select(CheckoutRow))
    finally:
        engine.dispose()
