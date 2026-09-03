FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build tooling needed for a few wheels; apt lists dropped in the same layer.
# `curl` is here for operator use against /health, not for the healthcheck --
# that one runs through httpx, which is already a runtime dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Source is copied BEFORE the editable install, deliberately.
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

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[dev]"

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
