# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/etl-vectorize next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/etl-vectorize/pyproject.toml services/etl-vectorize/uv.lock services/etl-vectorize/
RUN uv sync --project services/etl-vectorize --frozen --no-cache --no-dev --no-install-project

COPY services/etl-vectorize services/etl-vectorize
RUN uv sync --project services/etl-vectorize --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/etl-vectorize/.venv services/etl-vectorize/.venv
COPY --from=builder /app/services/etl-vectorize/src   services/etl-vectorize/src
COPY --from=builder /app/services/etl-vectorize/pyproject.toml services/etl-vectorize/pyproject.toml
COPY --from=builder /app/services/etl-vectorize/settings.yaml  services/etl-vectorize/settings.yaml

ENV PATH="/app/services/etl-vectorize/.venv/bin:$PATH"
WORKDIR /app/services/etl-vectorize

CMD ["python", "-m", "etl_vectorize.app"]
