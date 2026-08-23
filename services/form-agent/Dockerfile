# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/form-agent next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/form-agent/pyproject.toml services/form-agent/uv.lock services/form-agent/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/form-agent --frozen --no-dev --no-install-project

COPY services/form-agent services/form-agent
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/form-agent --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

COPY --from=builder --chown=app:app /app/services/form-agent/.venv services/form-agent/.venv
COPY --from=builder --chown=app:app /app/services/form-agent/src   services/form-agent/src
COPY --from=builder --chown=app:app /app/services/form-agent/pyproject.toml services/form-agent/pyproject.toml
COPY --from=builder --chown=app:app /app/services/form-agent/settings.yaml  services/form-agent/settings.yaml

ENV PATH="/app/services/form-agent/.venv/bin:$PATH"
WORKDIR /app/services/form-agent

USER app

CMD ["python", "-m", "form_agent.app"]
