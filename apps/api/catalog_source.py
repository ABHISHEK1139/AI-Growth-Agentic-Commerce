"""Which catalog answered a search, and why.

The buyer surface must work on a laptop with nothing running, and the unit suite
must not require PostgreSQL. That leaves two possible sources for a search, and
the only dangerous option is the one where the caller cannot tell them apart.
So this module does two things: it prefers the database, and it names the source
in the answer.

The fallback is narrow on purpose. It triggers when the datastore is *unreachable
or unmigrated*, never when it is reachable and simply has nothing matching. An
empty published catalog is a real answer and it stays visible as
``postgresql`` with a count of zero — masking it with seed rows would hide the
one condition an operator most needs to see before a demo.

Composition lives here rather than in a service because choosing between a
datastore and a file is a deployment concern, and because the fallback exists for
the case where the deployment's datastore is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from apps.api.db import get_session_factory
from packages.observability.logging import get_logger
from services.offers.constraints import OfferCandidate, OfferConstraints
from services.offers.seed import search_seed_candidates
from services.offers.service import OfferService

logger = get_logger(__name__)

CatalogSourceName = Literal["postgresql", "seed_fixture"]

#: Human-readable note attached to a degraded answer, so the reason travels with
#: the response instead of living only in a log line.
SEED_FALLBACK_NOTE = (
    "The published catalog was unreachable, so this answer came from the seed "
    "import artifacts. Filter semantics are identical; the record set is smaller."
)


@dataclass(frozen=True, slots=True)
class CatalogSearchOutcome:
    """A constrained search, plus provenance a reviewer can check."""

    candidates: list[OfferCandidate]
    source: CatalogSourceName
    #: Populated only when the database path was abandoned. The exception class
    #: name, never the driver message: a connection error routinely embeds the
    #: DSN, and the DSN embeds a password.
    degraded_reason: str | None = None

    @property
    def is_degraded(self) -> bool:
        return self.degraded_reason is not None


def search_catalog(
    *,
    merchant_id: str,
    constraints: OfferConstraints,
    now: datetime | None = None,
) -> CatalogSearchOutcome:
    """Run a constrained search against the published catalog, or the seed artifacts.

    The session is managed here rather than injected as a request dependency
    because the failure being handled is the session failing to reach anything:
    a dependency that yields and then commits would raise on teardown, after this
    function had already recovered.
    """
    session: Session | None = None
    try:
        session = get_session_factory()()
        candidates = OfferService().search_offer_candidates(
            session, merchant_id=merchant_id, constraints=constraints, now=now
        )
        return CatalogSearchOutcome(candidates=candidates, source="postgresql")
    except Exception as exc:
        if session is not None:
            try:
                session.rollback()
            except Exception:
                pass
        reason = type(exc).__name__
        logger.warning(
            "catalog search fell back to the seed artifacts",
            extra={"event": "CATALOG_SOURCE_DEGRADED", "error_kind": reason},
        )
        return CatalogSearchOutcome(
            candidates=search_seed_candidates(
                merchant_id=merchant_id, constraints=constraints, now=now
            ),
            source="seed_fixture",
            degraded_reason=reason,
        )
    finally:
        if session is not None:
            session.close()
