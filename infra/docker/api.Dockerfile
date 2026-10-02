FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build tooling lives only in this stage. Some dependencies ship sdists that
# need a compiler, but nothing at runtime does, so the final image never gets
# gcc/make/the Python headers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Source is copied before the install, deliberately.
#
# setuptools runs package discovery at install time. The previous ordering
# installed dependencies first against `mkdir -p` placeholders, to keep the
# dependency layer cached. Those empty directories have no __init__.py, so
# discovery recorded them as namespace packages and froze a mapping built from
# a layout that does not exist. It resolved only by accident, because the
# top-level mapping happened to point at /app, and it would go stale the moment
# a package moved or a new top-level package appeared -- a failure that shows up
# only inside the image, never on the host.
#
# The cost is that editing any file invalidates the dependency layer. That is
# acceptable: apps/, packages/, services/ and pipeline/ are bind-mounted for
# development, so rebuilds are rare.
COPY . .

# Runtime dependencies only. Installing `.[dev]` shipped pytest, ruff, mypy,
# hypothesis and import-linter into the production image: test tooling
# reachable from a service that handles payments.
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install -e .

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# `curl` is here for operator use against /health, not for the healthcheck --
# that one runs through httpx, which is already a runtime dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# The editable install above resolves the application packages from `/app`.
# Keeping only `/install` in the final stage made the api image depend on the
# compose development bind mounts, while the one-shot `migrate` service has no
# such mounts and therefore could not even find `infra/migrations/alembic.ini`.
# Ship the runtime source and migration files with the image so each service is
# self-contained in production as well as under compose.
COPY --from=builder /app/apps /app/apps
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services /app/services
COPY --from=builder /app/pipeline /app/pipeline
COPY --from=builder /app/infra /app/infra
# The committed demo catalog. `services.offers.seed` resolves it relative to the
# repository root, so without it `python -m apps.worker.seed_catalog` exits 2
# with SEED_ARTIFACT_MISSING and a fresh stack has an empty catalog — the
# seeder is a documented deployment step, so the artifacts have to be in the
# image that runs it. Small (two JSONL files) and the only `data/` shipped;
# `data/raw` and `data/out` stay out, they are build and runtime directories.
COPY --from=builder /app/data/seed /app/data/seed

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/health').status_code==200 else 1)"

# No --reload: the file watcher restarts the process on any bind-mounted file
# change, dropping in-flight payment and checkout requests. Local development
# keeps --reload via `make dev`; the image runs the steady state.
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
