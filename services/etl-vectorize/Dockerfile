# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/etl-vectorize next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/etl-vectorize/pyproject.toml services/etl-vectorize/uv.lock services/etl-vectorize/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/etl-vectorize --frozen --no-dev --no-install-project

COPY services/etl-vectorize services/etl-vectorize
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/etl-vectorize --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

COPY --from=builder /app/services/etl-vectorize/.venv services/etl-vectorize/.venv
COPY --from=builder /app/services/etl-vectorize/src   services/etl-vectorize/src
COPY --from=builder /app/services/etl-vectorize/pyproject.toml services/etl-vectorize/pyproject.toml
COPY --from=builder /app/services/etl-vectorize/settings.yaml  services/etl-vectorize/settings.yaml

ENV PATH="/app/services/etl-vectorize/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/etl-vectorize

USER app

CMD ["python", "-m", "etl_vectorize.app"]
