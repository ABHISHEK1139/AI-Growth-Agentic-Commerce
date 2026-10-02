"""The webhook handlers must not block the asyncio event loop.

Background
----------
Both webhook endpoints are declared ``async def``, so FastAPI runs them *on the event
loop* rather than in its threadpool. Everything they do is synchronous: the HMAC
verify, a SHA-256 over the raw body, a ``SELECT`` to deduplicate the event, an
``INSERT``, and the payment state transitions with their commits.

On the loop, each of those stalls every other in-flight request for its duration. The
failure mode is not a slow webhook -- it is that one slow webhook stops the storefront,
the agent API, and the health probe from being served at all. Provider webhooks arrive
in bursts and are retried on failure, so bursts are the normal case, not the edge case.

Why these tests use httpx.ASGITransport rather than TestClient
--------------------------------------------------------------
An earlier draft of this file measured concurrency with ``TestClient`` plus
``client.stream()``. Those tests passed while the bug was still live, for a subtle
reason: Starlette's ``TestClient`` drives the ASGI app through a portal running in a
*separate thread*, and returns a lazy response object. Measuring wall-clock around
``client.get()`` from the test thread therefore timed the portal's queueing, not the
application's responsiveness -- the instrument recorded 0.002s while the app was
provably still blocked inside the stall.

``httpx.ASGITransport`` runs the app on the *calling* event loop, which is what
production does under uvicorn. That makes it the only harness here that can actually
observe loop blocking. Every concurrency assertion below therefore goes through it.

The assertions are on behaviour (an unrelated request stays fast while a webhook is
busy), never on source text. Asserting that a handler contains ``run_in_threadpool``
would keep passing if the work moved back onto the loop inside a helper.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from apps.api.db import get_db

# Long enough that a loop-blocked request cannot finish inside the budget by luck,
# short enough to keep the suite fast.
WEBHOOK_STALL_SECONDS = 0.6
# A loop that is not blocked serves /health in single-digit milliseconds. The budget
# is set well above that noise floor but far below the stall, so it discriminates.
UNRELATED_REQUEST_BUDGET_SECONDS = 0.3

FAKE_WEBHOOK = "/api/v1/webhooks/fake"
RAZORPAY_WEBHOOK = "/api/v1/payments/razorpay/webhook"

PAYLOAD: dict[str, Any] = {
    "event": "payment.captured",
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_loop_test",
                "amount": 1000,
                "currency": "INR",
            }
        }
    },
}


@pytest.fixture
def loop_probe() -> Iterator[dict[str, Any]]:
    """Replaces webhook processing with an occupation of a measurable interval.

    ``worker_thread`` is what makes the assertion precise: if the work ran on the event
    loop, it would be the loop thread, and that is the condition being caught.
    ``entered`` lets a test synchronise on the stall actually starting rather than
    sleeping and hoping.
    """
    from services.payments import webhooks as webhooks_module

    state: dict[str, Any] = {
        "worker_thread": None,
        "entered": asyncio.Event(),
        "released": asyncio.Event(),
    }
    original = webhooks_module.WebhookProcessor.process_webhook

    def blocking_process(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        state["worker_thread"] = threading.get_ident()
        state["entered"].set()
        # A real sleep, not an await: this models the blocking I/O the handler
        # performs. It occupies whichever thread it was called on, which is exactly
        # the distinction under test.
        time.sleep(WEBHOOK_STALL_SECONDS)
        return {
            "status": "processed",
            "duplicate": False,
            "payment_id": "pay_loop_test",
        }

    webhooks_module.WebhookProcessor.process_webhook = blocking_process  # type: ignore[method-assign]
    try:
        yield state
    finally:
        webhooks_module.WebhookProcessor.process_webhook = original  # type: ignore[method-assign]


@pytest.fixture
def webhook_app(app: FastAPI) -> Iterator[FastAPI]:
    """The real application with the database dependency stubbed.

    No real session is used: ``process_webhook`` is replaced wholesale, so nothing
    downstream queries. That keeps the test independent of PostgreSQL-only types
    (``provider_event.payload`` is JSONB and will not compile on SQLite) and isolates
    the property under test, which is dispatch rather than persistence.
    """
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


def _client(app: FastAPI) -> httpx.AsyncClient:
    """An async client whose ASGI transport runs the app on the caller's loop."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def _post_webhook(client: httpx.AsyncClient, path: str) -> httpx.Response:
    return await client.post(
        path,
        content=json.dumps(PAYLOAD),
        headers={
            "content-type": "application/json",
            "X-Razorpay-Signature": "test-signature",
        },
    )


async def _assert_webhook_leaves_loop_responsive(
    app: FastAPI, path: str, loop_probe: dict[str, Any]
) -> None:
    """Core assertion: a busy webhook must not delay an unrelated request."""
    async with _client(app) as client:
        webhook_task = asyncio.create_task(_post_webhook(client, path))

        # Wait for the handler to actually be inside its blocking work, rather than
        # sleeping a guessed interval. Without this the measurement can race ahead of
        # the request and prove nothing.
        await asyncio.wait_for(loop_probe["entered"].wait(), timeout=5)

        loop_thread = threading.get_ident()
        started = time.perf_counter()
        health = await client.get("/health")
        elapsed = time.perf_counter() - started

        webhook = await asyncio.wait_for(webhook_task, timeout=10)

    # The webhook itself must have succeeded; otherwise the latency above proves
    # nothing about a request that was rejected before doing any work.
    assert webhook.status_code == 200, webhook.text
    assert health.status_code == 200, health.text

    assert elapsed < UNRELATED_REQUEST_BUDGET_SECONDS, (
        f"/health took {elapsed:.3f}s while {path} was mid-processing (budget "
        f"{UNRELATED_REQUEST_BUDGET_SECONDS:.3f}s). The webhook is running on the "
        "event loop, so it is blocking all other traffic -- including the liveness "
        "probe, which would take the whole service out of rotation."
    )

    # The precise version of the same claim. Latency alone could be satisfied by luck
    # or by a generous budget; this cannot.
    assert (
        loop_probe["worker_thread"] is not None
    ), "The webhook never reached the patched processor, so nothing was exercised."
    assert loop_probe["worker_thread"] != loop_thread, (
        "Webhook processing ran on the event loop thread itself. Unrelated traffic was "
        "blocked for the full duration even where the latency budget happened to pass."
    )


@pytest.mark.parametrize("path", [FAKE_WEBHOOK, RAZORPAY_WEBHOOK])
def test_webhook_does_not_block_the_event_loop(
    webhook_app: FastAPI, loop_probe: dict[str, Any], path: str
) -> None:
    """Both webhook endpoints keep the loop free while they work.

    Both routes are covered by one test on purpose. The checkout router's webhook had
    the identical defect, independently, and is easy to overlook precisely because it
    lives in a different module from the shared one.
    """
    asyncio.run(_assert_webhook_leaves_loop_responsive(webhook_app, path, loop_probe))


def test_concurrent_webhooks_are_not_serialised(
    webhook_app: FastAPI, loop_probe: dict[str, Any]
) -> None:
    """Two simultaneous webhooks must take about as long as one.

    Providers deliver in bursts, so serialised handling makes tail latency grow
    linearly with burst size. This asserts the sublinear property.
    """

    async def run() -> float:
        async with _client(webhook_app) as client:
            started = time.perf_counter()
            responses = await asyncio.gather(
                _post_webhook(client, FAKE_WEBHOOK),
                _post_webhook(client, FAKE_WEBHOOK),
            )
            elapsed = time.perf_counter() - started

        for response in responses:
            assert response.status_code == 200, response.text
        return elapsed

    elapsed = asyncio.run(run())

    # Serial execution would be ~2x the stall. The ceiling is generous to tolerate
    # scheduler noise while still failing serialised handling.
    assert elapsed < WEBHOOK_STALL_SECONDS * 1.9, (
        f"Two concurrent webhooks took {elapsed:.3f}s against a single-stall "
        f"baseline of {WEBHOOK_STALL_SECONDS:.3f}s, which indicates they were "
        "processed one after the other."
    )


def test_unrelated_request_is_fast_when_no_webhook_is_in_flight(
    webhook_app: FastAPI,
) -> None:
    """Baseline: /health is fast and unobstructed when nothing else is happening.

    Without this, a regression that made *every* request slow -- a saturated thread
    pool, a broken middleware -- could hide behind the concurrency tests above, since
    those only compare against a budget.
    """

    async def run() -> float:
        async with _client(webhook_app) as client:
            started = time.perf_counter()
            response = await client.get("/health")
            elapsed = time.perf_counter() - started
        assert response.status_code == 200, response.text
        return elapsed

    elapsed = asyncio.run(run())
    assert elapsed < UNRELATED_REQUEST_BUDGET_SECONDS, (
        f"/health took {elapsed:.3f}s with no concurrent load, so the budget used by "
        "the other tests in this module is not meaningful."
    )


def test_stall_fixture_really_stalls(webhook_app: FastAPI, loop_probe: dict[str, Any]) -> None:
    """Sanity check on the harness itself.

    If a refactor stopped the fixture patching ``process_webhook``, every webhooks in
    this module would return instantly, nothing would ever block, and the concurrency
    assertions would pass vacuously.
    """

    async def run() -> float:
        async with _client(webhook_app) as client:
            started = time.perf_counter()
            response = await _post_webhook(client, FAKE_WEBHOOK)
            elapsed = time.perf_counter() - started
        assert response.status_code == 200, response.text
        return elapsed

    elapsed = asyncio.run(run())

    assert loop_probe["worker_thread"] is not None, (
        "loop_probe did not intercept WebhookProcessor.process_webhook; the "
        "concurrency tests in this module are not testing anything."
    )
    assert elapsed >= WEBHOOK_STALL_SECONDS * 0.9, (
        f"Webhook returned in {elapsed:.3f}s despite a {WEBHOOK_STALL_SECONDS:.3f}s "
        "stall; the fixture is not applying."
    )


def test_both_webhook_handlers_are_declared_async() -> None:
    """Both handlers stay ``async def``.

    A guard rail rather than the bug detector. Making a handler ``def`` would send the
    whole thing to the threadpool, which sounds correct but breaks the raw-body read:
    a synchronous endpoint cannot ``await request.body()``, and HMAC is only valid over
    the exact bytes the provider signed. The concurrency tests above are what detect a
    regression; this one keeps the reason attached to the code.
    """
    from apps.api.routers import payments, razorpay_checkout

    assert inspect.iscoroutinefunction(payments.handle_webhook)
    assert inspect.iscoroutinefunction(razorpay_checkout.razorpay_webhook)


@pytest.mark.parametrize(
    ("module_path", "func_name"),
    [
        ("apps.api.routers.payments", "handle_webhook"),
        ("apps.api.routers.razorpay_checkout", "razorpay_webhook"),
    ],
)
def test_webhook_reads_the_exact_bytes_that_were_signed(module_path: str, func_name: str) -> None:
    """The body is read with ``await request.body()``, never re-serialised.

    HMAC-SHA256 is valid only over the exact bytes the provider signed. Parsing into a
    model and re-serialising would change key order, whitespace, or unicode escaping,
    and every signature would fail.
    """
    source = inspect.getsource(getattr(importlib.import_module(module_path), func_name))
    assert "await request.body()" in source, (
        f"{func_name} must read the body with `await request.body()`. HMAC "
        "verification requires the exact bytes the provider signed."
    )


def test_webhook_processing_is_dispatched_off_the_loop() -> None:
    """The blocking call is handed to Starlette's threadpool in both handlers.

    This is the one source-level assertion in the module, and it is deliberately not
    the primary defence: it would still pass if the work were moved back onto the loop
    inside a helper function. It is here because it fails loudly and specifically if
    someone removes the dispatch while restructuring, and because it documents that
    the split between "read the body on the loop" and "work in a thread" is
    intentional rather than incidental.
    """
    from apps.api.routers import payments, razorpay_checkout

    for module, func_name in (
        (payments, "handle_webhook"),
        (razorpay_checkout, "razorpay_webhook"),
    ):
        source = inspect.getsource(getattr(module, func_name))
        assert "run_in_threadpool" in source, (
            f"{module.__name__}.{func_name} no longer dispatches its processing off "
            "the event loop."
        )
