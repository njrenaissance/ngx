# syntax=docker/dockerfile:1.7

# ─── Builder ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Terraform CLI — pinned + checksum-verified. The worker invokes terraform
# at runtime (E.3); the api image carries it too for symmetry rather than
# splitting images and dealing with two build/push paths in CI.
# Checksum from https://releases.hashicorp.com/terraform/1.10.0/terraform_1.10.0_SHA256SUMS
ARG TERRAFORM_VERSION=1.10.0
ARG TERRAFORM_SHA256=4b05f4848d365597cf7ac5b59334c62a16b3bb7b524586578ee45ba823b6758b
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unzip \
 && curl -fsSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
 && echo "${TERRAFORM_SHA256}  /tmp/tf.zip" | sha256sum -c - \
 && unzip /tmp/tf.zip -d /usr/local/bin/ \
 && rm /tmp/tf.zip \
 && terraform version \
 && apt-get purge -y curl unzip \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

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

# Pinned terraform CLI from the builder. Owned by root with mode 755 so
# unprivileged forge user can exec it but not modify it.
COPY --from=builder /usr/local/bin/terraform /usr/local/bin/terraform

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER forge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('FORGE_PORT', '8000') + '/livez').read()" || exit 1

CMD ["python", "-m", "forge"]
