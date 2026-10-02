"""Persistent storage for external-agent API keys.

The registry these replace was in-memory and empty on every boot
(:meth:`apps.api.auth.install_auth`), so a merchant minted an agent key, handed
it to a buyer agent, restarted the API, and every subsequent token exchange
failed with a 401 that looked like a wrong key. The ``api_client`` table existed
in the first migration the whole time.

Why a repository and not a database-backed registry
---------------------------------------------------
:class:`~packages.security.apikeys.ApiClientRegistry` is the *authentication*
seam: it answers "does this presented key match an active client" in constant
time, and the token exchange depends on nothing else. Persisting it is therefore
a change behind that interface, not a change to the exchange. Keeping the split
means the credential comparison stays pure and testable without a database,
which is where the timing properties are asserted.

What is and is not stored
-------------------------
``key_hash`` only. The plaintext is returned once by the mint endpoint and never
written. That is what makes a database dump useless for authenticating as an
agent -- but it also means there is no recovery path: a lost key is reissued, not
recovered. That is deliberate, and the mint endpoint says so.

The concurrency note
--------------------
Revocation is a status column rather than a delete, so a token already issued
stays verifiable until it expires while new exchanges fail immediately. A delete
would retroactively invalidate tokens that a buyer agent is mid-checkout with,
and the buyer's authorization would fail for a reason the merchant cannot see.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.security.apikeys import ApiClient, ApiClientRegistry
from packages.security.principals import Role, Scope
from services.connectors.models import ApiClient as ApiClientRow

#: How many active clients one token exchange will load.
#:
#: A cap rather than a full scan, so a deployment with a very large key
#: population cannot turn a single exchange into an unbounded read. The cost of
#: hitting it is a 401 for a client outside the most recent N, which is a
#: diagnosable failure -- and one no realistic deployment reaches. Raise it when a
#: real one does, not before.
MAX_LOADED_CLIENTS = 5_000

#: Ceiling for a tenant-scoped *listing* (``list_for_merchant``). Much lower than
#: ``MAX_LOADED_CLIENTS`` on purpose: the load-all path is a bounded failure with a
#: diagnosable 401, whereas a listing is unbounded input multiplied by response size.
#: Even a badly behaved issuer does not produce a legitimate tenant with thousands of
#: keys, so a generous ceiling costs no real caller anything.
MAX_LISTED_CLIENTS = 500


class ApiClientRepository:
    """Load and save :class:`ApiClient` records for one database session.

    Not a context manager and not session-owning: the caller's request-scoped
    session is the unit of work, so a key minted and used inside one request is
    visible without a commit, and a failed request rolls the insert back with
    everything else.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- Reads ------------------------------------------------------------

    def resolve(self, key_hash: str) -> ApiClient | None:
        """The active client for a presented key digest, or ``None``.

        One query by digest rather than a scan, so the lookup cost does not grow
        with the number of registered agents -- which is what keeps it safe to
        compare against every stored client at all.
        """
        row = self._session.execute(
            select(ApiClientRow).where(ApiClientRow.key_hash == key_hash)
        ).scalar_one_or_none()
        if row is None or row.status != "active":
            return None
        return _to_domain(row)

    def load_into(self, registry: ApiClientRegistry) -> list[str]:
        """Add every active client to ``registry``. Returns the ids that were skipped.

        This is the read the token exchange performs, and it is per-row
        fault-tolerant on purpose. A row whose role this build does not recognise
        -- written by a newer version, or hand-edited -- must cost *that* agent
        its key, not every agent on the platform a 500. The skipped ids come back
        so the caller can log which rows are broken; a silently dropped
        credential is indistinguishable from a wrong one.
        """
        skipped: list[str] = []
        rows = self._session.execute(
            select(ApiClientRow)
            .where(ApiClientRow.status == "active")
            .order_by(ApiClientRow.created_at.desc())
            .limit(MAX_LOADED_CLIENTS)
        ).scalars()
        for row in rows:
            try:
                registry.add(_to_domain(row))
            except (_UnparseableClient, ValueError):
                # ValueError covers a duplicate digest (which the unique index
                # already prevents) and a scope set that exceeds the role ceiling.
                skipped.append(row.api_client_id)
        return skipped

    def list_all_active(self) -> list[ApiClient]:
        """Every active client, or raise on the first unreadable row.

        Strict, for administrative callers that need to know the whole population
        is intact. The token exchange uses :meth:`load_into` instead, which drops
        an unreadable row rather than failing every request.
        """
        rows = self._session.execute(
            select(ApiClientRow)
            .where(ApiClientRow.status == "active")
            .order_by(ApiClientRow.created_at.desc())
            .limit(MAX_LOADED_CLIENTS)
        ).scalars()
        return [_to_domain(row) for row in rows]

    def list_for_merchant(self, merchant_id: str, *, limit: int | None = None) -> list[ApiClient]:
        """Every key for a tenant, newest first.

        Bounded by ``MAX_LISTED_CLIENTS``. Key issuance is meant to be rare, but
        nothing prevents a merchant or a compromised session from minting rows in a
        loop, and an unbounded listing turns that into a full-table read and a large
        response per request. Callers that genuinely need everything (an export, an
        archival audit) can pass an explicit ``limit``.
        """
        statement = (
            select(ApiClientRow)
            .where(ApiClientRow.merchant_id == merchant_id)
            .order_by(ApiClientRow.created_at.desc())
        )
        statement = statement.limit(
            MAX_LISTED_CLIENTS if limit is None else min(limit, MAX_LISTED_CLIENTS)
        )
        rows = self._session.execute(statement).scalars()
        return [_to_domain(row) for row in rows]

    def get(self, client_id: str, merchant_id: str) -> ApiClient | None:
        """One client, scoped to a tenant.

        Scoped rather than looked up by id alone: a client id from another tenant
        is not "not found" in the sense of being absent, and answering for it
        would confirm the id exists somewhere.
        """
        row = self._session.execute(
            select(ApiClientRow).where(
                ApiClientRow.api_client_id == client_id,
                ApiClientRow.merchant_id == merchant_id,
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    # --- Writes -----------------------------------------------------------

    def add(self, client: ApiClient) -> ApiClient:
        """Persist a newly minted client. The hash is already computed.

        ``client_id`` is required, not defaulted. A generated fallback would let a
        mint path write two rows under one primary key -- which SQLite permits and
        PostgreSQL does not, so it would be a failure that only appears after
        deployment.
        """
        self._session.add(
            ApiClientRow(
                api_client_id=client.client_id,
                merchant_id=client.merchant_id,
                key_hash=client.key_hash,
                scopes=sorted(scope.value for scope in client.scopes),
                status="active" if client.active else "revoked",
                label=client.label,
                role=client.role.value,
                buyer_id=client.buyer_id,
                created_at=datetime.now(UTC),
            )
        )
        self._session.flush()
        return client

    def revoke(self, client_id: str, merchant_id: str) -> bool:
        """Mark one client revoked. Returns whether a row was changed.

        A status change and not a delete, so the history of who held which key
        survives -- the merchant console's "which agents have access" question is
        only answerable while the rows remain.
        """
        row = self._session.execute(
            select(ApiClientRow).where(
                ApiClientRow.api_client_id == client_id,
                ApiClientRow.merchant_id == merchant_id,
            )
        ).scalar_one_or_none()
        if row is None or row.status != "active":
            return False
        row.status = "revoked"
        self._session.flush()
        return True

    def count_active(self, merchant_id: str) -> int:
        from sqlalchemy import func

        return self._session.execute(
            select(func.count(ApiClientRow.api_client_id)).where(
                ApiClientRow.merchant_id == merchant_id,
                ApiClientRow.status == "active",
            )
        ).scalar_one()


def _to_domain(row: ApiClientRow) -> ApiClient:
    """Rebuild the domain object from a row.

    An unrecognised role or scope is dropped rather than raising: a row written
    by a newer build, or hand-edited, must not be able to crash the token
    exchange for every other agent. A client whose role no longer parses resolves
    to nothing, which refuses it.
    """
    try:
        role = Role(row.role)
    except (AttributeError, ValueError):
        # Refuse this client rather than the request. `ApiClient.__post_init__`
        # would raise on an unrecognised role, and one bad row must not take
        # down token exchange for every other agent on the platform.
        raise _UnparseableClient(row.api_client_id) from None

    if role is Role.BUYER and not row.buyer_id:
        # A buyer client with no buyer is a client whose authority is undefined.
        # Refused rather than defaulted: a guessed `buyer_id` would attribute
        # the agent's purchases to an arbitrary wallet.
        raise _UnparseableClient(row.api_client_id, "buyer client has no buyer_id")

    scopes: set[Scope] = set()
    for value in row.scopes or []:
        try:
            scopes.add(Scope(value))
        except ValueError:
            # One unknown scope does not invalidate the rest. A client issued
            # before a scope was renamed keeps working with the scopes it has.
            continue

    return ApiClient(
        client_id=row.api_client_id,
        key_hash=row.key_hash,
        merchant_id=row.merchant_id,
        role=role,
        buyer_id=row.buyer_id,
        scopes=frozenset(scopes),
        label=row.label or "",
        active=row.status == "active",
    )


class _UnparseableClient(Exception):
    """A stored row this build cannot interpret.

    Raised rather than skipped silently so the misconfiguration is visible in a
    log instead of presenting as "the key is invalid", which sends the merchant
    looking for a wrong key rather than at their database.

    The message names both *why* it is unusable, because the two causes have very
    different fixes: an unrecognised role means this build is older than the row,
    and a buyer client with no ``buyer_id`` means the row is incomplete.
    """

    def __init__(
        self, client_id: str, reason: str = "role is not recognised by this build"
    ) -> None:
        super().__init__(f"api_client {client_id}: {reason}")
        self.client_id = client_id
        self.reason = reason
