# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/rag-agent next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/rag-agent/pyproject.toml services/rag-agent/uv.lock services/rag-agent/
RUN uv sync --project services/rag-agent --frozen --no-cache --no-dev --no-install-project

COPY services/rag-agent services/rag-agent
RUN uv sync --project services/rag-agent --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/rag-agent/.venv services/rag-agent/.venv
COPY --from=builder /app/services/rag-agent/src   services/rag-agent/src
COPY --from=builder /app/services/rag-agent/pyproject.toml services/rag-agent/pyproject.toml
COPY --from=builder /app/services/rag-agent/settings.yaml  services/rag-agent/settings.yaml

ENV PATH="/app/services/rag-agent/.venv/bin:$PATH"
WORKDIR /app/services/rag-agent

CMD ["python", "-m", "rag_agent.app"]
