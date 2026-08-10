# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/ui-streamlit next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/ui-streamlit/pyproject.toml services/ui-streamlit/uv.lock services/ui-streamlit/
RUN uv sync --project services/ui-streamlit --frozen --no-cache --no-dev --no-install-project

COPY services/ui-streamlit services/ui-streamlit
RUN uv sync --project services/ui-streamlit --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

COPY --from=builder --chown=app:app /app/services/ui-streamlit/.venv services/ui-streamlit/.venv
COPY --from=builder --chown=app:app /app/services/ui-streamlit/src   services/ui-streamlit/src
COPY --from=builder --chown=app:app /app/services/ui-streamlit/pyproject.toml services/ui-streamlit/pyproject.toml
COPY --from=builder --chown=app:app /app/services/ui-streamlit/settings.yaml  services/ui-streamlit/settings.yaml

ENV PATH="/app/services/ui-streamlit/.venv/bin:$PATH"
WORKDIR /app/services/ui-streamlit

USER app

CMD ["python", "-m", "ui_streamlit.app"]
