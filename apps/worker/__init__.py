"""Worker application exports.

The re-exports are resolved lazily. Importing `apps.worker.seed_catalog` from
here meant that `python -m apps.worker.seed_catalog` loaded the module twice —
once as part of the package, once as `__main__` — which runpy warns about on
every invocation of a documented deployment step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "ScheduledJob",
    "Worker",
    "build_jobs",
    "seed_catalog_main",
]

if TYPE_CHECKING:
    from apps.worker.main import ScheduledJob, Worker, build_jobs
    from apps.worker.seed_catalog import main as seed_catalog_main


def __getattr__(name: str) -> Any:
    if name in ("ScheduledJob", "Worker", "build_jobs"):
        from apps.worker import main

        return getattr(main, name)
    if name == "seed_catalog_main":
        from apps.worker import seed_catalog

        return seed_catalog.main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
