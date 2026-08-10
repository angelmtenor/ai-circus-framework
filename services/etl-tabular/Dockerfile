# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/etl-tabular next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/etl-tabular/pyproject.toml services/etl-tabular/uv.lock services/etl-tabular/
RUN uv sync --project services/etl-tabular --frozen --no-cache --no-dev --no-install-project

COPY services/etl-tabular services/etl-tabular
RUN uv sync --project services/etl-tabular --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

COPY --from=builder --chown=app:app /app/services/etl-tabular/.venv services/etl-tabular/.venv
COPY --from=builder --chown=app:app /app/services/etl-tabular/src   services/etl-tabular/src
COPY --from=builder --chown=app:app /app/services/etl-tabular/pyproject.toml services/etl-tabular/pyproject.toml
COPY --from=builder --chown=app:app /app/services/etl-tabular/settings.yaml  services/etl-tabular/settings.yaml

ENV PATH="/app/services/etl-tabular/.venv/bin:$PATH"
WORKDIR /app/services/etl-tabular

USER app

CMD ["python", "-m", "etl_tabular.app"]
