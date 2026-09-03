"""The tenant-scoped repository base class (Requirement 24.3, 24.4, Property 28).

Requirement 24.4 says a query built without a tenant filter must *fail* rather than
return unfiltered rows. A runtime assertion inside a method does not deliver that:
it protects the code paths someone remembered to route through it, and the leak
arrives the day a developer writes ``session.execute(select(Checkout))`` in a hurry.
So the guarantee here is structural, and it rests on three decisions:

**The scope is a constructor argument.** ``TenantScopedRepository(session, scope)``
has no default and no keyword fallback. There is no repository object to call a
method on until a :class:`~packages.security.tenancy.TenantScope` exists, and the
scope is immutable afterwards.

**The session is private.** A repository never exposes the ``Session`` it holds.
The only ways to reach the database through this object are its own methods, and
every one of them goes through :meth:`TenantScopedRepository.execute`.

**Every statement is stamped, and the stamp cannot be guessed.**
:meth:`TenantScopedRepository.scoped_select` builds a ``SELECT`` with the tenant
predicate already applied and attaches a marker unique to that repository instance.
:meth:`TenantScopedRepository.execute` refuses any statement not carrying *that*
instance's marker. Chaining more ``.where()`` clauses preserves it, so ordinary use
is unaffected; handing in a bare ``select(Model)``, a ``text("SELECT ...")``, or a
statement stamped by a different repository raises :class:`UnscopedQueryError`. The
marker is an object identity held in a private attribute, so forging one means
reaching into the instance on purpose — at which point the reviewer reading the diff
is the control, and there is something to see.

A repository also cannot be declared for a model that has no tenant column: that
fails at construction, which means "this table is outside the tenant model" has to
be a deliberate decision rather than one that happens by omission.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Final, cast

from sqlalchemy import Result, Select, func, select
from sqlalchemy.orm import Mapper, Session, class_mapper

from packages.security.tenancy import TenantScope, require_tenant_scope

#: Execution-option key carrying the per-instance scope marker.
_SCOPE_MARKER_KEY: Final[str] = "agentpay_tenant_scope_marker"


class RepositoryError(RuntimeError):
    """A repository was misused. Always a programming error, never a client's fault.

    Deliberately not a :class:`~packages.errors.exceptions.DomainError`: these
    surface as ``INTERNAL_ERROR`` with a logged traceback. A missing tenant filter
    that answered a tidy 403 would look, in a test report, exactly like the control
    working correctly.
    """


class UnscopedQueryError(RepositoryError):
    """A statement reached :meth:`TenantScopedRepository.execute` unscoped."""


class ModelNotMappedError(RepositoryError):
    """The declared ``model`` is not a mapped SQLAlchemy class."""


class TenantColumnMissingError(RepositoryError):
    """The declared ``model`` has no column to scope on."""


class TenantScopeMissingBuyerError(RepositoryError):
    """A buyer-owned aggregate was scoped without a buyer identifier."""


class CrossTenantWriteError(RepositoryError):
    """A row was handed to a repository for a tenant it does not belong to."""


class TenantScopedRepository[ModelT]:
    """Base class for every repository. Subclasses declare a model and its columns.

    ::

        class CheckoutRepository(TenantScopedRepository[Checkout]):
            model = Checkout
            buyer_column = "buyer_id"          # buyer-owned aggregate
            requires_buyer_scope = True        # refuse a scope without a buyer

        repo = CheckoutRepository(session, principal.tenant_scope())
    """

    #: The mapped class this repository reads. Declared, not defaulted: the base
    #: class cannot be instantiated, because there would be nothing to scope.
    model: ClassVar[Any]

    #: Column carrying the merchant tenant. Every scoped table has one.
    merchant_column: ClassVar[str] = "merchant_id"

    #: Column carrying the owning buyer, for a buyer-owned aggregate. ``None``
    #: means the table is merchant-scoped only (a product, a policy version).
    buyer_column: ClassVar[str | None] = None

    #: When true, a scope without a ``buyer_id`` is refused at construction. Set on
    #: repositories over checkouts, authorizations, payments, and orders, so a
    #: buyer-owned aggregate cannot be read merchant-wide by accident
    #: (Requirement 24.6).
    requires_buyer_scope: ClassVar[bool] = False

    def __init__(self, session: Session, scope: TenantScope) -> None:
        model = getattr(type(self), "model", None)
        if model is None:
            raise RepositoryError(
                f"{type(self).__name__} must declare a `model`; "
                "TenantScopedRepository is not usable directly"
            )

        self._scope = require_tenant_scope(scope)
        self._mapper = self._resolve_mapper(model)
        self._merchant_attr = self._resolve_column(self.merchant_column)
        self._buyer_attr = (
            self._resolve_column(self.buyer_column) if self.buyer_column is not None else None
        )

        if self.requires_buyer_scope and not self._scope.is_buyer_scoped:
            raise TenantScopeMissingBuyerError(
                f"{type(self).__name__} is over a buyer-owned aggregate and "
                "requires a scope carrying a buyer_id"
            )

        self._session = session
        # Identity, not a value: the marker cannot be reproduced by guessing a
        # string, only by reading this attribute out of the instance.
        self._marker = object()

    # --- Declaration checks ----------------------------------------------

    @classmethod
    def _resolve_mapper(cls, model: Any) -> Mapper[Any]:
        try:
            return class_mapper(model)
        except Exception as exc:  # noqa: BLE001 - any mapping failure means "not a model"
            raise ModelNotMappedError(
                f"{getattr(model, '__name__', model)!r} is not a mapped SQLAlchemy class"
            ) from exc

    def _resolve_column(self, name: str) -> Any:
        """The mapped attribute for ``name``, or raise. Called only at construction."""
        if name not in self._mapper.columns:
            raise TenantColumnMissingError(
                f"{type(self).__name__}.model has no column {name!r}; "
                "a repository cannot be scoped to a tenant without one"
            )
        return getattr(type(self).model, name)

    # --- Scope ------------------------------------------------------------

    @property
    def scope(self) -> TenantScope:
        """The tenant this repository can see. Immutable for its lifetime."""
        return self._scope

    def tenant_predicates(self) -> list[Any]:
        """The filters applied to every statement this repository builds."""
        predicates: list[Any] = [self._merchant_attr == self._scope.merchant_id]
        if self._buyer_attr is not None and self._scope.buyer_id is not None:
            predicates.append(self._buyer_attr == self._scope.buyer_id)
        return predicates

    # --- Statement construction ------------------------------------------

    def scoped_select(self, *entities: Any) -> Select[Any]:
        """A ``SELECT`` already filtered to this tenant and stamped as scoped.

        The only statement builder on this class. Further ``.where()``,
        ``.order_by()``, and ``.limit()`` calls are fine: SQLAlchemy's generative
        methods carry execution options through, so the stamp survives.
        """
        statement = select(*entities) if entities else select(type(self).model)
        statement = statement.where(*self.tenant_predicates())
        return statement.execution_options(**{_SCOPE_MARKER_KEY: self._marker})

    def execute(self, statement: Select[Any]) -> Result[Any]:
        """Run a statement this repository scoped. Anything else raises.

        This is the choke point. ``self._session`` is private and no method other
        than this one reads from it, so every row this class returns came from a
        statement that passed the check below.
        """
        self._require_scoped(statement)
        return self._session.execute(statement)

    def _require_scoped(self, statement: Any) -> None:
        get_options = getattr(statement, "get_execution_options", None)
        marker = get_options().get(_SCOPE_MARKER_KEY) if callable(get_options) else None
        if marker is not self._marker:
            raise UnscopedQueryError(
                f"{type(self).__name__} refused a statement that was not built by "
                "`scoped_select`; a query without a tenant filter is not executable"
            )

    # --- Reads ------------------------------------------------------------

    def scalars(self, statement: Select[Any]) -> Sequence[ModelT]:
        """Entities from a scoped statement, for a subclass's own query."""
        return cast(Sequence[ModelT], self.execute(statement).scalars().all())

    def list_all(self, *, limit: int | None = None) -> list[ModelT]:
        """Every row visible in this scope, optionally capped."""
        statement = self.scoped_select()
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.scalars(statement))

    def find_by(self, **column_values: Any) -> list[ModelT]:
        """Rows matching equality filters, within the scope. Never without it."""
        statement = self.scoped_select()
        for name, value in column_values.items():
            statement = statement.where(self._resolve_column(name) == value)
        return list(self.scalars(statement))

    def get(self, identity: Any) -> ModelT | None:
        """One row by primary key, or ``None``.

        ``None`` for "belongs to another tenant" is the same answer as ``None`` for
        "does not exist" on purpose: distinguishing them tells an unauthorised
        caller that a record exists.
        """
        primary_key = self._mapper.primary_key
        if len(primary_key) != 1:
            raise RepositoryError(
                f"{type(self).__name__}.model has a composite primary key; "
                "use `find_by` with the key columns"
            )
        column = self._resolve_column(str(primary_key[0].key))
        rows = self.scalars(self.scoped_select().where(column == identity))
        return rows[0] if rows else None

    def count(self) -> int:
        """How many rows are visible in this scope."""
        statement = self.scoped_select(func.count()).select_from(type(self).model)
        return int(self.execute(statement).scalar_one())

    def exists(self, **column_values: Any) -> bool:
        return bool(self.find_by(**column_values))

    # --- Writes -----------------------------------------------------------

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new row, stamping and then verifying its tenant columns.

        Stamping alone would be enough to be correct; the verification is there for
        the case where the caller already set a value, because silently rewriting a
        merchant identifier someone passed deliberately would hide a bug rather
        than surface one.
        """
        self._stamp_and_verify(entity, self.merchant_column, self._scope.merchant_id)
        if self.buyer_column is not None and self._scope.buyer_id is not None:
            self._stamp_and_verify(entity, self.buyer_column, self._scope.buyer_id)
        self._session.add(entity)
        return entity

    def _stamp_and_verify(self, entity: ModelT, column: str, expected: str) -> None:
        current = getattr(entity, column, None)
        if current is None:
            setattr(entity, column, expected)
            return
        if current != expected:
            raise CrossTenantWriteError(
                f"{type(self).__name__} refused a row whose {column} does not match "
                "the repository scope"
            )

    def flush(self) -> None:
        """Flush pending writes without committing. The transaction is the caller's."""
        self._session.flush()
