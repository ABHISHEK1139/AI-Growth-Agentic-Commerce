"""Tool-facing commerce port.

This package holds the *contract* between the agent layer and the commerce core.
It deliberately contains no implementation and no persistence dependency, so the
agent layer can depend on it without acquiring a path to the database.
"""

from packages.commerce.facade import CommerceFacade

__all__ = ["CommerceFacade"]
