"""Data-access primitives shared by every service.

Two modules:

:mod:`packages.db.repository`
    The tenant-scoped repository base class the design requires ("The repository
    base class requires a tenant filter at construction, so an unfiltered query is
    a type error rather than a data leak"). It works against any mapped class and
    deliberately does not import the declarative base.
:mod:`packages.db.base`
    The declarative :class:`~packages.db.base.Base` every ORM model is declared
    against.

Both live in ``packages`` rather than in ``apps.api`` for the same reason: the
import contracts forbid a domain service from importing the API layer, and every
service's ``repository.py`` and ``models.py`` needs one of these. The engine, the
session factory, and the per-request session dependency stay in ``apps.api.db``,
because selecting a datastore and scoping a unit of work to a request are delivery
concerns rather than shared domain vocabulary.

Nothing is re-exported here: an import states which of the two it wants, and
neither module has to be imported to reach the other.
"""
