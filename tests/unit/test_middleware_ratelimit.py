"""Rate limiting, with no Redis anywhere in sight.

The counter store is injected through `app.state.rate_limit_backend`, so the whole
control is exercised in process: requests under the limit, the one that trips, the
retry hint, per-route rules, and the two degraded paths.

The behaviour that matters most is failing open. A rate limiter is a protective
control, not a dependency of the payment path — if the store is unreachable the
request must be allowed and the degradation logged. Taking checkout down because a
cache is unavailable would be a worse outage than the one being prevented, and a
protective control that is silently off is worse than not having one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.api.middleware.context import REQUEST_ID_HEADER, RequestContextMiddleware
from apps.api.middleware.errors import (
    UnhandledExceptionMiddleware,
    install_exception_handlers,
)
from apps.api.middleware.ratelimit import (
    DEFAULT_RULE,
    LIMIT_HEADER,
    REMAINING_HEADER,
    RETRY_AFTER_HEADER,
    InMemoryRateLimitBackend,
    RateLimitMiddleware,
    RateLimitRule,
    RateLimitUnavailableError,
    RedisRateLimitBackend,
    _bucket_key,
    actor_identity,
    build_backend,
    declare_route_limit,
    is_exempt,
    route_rules,
    rule_for,
)
from packages.observability.context import correlation_scope

PING = "/api/v1/ping"
OTHER = "/api/v1/other"

#: A timestamp aligned to a 60 second boundary, so window arithmetic in the
#: assertions below is readable rather than accidental.
WINDOW_START = 1_700_000_040.0


class _BrokenBackend:
    """A store that cannot answer. Stands in for an unreachable Redis."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RateLimitUnavailableError("ConnectionError")
        self.calls = 0

    def hit(self, key: str, window_seconds: int) -> int:
        self.calls += 1
        raise self.error


def _build_app(
    *,
    backend: object | None = None,
    limit: int = 2,
    window_seconds: int = 60,
    enabled: bool = True,
) -> FastAPI:
    """The production stack order (minus CORS) with an injected counter store."""
    app = FastAPI()
    install_exception_handlers(app)
    app.state.rate_limit_backend = backend

    @app.get(PING)
    def _ping() -> dict:
        return {"pong": True}

    @app.get(OTHER)
    def _other() -> dict:
        return {"other": True}

    @app.get("/health")
    def _health() -> dict:
        return {"ok": True}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=enabled,
        default_rule=RateLimitRule(limit=limit, window_seconds=window_seconds),
    )
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app


@pytest.fixture
def limited_client() -> Iterator[TestClient]:
    """Two requests per minute, counted in memory."""
    with TestClient(_build_app(backend=InMemoryRateLimitBackend(), limit=2)) as client:
        yield client


@pytest.fixture
def restore_route_rules() -> Iterator[None]:
    """`declare_route_limit` writes to a module-level table, so it is restored."""
    from apps.api.middleware import ratelimit

    original = dict(ratelimit._ROUTE_RULES)
    try:
        yield
    finally:
        ratelimit._ROUTE_RULES.clear()
        ratelimit._ROUTE_RULES.update(original)


class TestRateLimitRule:
    def test_a_rule_must_allow_at_least_one_request(self) -> None:
        """A limit of zero would be a closed door dressed up as a limit."""
        with pytest.raises(ValueError, match="at least one request"):
            RateLimitRule(limit=0)

    def test_a_window_must_be_at_least_a_second(self) -> None:
        with pytest.raises(ValueError, match="at least one second"):
            RateLimitRule(limit=10, window_seconds=0)

    def test_window_start_is_stable_within_a_window(self) -> None:
        rule = RateLimitRule(limit=10, window_seconds=60)

        assert rule.window_start(WINDOW_START) == rule.window_start(WINDOW_START + 59.9)
        assert rule.window_start(WINDOW_START) != rule.window_start(WINDOW_START + 60.0)

    @pytest.mark.parametrize(
        ("offset", "expected"),
        [
            (0.0, 60),  # exactly on a boundary: the whole window remains
            (30.0, 30),
            (59.5, 1),
            (59.999, 1),
        ],
    )
    def test_retry_after_is_whole_seconds_and_never_zero(
        self, offset: float, expected: int
    ) -> None:
        """A `Retry-After: 0` invites an immediate retry that trips again."""
        rule = RateLimitRule(limit=1, window_seconds=60)

        assert rule.retry_after_seconds(WINDOW_START + offset) == expected


class TestRuleResolution:
    def test_an_exact_route_wins(self) -> None:
        assert rule_for("POST", "/api/v1/payments").limit == 10

    def test_the_method_is_part_of_the_match(self) -> None:
        """A GET on a payments path is not the expensive operation."""
        assert rule_for("GET", "/api/v1/payments") == DEFAULT_RULE

    def test_the_method_is_matched_case_insensitively(self) -> None:
        assert rule_for("post", "/api/v1/payments").limit == 10

    def test_a_prefix_rule_covers_a_parameterised_path(self) -> None:
        """Middleware runs before route matching, so there is no path template to
        match against — a prefix is how a `{id}` route gets covered."""
        assert rule_for("GET", "/api/v1/catalog/off_12345").limit == 60

    def test_an_unlisted_route_gets_the_default(self) -> None:
        assert rule_for("GET", "/api/v1/something/new") == DEFAULT_RULE

    def test_the_caller_supplied_default_is_used(self) -> None:
        override = RateLimitRule(limit=7, window_seconds=30)

        assert rule_for("GET", "/api/v1/unlisted", default=override) == override

    def test_money_paths_are_tighter_than_catalog_paths(self) -> None:
        """The design's whole point: the endpoints that move money get the tight
        rates, the chatty read paths get the loose ones."""
        payments = rule_for("POST", "/api/v1/payments").limit
        token = rule_for("POST", "/api/v1/agent/auth/token").limit
        search = rule_for("POST", "/api/v1/catalog/search").limit

        assert payments < search
        assert token <= search
        assert payments <= rule_for("POST", "/api/v1/authorizations").limit

    def test_the_longest_matching_prefix_wins(self, restore_route_rules: None) -> None:
        declare_route_limit("GET", "/api/v1/deep/*", RateLimitRule(limit=5))
        declare_route_limit("GET", "/api/v1/deep/deeper/*", RateLimitRule(limit=99))

        assert rule_for("GET", "/api/v1/deep/deeper/x").limit == 99
        assert rule_for("GET", "/api/v1/deep/other").limit == 5

    def test_a_router_can_declare_its_own_limit(self, restore_route_rules: None) -> None:
        declare_route_limit("POST", "/api/v1/brand/new", RateLimitRule(limit=3, window_seconds=30))
        rule = rule_for("POST", "/api/v1/brand/new")

        assert (rule.limit, rule.window_seconds) == (3, 30)

    def test_the_published_view_is_a_copy(self) -> None:
        """Handing out the live table would let a caller edit a limit by accident."""
        snapshot = route_rules()
        snapshot["GET /api/v1/injected"] = RateLimitRule(limit=1)  # type: ignore[index]

        assert rule_for("GET", "/api/v1/injected") == DEFAULT_RULE


class TestExemptions:
    @pytest.mark.parametrize(
        "path", ["/health", "/health/db", "/docs", "/redoc", "/openapi.json", "/favicon.ico"]
    )
    def test_probes_and_docs_are_exempt(self, path: str) -> None:
        assert is_exempt(path)

    @pytest.mark.parametrize("path", ["/api/v1/payments", "/api/v1/catalog/search", "/"])
    def test_everything_else_is_counted(self, path: str) -> None:
        assert not is_exempt(path)

    def test_extra_prefixes_can_be_added(self) -> None:
        assert is_exempt("/metrics", extra_prefixes=("/metrics",))

    def test_health_is_not_rate_limited_even_at_a_limit_of_one(self) -> None:
        """An orchestrator probing every few seconds must not be told the service
        is unhealthy by the control meant to protect it."""
        with TestClient(_build_app(backend=InMemoryRateLimitBackend(), limit=1)) as client:
            statuses = [client.get("/health").status_code for _ in range(6)]

        assert statuses == [200] * 6

    def test_an_exempt_path_is_not_counted_against_a_limited_one(self) -> None:
        with TestClient(_build_app(backend=InMemoryRateLimitBackend(), limit=2)) as client:
            for _ in range(5):
                client.get("/health")

            assert client.get(PING).status_code == 200


class TestActorIdentity:
    def _request(self, *, client: tuple[str, int] | None, headers: list | None = None) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": PING,
                "headers": headers or [],
                "client": client,
            }
        )

    def test_an_authenticated_actor_is_counted_by_identity(self) -> None:
        """Otherwise one buyer behind a shared address could exhaust another's
        allowance."""
        with correlation_scope(actor_id="usr_1"):
            assert actor_identity(self._request(client=("1.2.3.4", 5))) == "actor:usr_1"

    def test_an_anonymous_caller_is_counted_by_address(self) -> None:
        assert actor_identity(self._request(client=("1.2.3.4", 5))) == "ip:1.2.3.4"

    def test_an_unknown_address_still_produces_a_key(self) -> None:
        """A missing client must not mean an uncounted request."""
        assert actor_identity(self._request(client=None)) == "ip:unknown"

    def test_a_forwarded_for_header_is_ignored(self) -> None:
        """It is caller-controlled. Trusting it would let anyone reset their own
        counter by inventing a header."""
        identity = actor_identity(
            self._request(
                client=("1.2.3.4", 5),
                headers=[(b"x-forwarded-for", b"9.9.9.9")],
            )
        )

        assert identity == "ip:1.2.3.4"


class TestInMemoryBackend:
    def test_counts_rise_per_key(self) -> None:
        backend = InMemoryRateLimitBackend()

        assert [backend.hit("k", 60) for _ in range(3)] == [1, 2, 3]

    def test_keys_are_independent(self) -> None:
        backend = InMemoryRateLimitBackend()
        backend.hit("a", 60)

        assert backend.hit("b", 60) == 1

    def test_unbounded_growth_is_prevented(self) -> None:
        """Keys embed their window, so old ones are dead weight. Dropping them
        only ever forgives requests, never denies one."""
        backend = InMemoryRateLimitBackend(max_keys=4)
        for index in range(10):
            backend.hit(f"k{index}", 60)

        assert backend.hit("k0", 60) == 1


class _FakePipeline:
    def __init__(self, count: int) -> None:
        self._count = count
        self.commands: list[str] = []

    def incr(self, key: str, amount: int) -> None:
        self.commands.append("incr")

    def expire(self, key: str, seconds: int) -> None:
        self.commands.append("expire")

    def execute(self) -> list:
        return [self._count, True]


class _FakeRedisClient:
    def __init__(self, count: int = 1) -> None:
        self.pipelines: list[_FakePipeline] = []
        self._count = count

    def pipeline(self) -> _FakePipeline:
        created = _FakePipeline(self._count)
        self.pipelines.append(created)
        return created

    def close(self) -> None:
        return None


class TestRedisBackend:
    def test_a_hit_increments_and_refreshes_the_expiry(self) -> None:
        """Both commands, one round trip. Refreshing the TTL is safe because the
        key already encodes its window."""
        fake = _FakeRedisClient(count=7)

        class _Backend(RedisRateLimitBackend):
            def _connect(self) -> object:
                return fake

        assert _Backend("redis://unused").hit("k", 60) == 7
        assert fake.pipelines[0].commands == ["incr", "expire"]

    def test_construction_opens_no_connection(self) -> None:
        """An unreachable Redis must not be able to stop the process starting."""
        backend = build_backend("redis://192.0.2.1:6379/0", timeout_seconds=0.01)

        assert isinstance(backend, RedisRateLimitBackend)
        assert backend.degraded is False

    def test_a_client_failure_becomes_the_unavailable_signal(self) -> None:
        """Callers fail open on one exception type, so every client failure has to
        arrive as that type."""

        class _Backend(RedisRateLimitBackend):
            def _connect(self) -> object:
                raise OSError("connection refused")

        with pytest.raises(RateLimitUnavailableError):
            _Backend("redis://unused").hit("k", 60)

    def test_a_failure_starts_a_cooldown_that_skips_the_socket(self) -> None:
        """Otherwise a Redis outage adds its connect timeout to every request on
        the payment path."""
        now = [1_000.0]

        class _Backend(RedisRateLimitBackend):
            attempts = 0

            def _connect(self) -> object:
                type(self).attempts += 1
                raise OSError("connection refused")

        backend = _Backend("redis://unused", cooldown_seconds=10.0, clock=lambda: now[0])

        with pytest.raises(RateLimitUnavailableError):
            backend.hit("k", 60)
        assert backend.degraded is True

        with pytest.raises(RateLimitUnavailableError, match="cooldown"):
            backend.hit("k", 60)
        assert _Backend.attempts == 1

    def test_the_cooldown_expires_and_the_store_is_retried(self) -> None:
        now = [1_000.0]

        class _Backend(RedisRateLimitBackend):
            attempts = 0

            def _connect(self) -> object:
                type(self).attempts += 1
                raise OSError("connection refused")

        backend = _Backend("redis://unused", cooldown_seconds=10.0, clock=lambda: now[0])
        with pytest.raises(RateLimitUnavailableError):
            backend.hit("k", 60)

        now[0] += 11.0
        assert backend.degraded is False
        with pytest.raises(RateLimitUnavailableError):
            backend.hit("k", 60)

        assert _Backend.attempts == 2


class TestLimitIsEnforced:
    def test_requests_under_the_limit_pass(self, limited_client: TestClient) -> None:
        assert [limited_client.get(PING).status_code for _ in range(2)] == [200, 200]

    def test_the_request_over_the_limit_is_refused(self, limited_client: TestClient) -> None:
        for _ in range(2):
            limited_client.get(PING)
        response = limited_client.get(PING)

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"

    def test_the_refusal_is_the_error_envelope(self, limited_client: TestClient) -> None:
        for _ in range(3):
            response = limited_client.get(PING)
        payload = response.json()

        assert set(payload) >= {"ok", "request_id", "error"}
        assert payload["ok"] is False
        assert payload["error"]["retryable"] is True
        assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]

    def test_a_retry_after_header_is_returned(self, limited_client: TestClient) -> None:
        for _ in range(3):
            response = limited_client.get(PING)

        assert int(response.headers[RETRY_AFTER_HEADER]) >= 1
        assert int(response.headers[RETRY_AFTER_HEADER]) <= 60

    def test_the_retry_hint_travels_in_the_body_as_well(self, limited_client: TestClient) -> None:
        """The header is for HTTP clients and proxies; the action is for a UI that
        renders recoveries generically (Requirement 31.10)."""
        for _ in range(3):
            response = limited_client.get(PING)
        payload = response.json()

        action = payload["next_actions"][0]
        assert action["action"] == "RETRY_AFTER"
        assert action["params"]["retry_after_seconds"] == int(response.headers[RETRY_AFTER_HEADER])
        assert payload["error"]["details"]["limit"] == 2
        assert payload["error"]["details"]["window_seconds"] == 60

    def test_the_limit_headers_describe_the_budget(self, limited_client: TestClient) -> None:
        first = limited_client.get(PING)
        second = limited_client.get(PING)
        third = limited_client.get(PING)

        assert (first.headers[LIMIT_HEADER], first.headers[REMAINING_HEADER]) == ("2", "1")
        assert second.headers[REMAINING_HEADER] == "0"
        assert third.headers[REMAINING_HEADER] == "0"

    def test_the_refusal_is_logged(self, limited_client: TestClient, logs) -> None:
        for _ in range(3):
            limited_client.get(PING)
        line = logs.with_event("RATE_LIMIT_EXCEEDED")[0]

        assert line["level"] == "WARNING"
        assert line["error_code"] == "RATE_LIMITED"
        assert line["path"] == PING
        assert line["limit"] == 2
        assert line["retry_after_seconds"] >= 1

    def test_the_access_log_reports_the_outcome(self, limited_client: TestClient, logs) -> None:
        for _ in range(3):
            limited_client.get(PING)
        line = logs.with_event("REQUEST_COMPLETED")[-1]

        assert line["status"] == 429
        assert line["outcome"] == "rate_limited"
        assert line["error_code"] == "RATE_LIMITED"

    def test_counters_are_per_route_not_global(self, limited_client: TestClient) -> None:
        """One shared counter would let a chatty search exhaust the checkout
        budget."""
        for _ in range(3):
            limited_client.get(PING)

        assert limited_client.get(PING).status_code == 429
        assert limited_client.get(OTHER).status_code == 200

    def test_a_declared_route_limit_overrides_the_default(self, restore_route_rules: None) -> None:
        declare_route_limit("GET", OTHER, RateLimitRule(limit=1, window_seconds=60))

        with TestClient(_build_app(backend=InMemoryRateLimitBackend(), limit=5)) as client:
            tight = [client.get(OTHER).status_code for _ in range(2)]
            loose = [client.get(PING).status_code for _ in range(2)]

        assert tight == [200, 429]
        assert loose == [200, 200]

    def test_counters_are_keyed_per_actor_route_and_window(self) -> None:
        """Two identities behind one address each get their own allowance, and a
        key cannot be shared across routes or windows."""
        rule = RateLimitRule(limit=2, window_seconds=60)
        base = _bucket_key(rule, "actor:usr_a", "GET", PING, WINDOW_START)

        assert base != _bucket_key(rule, "actor:usr_b", "GET", PING, WINDOW_START)
        assert base != _bucket_key(rule, "actor:usr_a", "GET", OTHER, WINDOW_START)
        assert base != _bucket_key(rule, "actor:usr_a", "POST", PING, WINDOW_START)
        assert base != _bucket_key(rule, "actor:usr_a", "GET", PING, WINDOW_START + 60.0)
        assert base == _bucket_key(rule, "actor:usr_a", "GET", PING, WINDOW_START + 59.0)

    def test_disabling_the_limiter_stops_all_counting(self) -> None:
        app = _build_app(backend=InMemoryRateLimitBackend(), limit=1, enabled=False)
        with TestClient(app) as client:
            responses = [client.get(PING) for _ in range(5)]

        assert [r.status_code for r in responses] == [200] * 5
        assert LIMIT_HEADER not in responses[-1].headers


class TestFailOpen:
    def test_a_store_failure_allows_the_request(self) -> None:
        """The one behaviour that must never regress: a limiter outage cannot take
        down the payment path."""
        backend = _BrokenBackend()
        with TestClient(_build_app(backend=backend, limit=1)) as client:
            statuses = [client.get(PING).status_code for _ in range(5)]

        assert statuses == [200] * 5
        assert backend.calls == 5

    def test_a_store_failure_is_logged_as_a_warning(self, logs) -> None:
        """An invisible protective control that is off is worse than none."""
        with TestClient(_build_app(backend=_BrokenBackend(), limit=1)) as client:
            client.get(PING)

        line = logs.with_event("RATE_LIMIT_DEGRADED")[0]
        assert line["level"] == "WARNING"
        assert line["outcome"] == "fail_open"
        assert line["reason"] == "RateLimitUnavailableError"
        assert line["path"] == PING

    def test_an_unexpected_backend_error_also_fails_open(self, logs) -> None:
        """Fail open on *any* failure, not only the signal we defined. A bug in a
        store client must not become a payment outage."""
        backend = _BrokenBackend(error=TimeoutError("socket timeout"))
        with TestClient(_build_app(backend=backend, limit=1)) as client:
            response = client.get(PING)

        assert response.status_code == 200
        assert logs.with_event("RATE_LIMIT_DEGRADED")[0]["reason"] == "TimeoutError"

    def test_a_store_failure_does_not_leak_into_the_response(self) -> None:
        with TestClient(_build_app(backend=_BrokenBackend(), limit=1)) as client:
            response = client.get(PING)

        assert "ConnectionError" not in response.text
        assert RETRY_AFTER_HEADER not in response.headers

    def test_no_configured_backend_fails_open_and_says_so(self, logs) -> None:
        with TestClient(_build_app(backend=None, limit=1)) as client:
            statuses = [client.get(PING).status_code for _ in range(3)]

        assert statuses == [200] * 3
        assert logs.with_event("RATE_LIMIT_DEGRADED")[0]["reason"] == "no_backend_configured"

    def test_a_degraded_response_still_carries_correlation_headers(self) -> None:
        with TestClient(_build_app(backend=_BrokenBackend(), limit=1)) as client:
            response = client.get(PING)

        assert response.headers[REQUEST_ID_HEADER]
