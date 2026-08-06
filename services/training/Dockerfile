# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/training next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/training/pyproject.toml services/training/uv.lock services/training/
RUN uv sync --project services/training --frozen --no-cache --no-dev --no-install-project

COPY services/training services/training
RUN uv sync --project services/training --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# libgomp1: OpenMP runtime lightgbm's compiled extension dlopens at import time.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/services/training/.venv services/training/.venv
COPY --from=builder /app/services/training/src   services/training/src
COPY --from=builder /app/services/training/pyproject.toml services/training/pyproject.toml
COPY --from=builder /app/services/training/settings.yaml  services/training/settings.yaml

ENV PATH="/app/services/training/.venv/bin:$PATH"
WORKDIR /app/services/training

CMD ["python", "-m", "training.app"]
