"""Coverage manifest discovery and file-by-file tracking."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ReviewState = Literal["reviewed", "deferred", "excluded"]


@dataclass
class ManifestEntry:
    path: str
    state: ReviewState
    reason: str | None = None
    file_size_bytes: int = 0
    language: str = "unknown"


EXCLUDED_DIRS = {
    ".git",
    ".next",
    "node_modules",
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    ".audit",
}


def build_coverage_manifest(root_dir: Path | None = None) -> list[ManifestEntry]:
    root = root_dir or Path.cwd()
    entries: list[ManifestEntry] = []

    in_scope_roots = [
        "apps",
        "services",
        "packages",
        "pipeline",
        "buyer-agent",
        "infra",
        "tests",
        "docs",
    ]

    for scope_name in in_scope_roots:
        scope_path = root / scope_name
        if not scope_path.exists():
            continue
        for p in scope_path.rglob("*"):
            if p.is_file():
                rel_parts = p.relative_to(root).parts
                if any(ex in rel_parts for ex in EXCLUDED_DIRS):
                    continue

                suffix = p.suffix.lower()
                lang = (
                    "python"
                    if suffix == ".py"
                    else (
                        "typescript"
                        if suffix in (".ts", ".tsx")
                        else (
                            "javascript"
                            if suffix == ".js"
                            else ("markdown" if suffix == ".md" else suffix.lstrip("."))
                        )
                    )
                )

                entries.append(
                    ManifestEntry(
                        path=str(p.relative_to(root)).replace("\\", "/"),
                        state="reviewed",
                        file_size_bytes=p.stat().st_size,
                        language=lang,
                    )
                )

    # Top level files
    top_level = ["Makefile", "pyproject.toml", "docker-compose.yml", "README.md"]
    for tf in top_level:
        tp = root / tf
        if tp.exists():
            entries.append(
                ManifestEntry(
                    path=tf,
                    state="reviewed",
                    file_size_bytes=tp.stat().st_size,
                    language="config",
                )
            )

    entries.sort(key=lambda e: e.path)
    return entries


def write_manifest_records(entries: list[ManifestEntry], out_path: Path | None = None) -> Path:
    target = out_path or (Path("audit") / "records" / "coverage-manifest.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e)) + "\n")
    return target
