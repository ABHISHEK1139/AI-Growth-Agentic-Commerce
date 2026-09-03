"""Liveness and readiness endpoints.

``/health`` answers whether the process is up and is deliberately dependency
free. ``/health/db`` answers whether the datastores are reachable and returns
503 when they are not, so an orchestrator can tell "starting" from "broken".

Neither endpoint reveals connection strings, credentials, or driver messages.
Both are exempt from rate limiting: a probe that trips a limiter reports the
service as unhealthy because of the control meant to protect it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from apps.api.auth import AppSettings
from apps.api.db import check_database
from apps.api.envelope import probe_payload
from packages.schemas.envelope import EnvelopeWarning

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def health(settings: AppSettings) -> dict[str, Any]:
    """Is the process serving requests?

    The provider names in the payload describe *this* application. Read from the
    process singleton they described the environment, so a probe could report the
    fakes as active while the running application had been built with real ones.
    """
    return probe_payload(
        ok=True,
        data={
            "service": settings.app_name,
            "env": settings.app_env,
            # Surfacing which providers are active makes it obvious in a demo
            # that the fakes are the default, and when they are not.
            "payment_provider": settings.payment_provider,
            "model_provider": settings.model_provider,
        },
    )


@router.get("/health/db", summary="Datastore readiness probe")
def health_db(response: Response, settings: AppSettings) -> dict[str, Any]:
    """Are PostgreSQL and Redis reachable?"""
    db_ok, db_error = check_database()
    redis_ok, redis_error = _check_redis(settings.redis_url)

    ok = db_ok and redis_ok
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    # Warnings name the component, never the DSN or the driver text.
    warnings = [
        EnvelopeWarning(code="DATASTORE_UNREACHABLE", message=f"{name} is not reachable.")
        for name, component_ok in (("postgres", db_ok), ("redis", redis_ok))
        if not component_ok
    ]

    return probe_payload(
        ok=ok,
        data={
            "postgres": {"ok": db_ok, "error": db_error},
            "redis": {"ok": redis_ok, "error": redis_error},
        },
        warnings=warnings,
    )


def _check_redis(redis_url: str) -> tuple[bool, str | None]:
    """Ping Redis. Reports the exception class only, never the URL.

    The URL is a parameter rather than a singleton lookup, so the probe checks the
    datastore this application was pointed at.
    """
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        client.close()
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not decide
        return False, type(exc).__name__
    return True, None
