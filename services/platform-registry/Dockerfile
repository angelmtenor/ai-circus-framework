# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/platform-registry next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/platform-registry/pyproject.toml services/platform-registry/uv.lock services/platform-registry/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/platform-registry --frozen --no-dev --no-install-project

COPY services/platform-registry services/platform-registry
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/platform-registry --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

COPY --from=builder --chown=app:app /app/services/platform-registry/.venv services/platform-registry/.venv
COPY --from=builder --chown=app:app /app/services/platform-registry/src   services/platform-registry/src
COPY --from=builder --chown=app:app /app/services/platform-registry/pyproject.toml services/platform-registry/pyproject.toml
COPY --from=builder --chown=app:app /app/services/platform-registry/settings.yaml  services/platform-registry/settings.yaml

ENV PATH="/app/services/platform-registry/.venv/bin:$PATH"
WORKDIR /app/services/platform-registry

USER app

CMD ["python", "-m", "platform_registry.app"]
