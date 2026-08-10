# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/assistant next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/assistant/pyproject.toml services/assistant/uv.lock services/assistant/
RUN uv sync --project services/assistant --frozen --no-cache --no-dev --no-install-project

COPY services/assistant services/assistant
RUN uv sync --project services/assistant --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

COPY --from=builder --chown=app:app /app/services/assistant/.venv services/assistant/.venv
COPY --from=builder --chown=app:app /app/services/assistant/src   services/assistant/src
COPY --from=builder --chown=app:app /app/services/assistant/pyproject.toml services/assistant/pyproject.toml
COPY --from=builder --chown=app:app /app/services/assistant/settings.yaml  services/assistant/settings.yaml

ENV PATH="/app/services/assistant/.venv/bin:$PATH"
WORKDIR /app/services/assistant

USER app

CMD ["python", "-m", "assistant.app"]
