# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

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
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/assistant/.venv services/assistant/.venv
COPY --from=builder /app/services/assistant/src   services/assistant/src
COPY --from=builder /app/services/assistant/pyproject.toml services/assistant/pyproject.toml
COPY --from=builder /app/services/assistant/settings.yaml  services/assistant/settings.yaml

ENV PATH="/app/services/assistant/.venv/bin:$PATH"
WORKDIR /app/services/assistant

CMD ["python", "-m", "assistant.app"]
