"""Standalone configuration for the dataset pipeline.

The pipeline is deliberately not allowed to import ``services`` or ``apps.api``
(see the "The pipeline is standalone and never imports domain services" import
contract in ``pyproject.toml``). A batch script that reaches into the request
path is how a pipeline ends up needing a database connection, a settings
singleton, and eventually a running API just to parse a ``.gz`` file.

So the pipeline reads the same three environment variables the application
exposes -- ``AGENTPAY_RAW_DIR``, ``AGENTPAY_OUT_DIR``, ``MAX_LINES_DEBUG`` --
directly, with the same defaults, and never imports the application's settings
model. The duplication is the point: it is three names, and it keeps the
boundary intact.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Immutable source directory. Read in place, never written to, never unpacked.
DEFAULT_RAW_DIR = REPO_ROOT.parent / "datasets"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "out"


@dataclass(frozen=True)
class PipelineConfig:
    """Resolved pipeline paths and the debug line cap.

    ``max_lines_debug`` is ``None`` for a full pass and a positive integer for a
    capped validation run (Requirement 1.15).
    """

    raw_dir: Path
    out_dir: Path
    max_lines_debug: int | None = None

    @property
    def candidates_db(self) -> Path:
        """Stage 1 output: one row per surviving candidate record."""
        return self.out_dir / "candidates.sqlite"

    @property
    def reviews_db(self) -> Path:
        """Stage 5 output. Declared here so the path lives in one place."""
        return self.out_dir / "reviews.sqlite"

    @property
    def catalog_dir(self) -> Path:
        """Stage 2-6 output directory."""
        return self.out_dir / "catalog"

    @property
    def products_jsonl(self) -> Path:
        """Stage 2 output: one selected product per line."""
        return self.catalog_dir / "products.jsonl"

    @property
    def raw_metadata_dir(self) -> Path:
        """Stage 2 output: the verbatim source record per selected product."""
        return self.catalog_dir / "raw_metadata"

    @property
    def images_manifest_jsonl(self) -> Path:
        """Stage 3 output: one image URL per line. No image is ever downloaded."""
        return self.catalog_dir / "images_manifest.jsonl"

    @property
    def offers_jsonl(self) -> Path:
        """Stage 4 output: deterministic synthetic offers for selected products."""
        return self.catalog_dir / "offers.jsonl"

    @property
    def quality_report_json(self) -> Path:
        """Stage 6 output: counts computed from the produced artifacts."""
        return self.catalog_dir / "quality_report.json"


def load_config(env: Mapping[str, str] | None = None) -> PipelineConfig:
    """Build a :class:`PipelineConfig` from an environment mapping.

    When ``env`` is omitted the process environment is used, layered over the
    repository ``.env`` file so that ``make catalog`` and a bare
    ``python -m pipeline.build_catalog`` behave the same as the API. When ``env``
    is supplied the ``.env`` file is not consulted at all, which is what keeps
    tests hermetic on a machine that has a populated ``.env``.
    """
    if env is None:
        env = {**read_env_file(REPO_ROOT / ".env"), **os.environ}

    return PipelineConfig(
        raw_dir=_resolve_dir(env.get("AGENTPAY_RAW_DIR"), DEFAULT_RAW_DIR),
        out_dir=_resolve_dir(env.get("AGENTPAY_OUT_DIR"), DEFAULT_OUT_DIR),
        max_lines_debug=_resolve_max_lines(env.get("MAX_LINES_DEBUG")),
    )


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines from a dotenv file, tolerating its absence.

    Intentionally minimal: no interpolation, no multi-line values, no export
    keyword. The pipeline only needs three scalar settings, and a real dotenv
    dependency for that would be a dependency to audit for no gain.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in {"AGENTPAY_RAW_DIR", "AGENTPAY_OUT_DIR", "MAX_LINES_DEBUG"}:
            continue
        values[key] = _strip_inline_comment(value.strip())
    return values


def _strip_inline_comment(value: str) -> str:
    if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
        return value[1:-1]
    head, sep, _ = value.partition(" #")
    return head.strip() if sep else value


def _resolve_dir(value: str | None, default: Path) -> Path:
    """Resolve a configured directory, treating relative paths as repo-relative."""
    if value is None or not value.strip():
        return default
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return Path(os.path.normpath(candidate))


def _resolve_max_lines(value: str | None) -> int | None:
    """An empty cap means "read every line", matching the application setting."""
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"MAX_LINES_DEBUG must be an integer or empty, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"MAX_LINES_DEBUG must be positive or empty, got {value!r}")
    return parsed
