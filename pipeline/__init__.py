"""Pipeline package exports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pipeline.build_catalog import STAGE_ORDER, build_parser
    from pipeline.build_catalog import main as build_catalog_main
    from pipeline.sample_data import generate_sample_data
    from pipeline.sample_data import main as sample_data_main

__all__ = [
    "STAGE_ORDER",
    "build_catalog_main",
    "build_parser",
    "generate_sample_data",
    "sample_data_main",
]


def __getattr__(name: str) -> Any:
    if name in {"STAGE_ORDER", "build_parser", "build_catalog_main"}:
        from pipeline import build_catalog

        if name == "build_catalog_main":
            return build_catalog.main
        return getattr(build_catalog, name)
    if name in {"generate_sample_data", "sample_data_main"}:
        from pipeline import sample_data

        if name == "sample_data_main":
            return sample_data.main
        return getattr(sample_data, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
