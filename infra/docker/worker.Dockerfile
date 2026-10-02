FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build tooling is confined to this stage; see api.Dockerfile.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Source before the install, for the same reason as api.Dockerfile: setuptools
# package discovery runs at install time and must see the real package
# directories, not `mkdir -p` placeholders.
COPY . .

# Runtime dependencies only; installing `.[dev]` would put the test suite in
# the image that runs the scheduled sweeps.
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install -e .

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY --from=builder /install /usr/local

# Editable installs resolve these packages from `/app`; copy their source into
# the runtime image rather than relying on compose-only bind mounts.
COPY --from=builder /app/apps /app/apps
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services /app/services
COPY --from=builder /app/pipeline /app/pipeline
COPY --from=builder /app/infra /app/infra
# The committed demo catalog, for the same reason as api.Dockerfile: this image
# runs `apps.worker.seed_catalog` and it resolves the artifacts from the
# repository root.
COPY --from=builder /app/data/seed /app/data/seed

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# The worker has no listening socket, so there is no probe to make: a
# healthcheck that cannot observe a hung job loop would only report false
# confidence. Liveness is the supervisor's job -- see `restart:` in
# docker-compose.yml.
CMD ["python", "-m", "apps.worker.main"]
