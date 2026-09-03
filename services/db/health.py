"""Database readiness and diagnostics."""

from __future__ import annotations

import time

from sqlalchemy import text

from services.db.engine import get_engine


def check_db_readiness() -> dict[str, object]:
    start = time.perf_counter()
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000.0
        return {
            "status": "healthy",
            "connected": True,
            "latency_ms": round(latency_ms, 2),
            "driver": engine.driver,
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return {
            "status": "unhealthy",
            "connected": False,
            "latency_ms": round(latency_ms, 2),
            "error": str(exc),
        }
