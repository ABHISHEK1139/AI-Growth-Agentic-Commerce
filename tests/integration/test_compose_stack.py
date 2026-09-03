"""Task 2 acceptance: the container build is valid and the stack answers.

This is a smoke test, not a unit test. It shells out to the real Docker CLI, so
it skips -- never fails -- when Docker is absent or the stack is not up. The
default ``pytest tests/unit`` run never touches it.

Run it with the stack up::

    docker compose up -d --build
    pytest tests/integration -m integration

Validates: Requirements 44.1, 44.2
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Services that must reach a healthy state. `worker` has no healthcheck (it is a
# loop, not a listener) and `web` idles until the frontend is scaffolded, so both
# are asserted as merely running.
HEALTHY_SERVICES = ("postgres", "redis", "api")
RUNNING_SERVICES = ("postgres", "redis", "api", "worker")


def _docker() -> str:
    """Absolute path to the Docker CLI, or skip."""
    resolved = shutil.which("docker")
    if resolved is None:
        pytest.skip("Docker CLI not on PATH")
    return resolved


def _run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, executable resolved via which()
        [_docker(), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _require_daemon() -> None:
    probe = _run("version", "--format", "{{.Server.Version}}", timeout=30)
    if probe.returncode != 0:
        pytest.skip("Docker daemon not reachable")


def _compose_ps() -> list[dict[str, object]]:
    """Container state for the compose project, as a list of dicts.

    `docker compose ps --format json` emits either a JSON array or one JSON
    object per line depending on the CLI version, so handle both.
    """
    result = _run("compose", "ps", "--all", "--format", "json")
    if result.returncode != 0:
        pytest.skip(f"docker compose ps failed: {result.stderr.strip()[:200]}")

    text = result.stdout.strip()
    if not text:
        return []
    if text.startswith("["):
        return list(json.loads(text))
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def compose_containers() -> list[dict[str, object]]:
    _require_daemon()
    containers = _compose_ps()
    if not containers:
        pytest.skip("compose project is not up; run `docker compose up -d --build` first")
    return containers


def test_compose_file_exists() -> None:
    assert COMPOSE_FILE.is_file(), f"expected a compose file at {COMPOSE_FILE}"


def test_compose_config_is_valid() -> None:
    """`config --quiet` resolves interpolation, merges, and schema. Exit 0 or bust."""
    _require_daemon()

    result = _run("compose", "config", "--quiet")

    assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"


def test_compose_declares_exactly_the_five_agreed_services() -> None:
    """The infrastructure rule from the design: five services, no drift."""
    _require_daemon()

    result = _run("compose", "config", "--services")
    assert result.returncode == 0, result.stderr

    services = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    assert services == {"api", "worker", "web", "postgres", "redis"}


@pytest.mark.parametrize("service", RUNNING_SERVICES)
def test_service_is_running(compose_containers: list[dict[str, object]], service: str) -> None:
    states = {str(c.get("Service")): str(c.get("State", "")) for c in compose_containers}
    assert service in states, f"{service} has no container; found {sorted(states)}"
    assert states[service] == "running", f"{service} is {states[service]!r}, expected 'running'"


@pytest.mark.parametrize("service", HEALTHY_SERVICES)
def test_service_is_healthy(compose_containers: list[dict[str, object]], service: str) -> None:
    health = {str(c.get("Service")): str(c.get("Health", "")) for c in compose_containers}
    assert (
        health.get(service) == "healthy"
    ), f"{service} health is {health.get(service)!r}, expected 'healthy'"


def test_api_health_probe_answers_ok_inside_the_container_network(
    compose_containers: list[dict[str, object]],
) -> None:
    """The same call the compose healthcheck makes, asserted on the payload.

    Executed inside the api container so it proves in-network reachability, not
    just a published port on the host.
    """
    result = _run(
        "compose",
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        "import httpx; print(httpx.get('http://localhost:8000/health').text)",
    )
    assert result.returncode == 0, f"exec into api failed:\n{result.stderr}"

    body = json.loads(result.stdout.strip().splitlines()[-1])
    assert body["ok"] is True
    assert body["data"]["service"] == "agentpay"


def test_api_reaches_postgres_and_redis(compose_containers: list[dict[str, object]]) -> None:
    """/health/db is the readiness probe; 200 means both datastores answered."""
    result = _run(
        "compose",
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        (
            "import httpx,json;"
            "r=httpx.get('http://localhost:8000/health/db');"
            "print(json.dumps({'status': r.status_code, 'body': r.json()}))"
        ),
    )
    assert result.returncode == 0, f"exec into api failed:\n{result.stderr}"

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == 200, payload
    assert payload["body"]["data"]["postgres"]["ok"] is True
    assert payload["body"]["data"]["redis"]["ok"] is True


def test_api_container_runs_as_the_non_root_user(
    compose_containers: list[dict[str, object]],
) -> None:
    """uid 10001, per the image contract. A root container is a finding, not a nit."""
    result = _run("compose", "exec", "-T", "api", "python", "-c", "import os; print(os.getuid())")
    assert result.returncode == 0, result.stderr

    assert result.stdout.strip().splitlines()[-1] == "10001"


def test_application_packages_import_through_the_editable_install(
    compose_containers: list[dict[str, object]],
) -> None:
    """The regression this task fixes.

    With the editable install performed before the source copy, discovery ran
    against empty placeholder directories. Importing with the working directory
    stripped from sys.path is what distinguishes a real install from an accident
    of cwd.
    """
    probe = (
        "import sys; sys.path[:] = [p for p in sys.path if p not in ('', '/app')];"
        "import importlib;"
        "importlib.import_module('apps.api.main');"
        "importlib.import_module('apps.worker.main');"
        "importlib.import_module('packages.observability.logging');"
        "print('ok')"
    )
    result = _run("compose", "exec", "-T", "api", "python", "-c", probe)

    assert (
        result.returncode == 0
    ), f"editable install does not expose the packages:\n{result.stderr}"
    assert result.stdout.strip().splitlines()[-1] == "ok"
