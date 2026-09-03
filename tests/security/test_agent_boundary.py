"""The agent layer has no path to the database (Requirement 23.1, Task 30).

`lint-imports` already enforces this in CI, but a contract in `pyproject.toml`
can be edited by the same commit that breaks it. These tests assert the guarantee
from inside the test suite, so weakening the linter configuration is not enough to
make the boundary quietly disappear.

Three independent angles, because they fail for different reasons:

* No callable in ``services.agent`` accepts or returns a unit of work. This catches
  the original defect directly, where a ``Session`` was threaded through the tool
  loop and on into offers, checkout, authorization, payments, and the audit writer.
* No source file in ``services.agent`` imports a database library.
* No module reachable from ``services.agent`` imports one either. That is the
  subtler breach: a helper imports a service that holds a session and hands one out.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
from collections import deque
from pathlib import Path
from typing import Any

import pytest

import services.agent

pytestmark = pytest.mark.security

REPO_ROOT = Path(services.agent.__file__).resolve().parents[2]
AGENT_PACKAGE_ROOT = Path(services.agent.__file__).parent

FIRST_PARTY_ROOTS = ("apps", "packages", "services", "pipeline")
DATABASE_ROOTS = frozenset({"sqlalchemy", "psycopg", "psycopg2"})

# Any of these in an annotation means a unit of work crossed the boundary,
# whatever the parameter happens to be named. Matched on word boundaries so
# ``ToolExecutionResult`` is not mistaken for ``Result``.
FORBIDDEN_ANNOTATION = re.compile(
    r"\b(Session|sessionmaker|scoped_session|Connection|Engine|Result|Row)\b"
)


def _agent_modules() -> list[str]:
    names = [services.agent.__name__]
    names.extend(
        module.name
        for module in pkgutil.walk_packages(
            services.agent.__path__, prefix=f"{services.agent.__name__}."
        )
    )
    return sorted(names)


def _agent_source_files() -> list[Path]:
    return sorted(AGENT_PACKAGE_ROOT.rglob("*.py"))


def _iter_callables(module: Any):
    """Every function and method defined *in* the module, not merely visible there."""
    for _, member in inspect.getmembers(module):
        if inspect.isfunction(member) and member.__module__ == module.__name__:
            yield member
        elif inspect.isclass(member) and member.__module__ == module.__name__:
            for _, method in inspect.getmembers(member, predicate=inspect.isfunction):
                if method.__module__ == module.__name__:
                    yield method


def _render(annotation: Any) -> str:
    return annotation if isinstance(annotation, str) else repr(annotation)


# ---------------------------------------------------------------------------
# First-party import graph, built from source rather than from runtime state.
# ---------------------------------------------------------------------------


def _module_name_for(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _first_party_files() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root in FIRST_PARTY_ROOTS:
        root_path = REPO_ROOT / root
        if not root_path.is_dir():
            continue
        for path in root_path.rglob("*.py"):
            if "node_modules" in path.parts or "apps/web" in path.as_posix():
                continue
            modules[_module_name_for(path)] = path
    return modules


def _direct_imports(path: Path, module_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    package = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            imported.add(prefix)
            # ``from pkg import submodule`` imports a module, not a name.
            imported.update(f"{prefix}.{alias.name}" for alias in node.names)
    return imported


def _resolve(candidate: str, known: dict[str, Path]) -> str | None:
    """Map an imported name onto a first-party module, trimming trailing symbols."""
    parts = candidate.split(".")
    while parts:
        name = ".".join(parts)
        if name in known:
            return name
        parts.pop()
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agent_modules_exist_to_be_checked() -> None:
    """Guard against the checks below passing because they found nothing."""
    modules = _agent_modules()
    assert "services.agent.loop" in modules
    assert len(modules) >= 5
    assert len(_agent_source_files()) >= 5


@pytest.mark.parametrize("module_name", _agent_modules())
def test_no_agent_callable_accepts_a_database_session(module_name: str) -> None:
    module = importlib.import_module(module_name)
    offenders: list[str] = []

    for func in _iter_callables(module):
        try:
            signature = inspect.signature(func)
        except (ValueError, TypeError):  # pragma: no cover - builtins have no signature
            continue
        for name, parameter in signature.parameters.items():
            rendered = _render(parameter.annotation)
            if FORBIDDEN_ANNOTATION.search(rendered):
                offenders.append(f"{module_name}.{func.__qualname__}({name}: {rendered})")

    assert offenders == [], (
        "The agent layer must never receive a unit of work; commerce reaches it "
        f"only through the facade port. Offending parameters: {offenders}"
    )


@pytest.mark.parametrize("module_name", _agent_modules())
def test_no_agent_callable_returns_a_database_type(module_name: str) -> None:
    module = importlib.import_module(module_name)
    offenders: list[str] = []

    for func in _iter_callables(module):
        try:
            signature = inspect.signature(func)
        except (ValueError, TypeError):  # pragma: no cover
            continue
        rendered = _render(signature.return_annotation)
        if FORBIDDEN_ANNOTATION.search(rendered):
            offenders.append(f"{module_name}.{func.__qualname__} -> {rendered}")

    assert offenders == [], f"Offending return annotations: {offenders}"


@pytest.mark.parametrize("source_file", _agent_source_files(), ids=lambda path: path.name)
def test_no_agent_source_file_imports_a_database_library(source_file: Path) -> None:
    imported = _direct_imports(source_file, _module_name_for(source_file))
    forbidden = {name for name in imported if name.split(".")[0] in DATABASE_ROOTS}
    assert forbidden == set(), f"{source_file.name} imports {sorted(forbidden)}"


def test_services_agent_reaches_no_database_library_through_any_import_chain() -> None:
    """The transitive contract, re-derived from source so editing the linter config is not enough."""
    known = _first_party_files()
    assert "services.agent.loop" in known, "the import graph failed to find the agent layer"

    start = [
        name for name in known if name == "services.agent" or name.startswith("services.agent.")
    ]
    assert start, "no agent modules discovered"

    queue: deque[tuple[str, tuple[str, ...]]] = deque((name, (name,)) for name in start)
    seen: set[str] = set(start)
    violations: list[str] = []

    while queue:
        module_name, chain = queue.popleft()
        for candidate in sorted(_direct_imports(known[module_name], module_name)):
            root = candidate.split(".")[0]
            if root in DATABASE_ROOTS:
                violations.append(" -> ".join([*chain, candidate]))
                continue
            if root not in FIRST_PARTY_ROOTS:
                continue
            resolved = _resolve(candidate, known)
            if resolved is None or resolved in seen:
                continue
            seen.add(resolved)
            queue.append((resolved, (*chain, resolved)))

    assert violations == [], (
        "services.agent reaches a database library through an import chain:\n"
        + "\n".join(sorted(violations))
    )
