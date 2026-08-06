# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/prediction next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/prediction/pyproject.toml services/prediction/uv.lock services/prediction/
RUN uv sync --project services/prediction --frozen --no-cache --no-dev --no-install-project

COPY services/prediction services/prediction
RUN uv sync --project services/prediction --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# libgomp1: OpenMP runtime lightgbm's compiled extension dlopens at import time.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/services/prediction/.venv services/prediction/.venv
COPY --from=builder /app/services/prediction/src   services/prediction/src
COPY --from=builder /app/services/prediction/pyproject.toml services/prediction/pyproject.toml
COPY --from=builder /app/services/prediction/settings.yaml  services/prediction/settings.yaml

ENV PATH="/app/services/prediction/.venv/bin:$PATH"
WORKDIR /app/services/prediction

CMD ["python", "-m", "prediction.app"]
