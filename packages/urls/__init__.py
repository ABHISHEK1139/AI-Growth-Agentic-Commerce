"""URL predicates shared by the domain and the delivery layer.

One question today: does a URL address this machine? It is a pure function of the
string — no environment, no configuration, no I/O — so it belongs wherever both
sides can see it. It used to live in ``apps.api.config`` next to the setting it
was written for, which is what made :mod:`services.agent.model` import the
application in order to answer it.

Implementation sits in ``__init__.py`` rather than a submodule, following
:mod:`packages.money`: there is one concern here and splitting it would add a
name to import without adding a boundary.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

__all__ = ["LOOPBACK_HOSTNAMES", "is_loopback_url"]

#: Hostnames that mean "this machine". A model server running here has no
#: credential to present, so this set decides whether one is required.
LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_url(url: str) -> bool:
    """Whether a URL addresses this host.

    Decided by parsing the host, never by looking for a substring. A substring
    test gets this wrong in both directions: ``https://localhost.example.com/v1``
    contains ``localhost`` and is a different machine, and a query string can
    carry ``127.0.0.1`` on a URL pointed at a metered provider. Since the answer
    is what decides whether a credential is required, a false positive here would
    let a remote endpoint be called unauthenticated.
    """
    try:
        hostname = urlsplit(url.strip()).hostname
    except ValueError:
        # A base URL malformed enough that its host cannot be read is not local.
        return False
    if not hostname:
        return False
    host = hostname.lower()
    if host in LOOPBACK_HOSTNAMES:
        return True
    try:
        # Covers the rest of 127.0.0.0/8 and any IPv6 spelling of ::1.
        return ip_address(host).is_loopback
    except ValueError:
        return False
