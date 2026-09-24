# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/dl-inference next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/dl-inference/pyproject.toml services/dl-inference/uv.lock services/dl-inference/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/dl-inference --frozen --no-dev --no-install-project

COPY services/dl-inference services/dl-inference
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/dl-inference --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app
RUN useradd --create-home --shell /bin/bash app

COPY --from=builder --chown=app:app /app/services/dl-inference/.venv services/dl-inference/.venv
COPY --from=builder --chown=app:app /app/services/dl-inference/src   services/dl-inference/src
COPY --from=builder --chown=app:app /app/services/dl-inference/pyproject.toml services/dl-inference/pyproject.toml
COPY --from=builder --chown=app:app /app/services/dl-inference/settings.yaml  services/dl-inference/settings.yaml

ENV PATH="/app/services/dl-inference/.venv/bin:$PATH"
# Two glibc malloc arenas instead of one per thread: onnxruntime's intra-op threads plus
# uvicorn's worker threads otherwise fragment the heap and pin RSS near its peak (measured:
# ~1.0 GB -> ~0.6 GB steady state with both healthcare models loaded).
ENV MALLOC_ARENA_MAX=2
WORKDIR /app/services/dl-inference

USER app

CMD ["python", "-m", "dl_inference.app"]
