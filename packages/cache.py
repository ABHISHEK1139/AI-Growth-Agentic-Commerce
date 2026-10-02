"""Read-through cache for hot catalogue reads, backed by Redis.

Why this exists
---------------
Measured, not assumed. §4.1 of ``docs/production/READINESS_PLAN.md`` puts the service's
clean ceiling at ~200 req/s, and identifies the limit as **database round-trips per
request** rather than compute: at 200 req/s the API peaked at 6% CPU and PostgreSQL at
0.03%, while ``/health`` — which touches no database — sustained 250 req/s on the same
machine.

So the way to move the ceiling is to make fewer queries, not to add more replicas. This
module is that.

Design constraints, in priority order
--------------------------------------
1. **Never fail a request.** A cache outage must degrade to the current behaviour, which
   is a database read. Taking checkout down because Redis is unavailable is strictly
   worse than the slower path this avoids. Same posture as
   :class:`~apps.api.middleware.ratelimit.RedisRateLimitBackend`.
2. **Never serve stale financial state.** Only reads that are *already* safe to serve
   late go through here. Prices and payment status are read live, because a cached price
   is a wrong price.
3. **Bounded blast radius of a bug.** Keys are namespaced and versioned, so a stale
   entry left by an older release expires rather than persisting across a deploy.

Invalidation
------------
Explicit, never time-based-only. ``invalidate()`` takes a *namespace* and drops every key
under it via ``SCAN``/``UNLINK``, so a catalogue publish invalidates the whole catalogue
in one call instead of requiring the write path to enumerate what it changed.

That is the deliberate trade: a scan on write is more expensive than a targeted delete,
and it is the right trade here because catalogue writes are rare (a publish, an import)
while catalogue reads are the hot path. A read-heavy system with frequent writes would
want the opposite and should not use this module unchanged.

Failure handling
----------------
Every Redis call is wrapped. On failure the backend enters a cooldown during which it
does not even attempt a connection, so an outage adds nothing to request latency beyond
the first failure. Without that, every request would pay the connect timeout — turning a
cache into a latency *amplifier*, which is the classic way a cache makes an incident
worse.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

__all__ = [
    "CacheBackend",
    "InMemoryCacheBackend",
    "RedisCacheBackend",
    "NullCacheBackend",
    "get_cache",
    "reset_cache",
]


class CacheBackend(Protocol):
    """What this module needs from a store. Two implementations ship; both are tested."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def drop_namespace(self, prefix: str) -> int: ...

    @property
    def degraded(self) -> bool: ...


class NullCacheBackend:
    """Disables caching without changing any call site.

    Used when no Redis URL is configured, and by tests that want to assert the
    uncached path. Every method succeeds and reads always miss, so instrumenting a read
    site is a two-line change rather than a branch at each one.
    """

    degraded = False

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None

    def drop_namespace(self, prefix: str) -> int:
        return 0


class InMemoryCacheBackend:
    """Process-local cache. Correct for one worker, wrong for several.

    Used by the unit suite, and available as a deliberate single-process deployment
    choice. **Not** correct behind multiple API replicas: each has its own copy, so an
    invalidation on one worker leaves the others serving stale entries until their TTL
    expires. ``packages.cache`` documents that rather than hiding it, because the
    failure is invisible until a user sees a stale price.

    Expiry is checked on read rather than by a sweeper, so an expired entry costs one
    comparison and no background thread.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._entries: dict[str, tuple[str, float]] = {}
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def degraded(self) -> bool:
        return False

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                return None
            return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._entries[key] = (value, self._clock() + ttl_seconds)

    def drop_namespace(self, prefix: str) -> int:
        with self._lock:
            doomed = [k for k in self._entries if k.startswith(prefix)]
            for key in doomed:
                del self._entries[key]
            return len(doomed)


class RedisCacheBackend:
    """Redis-backed cache that fails open and cools down after a failure.

    See the module docstring for why cooldown matters: without it, a Redis outage makes
    every request pay the connect timeout, so the cache amplifies latency exactly when
    the system is already unhealthy.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        timeout_seconds: float = 0.25,
        cooldown_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis_url = redis_url
        self._timeout_seconds = timeout_seconds
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._client: Any = None
        self._unavailable_until: float = 0.0
        self._lock = threading.Lock()

    @property
    def degraded(self) -> bool:
        return self._clock() < self._unavailable_until

    def _connect(self) -> Any:
        import redis

        return redis.Redis.from_url(
            self._redis_url,
            socket_connect_timeout=self._timeout_seconds,
            socket_timeout=self._timeout_seconds,
            retry_on_timeout=False,
        )

    def _degrade(self) -> None:
        with self._lock:
            self._unavailable_until = self._clock() + self._cooldown_seconds
            client = self._client
            self._client = None
        if client is not None:
            # Closing an already-broken client is best effort; a failure here changes
            # nothing about the request that already failed.
            with contextlib.suppress(Exception):
                client.close()

    def _live_client(self) -> Any | None:
        if self.degraded:
            return None
        with self._lock:
            if self._client is None:
                try:
                    self._client = self._connect()
                except Exception:  # noqa: BLE001 - any failure means "no cache"
                    self._degrade()
                    return None
            return self._client

    def get(self, key: str) -> str | None:
        client = self._live_client()
        if client is None:
            return None
        try:
            raw = client.get(key)
        except Exception:  # noqa: BLE001 - a cache miss is always an acceptable answer
            self._degrade()
            return None
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        client = self._live_client()
        if client is None:
            return
        try:
            client.set(key, value, ex=ttl_seconds)
        except Exception:  # noqa: BLE001 - failing to cache must not fail the request
            self._degrade()

    def drop_namespace(self, prefix: str) -> int:
        """Delete every key under ``prefix``.

        ``SCAN`` rather than ``KEYS``: ``KEYS`` is O(n) over the whole keyspace and
        blocks the Redis event loop for the duration, which on a shared instance is a
        self-inflicted outage on the write path. ``UNLINK`` frees the memory
        asynchronously so the delete does not block either.
        """
        client = self._live_client()
        if client is None:
            return 0
        removed = 0
        try:
            for key in client.scan_iter(match=f"{prefix}*", count=500):
                client.unlink(key)
                removed += 1
        except Exception:  # noqa: BLE001 - a failed invalidation is logged by the caller
            self._degrade()
        return removed


# ---------------------------------------------------------------------------
# Versioned keys
# ---------------------------------------------------------------------------
#
# The version is in the key, not the value, so an entry written by an older release is
# never read by a newer one even if its TTL has not elapsed. Bumping ``KEY_VERSION`` is
# the supported way to invalidate everything at once -- which is what you want when a
# bug is in the cached *shape*, and doing that by waiting out TTLs is not a plan.

KEY_VERSION = "v1"
KEY_PREFIX = f"agentpay:cache:{KEY_VERSION}"


def build_key(namespace: str, *parts: str) -> str:
    """Compose a namespaced, versioned key.

    Parts are joined with ``:``. A part containing a colon could collide with a
    different decomposition of the same key, so ids containing colons are rejected
    rather than silently producing a key that some other call site also generates.
    """
    for part in parts:
        if ":" in part:
            raise ValueError(
                f"cache key part may not contain ':' (got {part!r}); it would make the "
                "key ambiguous and could collide with another namespace"
            )
    suffix = ":".join(parts)
    return f"{KEY_PREFIX}:{namespace}" + (f":{suffix}" if suffix else "")


# ---------------------------------------------------------------------------
# Process-wide backend
# ---------------------------------------------------------------------------

_backend: CacheBackend | None = None
_backend_lock = threading.Lock()


def get_cache(redis_url: str | None = None) -> CacheBackend:
    """The process-wide cache backend, built on first use.

    Built lazily so importing this module never opens a connection: unit tests import
    the services that use it without a Redis running, and ``/health`` must stay
    answerable when Redis is down.
    """
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is None:
            _backend = RedisCacheBackend(redis_url) if redis_url else NullCacheBackend()
    return _backend


def reset_cache(backend: CacheBackend | None = None) -> None:
    """Replace or clear the process-wide backend.

    Tests call this with an :class:`InMemoryCacheBackend`; production calls it with
    ``None`` after a settings change.
    """
    global _backend
    with _backend_lock:
        _backend = backend


def cached(
    namespace: str,
    *parts: str,
    ttl_seconds: int,
    produce: Callable[[], Any],
    backend: CacheBackend | None = None,
) -> Any:
    """Return a cached value, or compute, store and return it.

    A plain function rather than a context manager: the value is needed as a return,
    not as a ``with`` block, and a context manager would force every call site to wrap
    a single value in a block that adds nothing.

    On a cache miss or a cache failure, ``produce`` runs and the request proceeds
    normally -- the only difference is that ``produce`` also warms the cache. Cache
    calls are wrapped, so a backend that raises cannot take the request down, which is
    constraint 1 in the module docstring.
    """
    store = backend if backend is not None else get_cache()
    key = build_key(namespace, *parts)

    # The read is wrapped for the same reason as the write: a store that raises must
    # not be able to fail the request. `RedisCacheBackend` handles its own Redis errors
    # and degrades, but a backend that raises for any other reason -- a misconfigured
    # URL, a serialisation surprise, a future implementation -- would otherwise take the
    # endpoint down. The cache is an optimisation; it is never allowed to be a
    # dependency, and the only way to guarantee that is to not trust the callee.
    hit = None
    with contextlib.suppress(Exception):
        hit = store.get(key)

    if hit is not None:
        try:
            return json.loads(hit)
        except json.JSONDecodeError:
            # A corrupt or legacy-shaped entry. Treated as a miss and overwritten below
            # rather than propagated: a cache entry must never be able to fail a request
            # it was only meant to speed up.
            pass

    value = produce()

    # Failing to cache must not fail the request that succeeded in computing the value,
    # so the write is best effort by construction.
    with contextlib.suppress(Exception):
        store.set(key, json.dumps(value, default=str), ttl_seconds)

    return value


def invalidate(namespace: str, *parts: str) -> int:
    """Drop one entry, or a whole namespace when ``parts`` is empty.

    Returns the number of keys actually removed, which is not cosmetic. A caller that
    invalidates a mistyped namespace must be able to see that it removed nothing --
    otherwise "invalidated" reads as success while stale entries live on for their full
    TTL, which for an offer means a stale price.
    """
    store = get_cache()
    key = build_key(namespace, *parts)
    try:
        # Either way the prefix is the entry itself; the namespace form simply appends
        # a separator so it cannot match a sibling namespace.
        return store.drop_namespace(key if parts else f"{key}:")
    except Exception:  # noqa: BLE001 - a failed invalidation is reported as "removed nothing"
        return 0
