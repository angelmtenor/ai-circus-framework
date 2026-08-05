# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/llm-gateway next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/llm-gateway/pyproject.toml services/llm-gateway/uv.lock services/llm-gateway/
RUN uv sync --project services/llm-gateway --frozen --no-cache --no-dev --no-install-project

COPY services/llm-gateway services/llm-gateway
RUN uv sync --project services/llm-gateway --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/llm-gateway/.venv services/llm-gateway/.venv
COPY --from=builder /app/services/llm-gateway/src   services/llm-gateway/src
COPY --from=builder /app/services/llm-gateway/pyproject.toml services/llm-gateway/pyproject.toml
COPY --from=builder /app/services/llm-gateway/settings.yaml  services/llm-gateway/settings.yaml
COPY --from=builder /app/services/llm-gateway/litellm_config.yaml services/llm-gateway/litellm_config.yaml

ENV PATH="/app/services/llm-gateway/.venv/bin:$PATH"
WORKDIR /app/services/llm-gateway

CMD ["python", "-m", "llm_gateway.app"]
