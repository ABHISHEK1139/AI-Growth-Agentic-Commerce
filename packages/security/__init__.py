"""Authentication, authorization, and tenancy primitives (Requirement 24).

Pure logic only: no FastAPI, no SQLAlchemy, no settings object. Everything here
takes its secret, its clock, and its identifiers as arguments, which is what makes
an expired token, a tampered signature, and a cross-tenant scope testable without
a request, a database, or a running clock.

Four modules, deliberately separate:

* :mod:`packages.security.tenancy` — :class:`~packages.security.tenancy.TenantScope`,
  the value that :mod:`packages.db.repository` demands at construction. Imports
  nothing from this package, so the data layer can depend on it without pulling in
  the notion of a principal.
* :mod:`packages.security.principals` — roles, scopes, and the
  :class:`~packages.security.principals.Principal` every authenticated request
  resolves to.
* :mod:`packages.security.tokens` — the signed-token codec: issue, verify
  signature, verify expiry, rebuild a principal.
* :mod:`packages.security.apikeys` — long-lived agent credentials, stored as a
  hash and compared in constant time.
* :mod:`packages.security.authorization` — the checks a caller performs against a
  principal: role, scope, tenant, ownership.

Nothing is re-exported here, matching :mod:`packages.errors`: callers import the
module they mean so the dependency direction stays visible.
"""
