# syntax=docker/dockerfile:1

# ── Stage 1: dependency install ───────────────────────────────────────────────
FROM python:3.12-slim AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-editable --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --no-editable --no-dev

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin appuser

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=python-builder --chown=appuser:appuser /app/src /app/src

COPY ./alembic /app/alembic
COPY ./alembic.ini /app/alembic.ini

USER appuser

EXPOSE 8015

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8015/health')" \
    || exit 1

CMD ["uvicorn", "celine.grid.main:create_app", \
    "--host", "0.0.0.0", \
    "--port", "8015", \
    "--factory"]
