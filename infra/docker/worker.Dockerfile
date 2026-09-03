FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Source before the editable install, for the same reason as api.Dockerfile:
# setuptools package discovery runs at install time and must see the real
# package directories, not `mkdir -p` placeholders.
COPY . .

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[dev]"

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "apps.worker.main"]
