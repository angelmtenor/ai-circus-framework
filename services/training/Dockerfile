# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/training next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/training/pyproject.toml services/training/uv.lock services/training/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/training --frozen --no-dev --no-install-project

COPY services/training services/training
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/training --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

# libgomp1: OpenMP runtime lightgbm's compiled extension dlopens at import time.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/services/training/.venv services/training/.venv
COPY --from=builder /app/services/training/src   services/training/src
COPY --from=builder /app/services/training/pyproject.toml services/training/pyproject.toml
COPY --from=builder /app/services/training/settings.yaml  services/training/settings.yaml

ENV PATH="/app/services/training/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/training

USER app

CMD ["python", "-m", "training.app"]
