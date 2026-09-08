# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/data-platform-manager next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/data-platform-manager/pyproject.toml services/data-platform-manager/uv.lock services/data-platform-manager/
RUN uv sync --project services/data-platform-manager --frozen --no-cache --no-dev --no-install-project

COPY services/data-platform-manager services/data-platform-manager
RUN uv sync --project services/data-platform-manager --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/data-platform-manager/.venv services/data-platform-manager/.venv
COPY --from=builder /app/services/data-platform-manager/src   services/data-platform-manager/src
COPY --from=builder /app/services/data-platform-manager/pyproject.toml services/data-platform-manager/pyproject.toml
COPY --from=builder /app/services/data-platform-manager/settings.yaml  services/data-platform-manager/settings.yaml

ENV PATH="/app/services/data-platform-manager/.venv/bin:$PATH"
WORKDIR /app/services/data-platform-manager

CMD ["python", "-m", "data_platform_manager.app"]
