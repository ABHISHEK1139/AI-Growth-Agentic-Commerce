"""Every endpoint path the docs name must be a route the application serves.

A planning or integration document that tells a caller to
``POST /api/v1/catalog/import`` when the gateway serves
``POST /api/v1/merchant/catalog/imports`` is worse than no document: the caller
writes code against it, gets a 404, and reads that as a bug in their own
integration. Three documents had drifted that way, so the check is here rather
than in a reviewer's memory.

Both delivery layers are consulted: the FastAPI app under ``apps/api`` and the
Next.js route handlers under ``apps/web/src/app``, because the storefront
legitimately calls both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from apps.api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Files that describe the interface. ``roadmap_and_spec`` and the ADR set are
#: historical records and are deliberately not held to the current route table.
DOC_GLOBS: tuple[str, ...] = (
    "README.md",
    "ABOUT.md",
    "docs/frontend_design_guide.md",
    "docs/protocol-scope.md",
    "docs/architecture.md",
    "docs/state-machine.md",
    "docs/runbooks/*.md",
    "docs/adr/*.md",
    "buyer-agent/README.md",
)

#: A path-looking token in prose or a fenced block.
_PATH_RE = re.compile(
    r"(?<![\w`\-])((?:/api|/health|/\.well-known|/docs|/openapi\.json)"
    r"(?:/[A-Za-z0-9_\-.{}:$]*)*)"
)

_TEMPLATE_SEGMENT = re.compile(r"\{[^}]+\}")


def _fastapi_paths() -> set[str]:
    app = create_app()
    return {route.path for route in app.routes if isinstance(route, APIRoute)}


def _next_paths() -> set[str]:
    """Route handlers in ``apps/web/src/app``.

    ``page.tsx`` is excluded on purpose: a page is a URL a shopper visits, not
    an endpoint the frontend calls.
    """
    app_dir = REPO_ROOT / "apps" / "web" / "src" / "app"
    paths: set[str] = set()
    for route_file in app_dir.rglob("route.ts"):
        relative = route_file.relative_to(app_dir)
        parts = list(relative.parts[:-1])
        for part in parts:
            # `[...path]` is a catch-all: `/api/[...path]` serves everything
            # under /api, so it stands in for any /api path.
            if part.startswith("[..."):
                prefix = "/" + "/".join(parts[: parts.index(part)])
                paths.add(prefix + "/*")
                parts = parts[: parts.index(part)] + ["*"]
                break
            if part.startswith("[") and part.endswith("]"):
                parts[parts.index(part)] = "{param}"
        paths.add("/" + "/".join(parts))
    return paths


def _matches(path: str, registered: set[str]) -> bool:
    """Whether ``path`` resolves, treating ``{param}`` as any single segment."""
    if path in registered:
        return True
    for candidate in registered:
        cand_parts = candidate.strip("/").split("/")
        path_parts = path.strip("/").split("/")
        if candidate.endswith("/*"):
            # Catch-all: `/api/*` answers any path under /api.
            if path_parts[: len(cand_parts) - 1] == cand_parts[:-1]:
                return True
            continue
        if len(cand_parts) != len(path_parts):
            continue
        if all(
            _TEMPLATE_SEGMENT.fullmatch(c) or c == p
            for c, p in zip(cand_parts, path_parts, strict=False)
        ):
            return True
    return False


def _is_prefix_only(path: str, registered: set[str]) -> bool:
    """A path that is only a namespace (``/api/v1``) names no resource."""
    normalized = path.rstrip("/")
    return any(candidate.startswith(normalized + "/") for candidate in registered)


def _documented_paths(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    found: set[str] = set()
    for raw in _PATH_RE.findall(text):
        candidate = raw.rstrip(".,;:)]}`\"'")
        # Not a route reference: a namespace, a glob, or an incomplete mention.
        if candidate.endswith("/") or "*" in candidate or candidate in ("/api", "/docs"):
            continue
        found.add(candidate)
    return found


def _doc_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DOC_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return [f for f in files if f.is_file()]


@pytest.mark.parametrize("doc", _doc_files(), ids=lambda p: p.name)
def test_documented_endpoints_exist(doc: Path) -> None:
    registered = _fastapi_paths() | _next_paths()
    unknown = sorted(
        p
        for p in _documented_paths(doc)
        if not _matches(p, registered) and not _is_prefix_only(p, registered)
    )
    assert not unknown, (
        f"{doc.relative_to(REPO_ROOT)} names endpoints the app does not serve: "
        f"{unknown}. Either the route moved (update the doc) or the route is "
        f"missing (implement it)."
    )


def test_the_checker_can_actually_see_the_routes() -> None:
    """A doc check that passes because it found nothing is not a check."""
    fastapi = _fastapi_paths()
    assert "/api/v1/payments" in fastapi
    assert "/api/v1/orders/{order_id}" in fastapi
    assert "/.well-known/agent-commerce" in fastapi

    frontend = _next_paths()
    assert "/api/grok/chat" in frontend
    # The catch-all is normalised to `/api/*`: every path under /api that the
    # FastAPI app does not serve falls through to it, so a doc naming an
    # `/api/...` path is answered by *something*.
    assert "/api/*" in frontend

    assert _matches("/api/v1/orders/ord_123", fastapi)
    assert not _matches("/api/v1/nope", fastapi)
