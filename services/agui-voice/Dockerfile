# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/agui-voice next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/agui-voice/pyproject.toml services/agui-voice/uv.lock services/agui-voice/
RUN uv sync --project services/agui-voice --frozen --no-cache --no-dev --no-install-project

COPY services/agui-voice services/agui-voice
RUN uv sync --project services/agui-voice --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/agui-voice/.venv services/agui-voice/.venv
COPY --from=builder /app/services/agui-voice/src   services/agui-voice/src
COPY --from=builder /app/services/agui-voice/pyproject.toml services/agui-voice/pyproject.toml
COPY --from=builder /app/services/agui-voice/settings.yaml  services/agui-voice/settings.yaml

ENV PATH="/app/services/agui-voice/.venv/bin:$PATH"
WORKDIR /app/services/agui-voice

CMD ["python", "-m", "agui_voice.app"]
