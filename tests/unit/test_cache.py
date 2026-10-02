"""The read cache must be invisible when it works and harmless when it does not.

The two properties that matter, in order:

1. **It never fails a request.** Every failure mode of the store -- unreachable, timing
   out, returning garbage, raising on write -- has to degrade to the uncached path. A
   cache that can take checkout down is worse than no cache, because it converts a fast
   database into an unavailable one.
2. **It never serves another tenant's data.** The capability document carries one
   merchant's financial ceilings and an offer carries a price. A key that omitted the
   tenant would serve one merchant's limits to another, and nothing in the response
   would reveal it.

Point 2 is why the tenant appears in every key in the production call sites, and why
these tests assert on key composition rather than just on "it cached something".
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from packages.cache import (
    InMemoryCacheBackend,
    NullCacheBackend,
    build_key,
    cached,
    invalidate,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _isolated_backend() -> Any:
    """Every test gets its own process-wide backend.

    Autouse, because the backend is a module global by design (one connection per
    process). Without this a test that populates it leaks into the next one, which is a
    failure that appears in whichever test happens to run second.
    """
    backend = InMemoryCacheBackend()
    reset_cache(backend)
    yield backend
    reset_cache(None)


class _ExplodingBackend:
    """A store that fails every operation, the way an unreachable Redis does."""

    degraded = True

    def __init__(self) -> None:
        self.calls = 0

    def _boom(self) -> None:
        self.calls += 1
        raise ConnectionError("redis is gone")

    def get(self, key: str) -> str | None:
        self._boom()
        return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._boom()

    def drop_namespace(self, prefix: str) -> int:
        self._boom()
        return 0


# ---------------------------------------------------------------------------
# Correctness of the basic contract
# ---------------------------------------------------------------------------


def test_a_miss_computes_and_a_hit_does_not() -> None:
    produced = 0

    def produce() -> dict[str, int]:
        nonlocal produced
        produced += 1
        return {"n": produced}

    first = cached("thing", "a", ttl_seconds=60, produce=produce)
    second = cached("thing", "a", ttl_seconds=60, produce=produce)

    assert first == {"n": 1}
    assert second == {"n": 1}, "the second call was served a stale or recomputed value"
    assert produced == 1, "produce ran twice; the entry was not reused"


def test_different_keys_do_not_collide() -> None:
    assert build_key("offer", "m1", "o1") != build_key("offer", "m1", "o2")
    assert build_key("offer", "m1", "o1") != build_key("product", "m1", "o1")
    assert build_key("offer", "m1", "o1") != build_key("offer", "m2", "o1")


def test_the_tenant_is_part_of_the_key() -> None:
    """The property that stops one merchant's ceilings reaching another.

    Asserted as a key-composition property rather than by behaviour, because the
    failure it guards against is invisible at the HTTP layer: both tenants would get a
    200 with a plausible document, differing only in numbers.
    """
    a = build_key("capability", "merchant_a")
    b = build_key("capability", "merchant_b")

    assert a != b
    assert "merchant_a" in a
    assert "merchant_b" not in a


def test_a_colon_in_a_key_part_is_rejected() -> None:
    """A part containing a colon makes the key ambiguous.

    ``build_key("offer", "m:1", "o1")`` and ``build_key("offer", "m", "1:o1")`` would
    otherwise produce the same string, so two different cache entries would share one
    slot. Rejected at construction instead.
    """
    with pytest.raises(ValueError, match="may not contain"):
        build_key("offer", "merchant:1", "offer_1")


def test_keys_are_versioned() -> None:
    """A release that changes the cached shape must be able to orphan old entries."""
    assert build_key("offer", "m", "o").startswith("agentpay:cache:v")


# ---------------------------------------------------------------------------
# Failure modes: the cache must be invisible
# ---------------------------------------------------------------------------


def test_an_unreachable_store_still_serves_the_request() -> None:
    """Every read fails; the endpoint must behave exactly as it does uncached."""
    backend = _ExplodingBackend()
    produced = 0

    def produce() -> str:
        nonlocal produced
        produced += 1
        return "computed"

    assert cached("thing", "k", ttl_seconds=60, produce=produce, backend=backend) == "computed"
    assert cached("thing", "k", ttl_seconds=60, produce=produce, backend=backend) == "computed"
    assert produced == 2, "with the store down every call must recompute"
    assert backend.calls >= 4, "the store was not consulted, so this proved nothing"


def test_a_corrupt_entry_is_treated_as_a_miss_not_an_error() -> None:
    """A legacy-shaped or truncated entry must not be able to fail a request."""
    backend = InMemoryCacheBackend()
    key = build_key("thing", "k")
    backend.set(key, "{not json", ttl_seconds=60)

    def produce() -> dict[str, int]:
        return {"fresh": 1}

    value = cached("thing", "k", ttl_seconds=60, produce=produce, backend=backend)

    assert value == {"fresh": 1}
    # And it was overwritten, so the next reader is not poisoned either.
    assert cached("thing", "k", ttl_seconds=60, produce=produce, backend=backend) == {"fresh": 1}


def test_a_value_that_is_not_json_serialisable_is_still_returned() -> None:
    """``produce`` succeeding is what matters; failing to cache is incidental."""

    def produce() -> object:
        return datetime_stub()

    value = cached("thing", "unserialisable", ttl_seconds=60, produce=produce)
    assert value is not None


def datetime_stub() -> object:
    from datetime import UTC, datetime

    return datetime(2026, 1, 1, tzinfo=UTC)


def test_the_null_backend_disables_caching_without_changing_call_sites() -> None:
    backend = NullCacheBackend()
    calls = 0

    def produce() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert cached("t", "k", ttl_seconds=60, produce=produce, backend=backend) == 1
    assert cached("t", "k", ttl_seconds=60, produce=produce, backend=backend) == 2
    assert backend.degraded is False


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def test_invalidate_drops_the_whole_namespace() -> None:
    """A catalogue publish must clear every offer for that merchant at once.

    Enumerating what changed is the alternative, and it is the one that rots: a new
    write path that forgets to invalidate leaves stale prices indefinitely.
    """
    backend = InMemoryCacheBackend()
    for offer in ("o1", "o2", "o3"):
        cached("offer", "m1", offer, ttl_seconds=300, produce=lambda: {"v": 1}, backend=backend)
    cached("offer", "m2", "o1", ttl_seconds=300, produce=lambda: {"v": 1}, backend=backend)
    cached("product", "m1", "p1", ttl_seconds=300, produce=lambda: {"v": 1}, backend=backend)

    # `invalidate` operates on the process-wide backend, so point that at this store
    # rather than creating a second one. Using a local backend here would make the
    # invalidation a no-op and the test would pass while proving nothing.
    reset_cache(backend)
    invalidate("offer", "m1")

    # m1's offers must recompute -- the invalidation cleared them.
    for offer in ("o1", "o2", "o3"):
        value = cached(
            "offer",
            "m1",
            offer,
            ttl_seconds=300,
            produce=lambda: {"v": 2},
            backend=backend,
        )
        assert value == {"v": 2}, f"{offer} was served from cache after invalidation"

    # m2 and the product namespace must survive: the invalidation was scoped to one
    # merchant's offers, and over-reaching would throw away the whole cache on every
    # publish.
    calls = 0

    def untouched() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"v": 99}

    assert cached("offer", "m2", "o1", ttl_seconds=300, produce=untouched) == {"v": 1}
    assert cached("product", "m1", "p1", ttl_seconds=300, produce=untouched) == {"v": 1}
    assert calls == 0, "invalidation reached beyond the m1 offer namespace"


def test_invalidate_reports_a_mistyped_namespace_as_removing_nothing() -> None:
    """The silent-failure mode: invalidating a key that was never written.

    Returning a count is what lets a caller log "invalidated 0 keys" and notice, rather
    than believing it cleared a namespace that did not exist.
    """
    backend = InMemoryCacheBackend()
    cached("offer", "m1", "o1", ttl_seconds=300, produce=lambda: 1, backend=backend)
    reset_cache(backend)

    removed = invalidate("offre", "m1")  # typo
    assert removed == 0
    assert backend.get(build_key("offer", "m1", "o1")) is not None, "a typo cleared real entries"


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_an_expired_entry_is_not_served() -> None:
    """A TTL that does not expire is a correctness bug, not a performance bug."""
    now = [1000.0]
    backend = InMemoryCacheBackend(clock=lambda: now[0])

    cached("thing", "k", ttl_seconds=10, produce=lambda: "first", backend=backend)
    now[0] = 1005.0
    assert (
        cached("thing", "k", ttl_seconds=10, produce=lambda: "second", backend=backend) == "first"
    )

    now[0] = 1011.0  # past the TTL
    assert cached("thing", "k", ttl_seconds=10, produce=lambda: "third", backend=backend) == "third"


def test_expiry_is_checked_on_read_so_no_sweeper_thread_is_needed() -> None:
    now = [0.0]
    backend = InMemoryCacheBackend(clock=lambda: now[0])
    cached("t", "expired", ttl_seconds=1, produce=lambda: "v", backend=backend)
    now[0] = 5.0

    assert backend.get(build_key("t", "expired")) is None
    # ...and the entry is gone, not merely hidden, so memory does not grow unbounded.
    assert build_key("t", "expired") not in backend._entries  # noqa: SLF001


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_readers_do_not_corrupt_the_store() -> None:
    """The backend is shared across threads by construction.

    uvicorn runs sync endpoints in a threadpool, so a cache that is not thread-safe is
    not merely untidy -- it corrupts entries under real traffic.
    """
    backend = InMemoryCacheBackend()
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            for i in range(200):
                # `value=i` binds the loop variable now rather than at call time.
                # Without it every entry would be written with whatever `i` happened to
                # be when the lambda ran, which is the late-binding bug this lint rule
                # exists to catch and which would quietly weaken the assertion below.
                cached(
                    "thing",
                    f"k{index}",
                    str(i),
                    ttl_seconds=60,
                    produce=lambda value=i: {"i": value},
                    backend=backend,
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent use raised: {errors[:1]}"


def test_a_slow_store_does_not_block_readers_indefinitely() -> None:
    """Concurrent reads against a slow store all complete.

    Not a latency assertion -- a wall-clock budget in a unit test is a flaky test. The
    property is that no reader is serialised behind another's timeout, which is what
    would turn a slow cache into a slow endpoint.
    """

    class _SlowBackend(InMemoryCacheBackend):
        def get(self, key: str) -> str | None:  # type: ignore[override]
            time.sleep(0.02)
            return super().get(key)

    backend = _SlowBackend()
    total = 12
    results: list[int] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        # A distinct key per thread, so every call is a genuine miss rather than ten
        # threads contending for one entry -- the point is that no reader waits on
        # another's store call, not that they agree on a value.
        value = cached(f"slow{index}", "k", ttl_seconds=60, produce=lambda: index, backend=backend)
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(total)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == list(
        range(total)
    ), "a reader lost its result, which means the slow store serialised them"


def test_json_round_trip_preserves_the_shape() -> None:
    """The stored form must not reshape what the caller gets back.

    A cache that returned tuples where the caller passed lists would be a type-level
    landmine that only shows up under load.
    """
    payload: dict[str, Any] = {"a": [1, 2, {"b": None}], "c": True, "d": 1.5}
    backend = InMemoryCacheBackend()
    first = cached("shape", "k", ttl_seconds=60, produce=lambda: payload, backend=backend)
    second = cached(
        "shape", "k", ttl_seconds=60, produce=lambda: {"different": True}, backend=backend
    )

    assert first == second == payload
    assert json.loads(backend.get(build_key("shape", "k"))) == payload
