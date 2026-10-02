"""Mint a load-test session token for the merchant that owns the seeded rows.

Why a script rather than a login call
-------------------------------------
The catalogue and offer endpoints are tenant-scoped: unauthenticated they fall back to
``merchant_demo`` and return 404 for every seeded row. Load testing those endpoints
without a session would measure 404s, which are fast and meaningless.

Signing a token directly with the same secret the API uses keeps the harness independent
of password policy, rate limits and seeded operator accounts, so the numbers measure the
*read path* rather than the login path.

The token is short-lived and carries a real role, exactly as a browser session would.

Run through the API container so ``SESSION_SECRET`` is the live one::

    docker compose exec -T api python infra/loadtest/mint_session.py

It prints the raw token, ready for ``SESSION_TOKEN=`` in run-load-test.ps1.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from apps.api.config import get_settings
from packages.security.principals import Role
from packages.security.tokens import issue_session_token

# Matches the ownership pattern in seed.sql exactly, including its off-by-one:
# offers are assigned ``'ld_merch_' || (1 + (g % 200))``, so g=1 lands on merchant 2.
# The load script reads offers on the same 200-stride (1, 201, 401, ...), which is why
# this merchant is the one whose catalogue those reads actually resolve to. Getting this
# wrong returns OFFER_NOT_FOUND for every request and the harness silently measures
# 404s -- fast, and completely meaningless.
MERCHANT_ID = "ld_merch_2"


def main() -> int:
    settings = get_settings()
    issued = issue_session_token(
        secret=settings.session_secret,
        subject="ld_loadtest_console",
        role=Role.MERCHANT_ADMIN,
        merchant_id=MERCHANT_ID,
        ttl_seconds=3600,
    )
    print(issued.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
