"""Error vocabulary shared by every layer.

Two modules, deliberately kept apart:

* :mod:`packages.errors.registry` — the code table. Pure data, no dependencies,
  so anything may import it: services, transport, the worker, the frontend type
  generator.
* :mod:`packages.errors.exceptions` — the exception services raise.

Nothing is re-exported here on purpose. Callers import from the submodule they
mean, which keeps the dependency direction obvious and avoids an import cycle
with :mod:`packages.schemas.envelope` (which needs the registry, and which the
exceptions need in turn).
"""
