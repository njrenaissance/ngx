# syntax=docker/dockerfile:1.7

# ─── Builder ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install production dependencies (no dev group)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Install the project itself
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Copy migration and seed artefacts needed at runtime.
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY db/ ./db/

# ─── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN groupadd --system forge && useradd --system --gid forge --create-home forge

WORKDIR /app

COPY --from=builder --chown=forge:forge /app/.venv /app/.venv
COPY --from=builder --chown=forge:forge /app/src /app/src
COPY --from=builder --chown=forge:forge /app/alembic /app/alembic
COPY --from=builder --chown=forge:forge /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=forge:forge /app/db /app/db

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER forge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('FORGE_PORT', '8000') + '/livez').read()" || exit 1

CMD ["python", "-m", "forge"]
