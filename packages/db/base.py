"""The declarative base every ORM model inherits from.

It lives here rather than in ``apps.api.db`` because the mapped classes live in
``services.*.models``, and a domain service may not import the delivery layer
(the "Domain services never import the API or web layers" contract). Every one of
the eight ``services/*/models.py`` modules needs this class, so while it sat in
``apps.api.db`` the schema itself was the reason the dependency ran backwards:
eight domain modules importing the application in order to declare a table.

Nothing configuration-dependent belongs in this module, and nothing here reads
the environment. The engine, the session factory, and the request-scoped session
dependency stay in ``apps.api.db``, because choosing a datastore and managing a
unit of work per request are delivery concerns. What is shared is only the
registry the models are declared against.

``apps.api.db`` re-exports :class:`Base` so the delivery layer keeps reading as
one module, and ``infra/migrations`` can reach the metadata from either name.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""
