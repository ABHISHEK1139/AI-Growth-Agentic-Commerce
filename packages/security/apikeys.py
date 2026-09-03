"""API keys for external agents.

An API key is the long-lived credential an external buyer agent holds; it is worth
nothing on its own and is exchanged for a short-lived scoped bearer token
(``POST /agent/auth/token``). That split is the point: the credential that sits in
someone else's configuration file never travels on a request that moves money, and
the credential that does expires in an hour.

Three properties this module exists to guarantee:

* **Never stored in plaintext.** :class:`ApiClient` holds a SHA-256 digest.
  :func:`issue_api_key` returns the plaintext exactly once, to be handed to the
  operator and then forgotten by us.
* **Compared in constant time.** :func:`verify_api_key` uses
  ``hmac.compare_digest``. A plain ``==`` on a hex digest leaks its prefix through
  timing, and a prefix is enough to make brute force cheap.
* **Not enumerable.** Lookup is by digest, so a client is found in one dictionary
  probe without any candidate-by-candidate comparison over stored secrets.

A plain SHA-256 rather than a password KDF is deliberate. These keys are 32 bytes
from :mod:`secrets`, not human-chosen: there is no dictionary to attack and no
guessing advantage for an attacker holding the digest, so the iteration count a KDF
would buy protects nothing while costing latency on every agent request. The
argument is entirely different for a human password, and this module is not for
human passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from packages.observability.context import new_id
from packages.security.principals import Role, Scope, grant_scopes, scopes_for_role

#: Prefixed so a leaked string is recognisable as an AgentPay agent key in a log
#: sweep or a support ticket, and so a key pasted into the wrong field fails fast.
API_KEY_PREFIX = "ak_"

#: 32 bytes of entropy. Long enough that the digest is not attackable; short
#: enough to paste.
API_KEY_BYTES = 32

#: Bound before hashing. A megabyte "key" is not a key.
MAX_API_KEY_LENGTH = 256


def generate_api_key() -> str:
    """A new plaintext key. Cryptographically random, shown once."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(API_KEY_BYTES)}"


def hash_api_key(api_key: str) -> str:
    """The digest stored for ``api_key``.

    Deterministic, so a presented key is found by lookup rather than by comparing
    against every stored client in turn.
    """
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("an api key must be a non-empty string")
    if len(api_key) > MAX_API_KEY_LENGTH:
        raise ValueError("api key is implausibly long")
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(presented: str, stored_hash: str) -> bool:
    """Whether ``presented`` hashes to ``stored_hash``, in constant time.

    Returns ``False`` for a malformed input rather than raising, because the caller
    of this function is answering "does this credential match", and a malformed
    credential simply does not.
    """
    if not isinstance(presented, str) or not isinstance(stored_hash, str):
        return False
    if not presented or not stored_hash or len(presented) > MAX_API_KEY_LENGTH:
        return False
    return hmac.compare_digest(hashlib.sha256(presented.encode("utf-8")).hexdigest(), stored_hash)


@dataclass(frozen=True, slots=True)
class ApiClient:
    """A registered external agent.

    Carries a digest, never a key. ``scopes`` is the ceiling for tokens issued to
    this client, itself already bounded by what the role permits.
    """

    client_id: str
    key_hash: str
    merchant_id: str
    role: Role
    buyer_id: str | None = None
    scopes: frozenset[Scope] = field(default_factory=frozenset)
    label: str = ""
    active: bool = True

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ValueError("an API client must have a client_id")
        if not self.merchant_id:
            raise ValueError("an API client must belong to a merchant tenant")
        if self.role is Role.BUYER and not self.buyer_id:
            raise ValueError("a buyer API client must carry a buyer_id")
        if not self.key_hash or len(self.key_hash) != 64:
            raise ValueError("key_hash must be a sha256 hex digest")
        if self.key_hash.startswith(API_KEY_PREFIX):
            # The one mistake that would defeat the whole module: a plaintext key
            # passed where a digest belongs.
            raise ValueError("key_hash looks like a plaintext api key")
        excess = self.scopes - scopes_for_role(self.role)
        if excess:
            raise ValueError(
                f"role {self.role.value} may not be granted "
                f"{sorted(scope.value for scope in excess)}"
            )

    def matches(self, presented_key: str) -> bool:
        return verify_api_key(presented_key, self.key_hash)

    def as_log_fields(self) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "merchant_id": self.merchant_id,
            "actor_role": self.role.value,
        }


class ApiClientRegistry:
    """The set of registered agent clients, keyed by digest.

    In-memory for now. Task 9 owns the ``api_client`` table and the repository
    behind it; this class is the seam that swap sits behind — the token exchange
    depends on :meth:`resolve` and nothing else, so replacing the storage does not
    touch the authentication path.
    """

    def __init__(self, clients: Iterable[ApiClient] = ()) -> None:
        self._by_hash: dict[str, ApiClient] = {}
        for client in clients:
            self.add(client)

    def add(self, client: ApiClient) -> ApiClient:
        if client.key_hash in self._by_hash:
            raise ValueError("a client with this key digest is already registered")
        self._by_hash[client.key_hash] = client
        return client

    def issue(
        self,
        *,
        merchant_id: str,
        role: Role,
        buyer_id: str | None = None,
        scopes: Iterable[Scope] | None = None,
        label: str = "",
        client_id: str | None = None,
    ) -> tuple[str, ApiClient]:
        """Register a new client and return ``(plaintext_key, client)``.

        The plaintext is returned once and never stored. A caller that loses it
        issues another key; there is no recovery path, by design.
        """
        api_key = generate_api_key()
        client = ApiClient(
            client_id=client_id or new_id("apc"),
            key_hash=hash_api_key(api_key),
            merchant_id=merchant_id,
            role=role,
            buyer_id=buyer_id,
            scopes=grant_scopes(role, scopes),
            label=label,
        )
        self.add(client)
        return api_key, client

    def resolve(self, presented_key: str) -> ApiClient | None:
        """The active client this key belongs to, or ``None``.

        One digest computation, one dictionary probe, then a constant-time
        confirmation of the digest that came back. An inactive client resolves to
        ``None``: revocation is a flag, and the flag is checked here rather than
        being left to each caller to remember.
        """
        if not isinstance(presented_key, str) or not presented_key:
            return None
        if len(presented_key) > MAX_API_KEY_LENGTH:
            return None
        digest = hashlib.sha256(presented_key.encode("utf-8")).hexdigest()
        client = self._by_hash.get(digest)
        if client is None:
            return None
        if not hmac.compare_digest(client.key_hash, digest):  # pragma: no cover - defensive
            return None
        if not client.active:
            return None
        return client

    def revoke(self, client_id: str) -> bool:
        """Deactivate a client. Returns whether one was found."""
        for digest, client in self._by_hash.items():
            if client.client_id == client_id:
                self._by_hash[digest] = ApiClient(
                    client_id=client.client_id,
                    key_hash=client.key_hash,
                    merchant_id=client.merchant_id,
                    role=client.role,
                    buyer_id=client.buyer_id,
                    scopes=client.scopes,
                    label=client.label,
                    active=False,
                )
                return True
        return False

    def clients(self) -> Mapping[str, ApiClient]:
        """Read-only view keyed by digest, for administrative listing."""
        return dict(self._by_hash)

    def __len__(self) -> int:
        return len(self._by_hash)
