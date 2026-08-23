# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/training next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/training/pyproject.toml services/training/uv.lock services/training/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/training --frozen --no-dev --no-install-project

COPY services/training services/training
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/training --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

# libgomp1: OpenMP runtime lightgbm's compiled extension dlopens at import time.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=app:app /app/services/training/.venv services/training/.venv
COPY --from=builder --chown=app:app /app/services/training/src   services/training/src
COPY --from=builder --chown=app:app /app/services/training/pyproject.toml services/training/pyproject.toml
COPY --from=builder --chown=app:app /app/services/training/settings.yaml  services/training/settings.yaml

ENV PATH="/app/services/training/.venv/bin:$PATH"
WORKDIR /app/services/training

USER app

CMD ["python", "-m", "training.app"]
