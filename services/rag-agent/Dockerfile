# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/rag-agent next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/rag-agent/pyproject.toml services/rag-agent/uv.lock services/rag-agent/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/rag-agent --frozen --no-dev --no-install-project

COPY services/rag-agent services/rag-agent
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/rag-agent --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

COPY --from=builder /app/services/rag-agent/.venv services/rag-agent/.venv
COPY --from=builder /app/services/rag-agent/src   services/rag-agent/src
COPY --from=builder /app/services/rag-agent/pyproject.toml services/rag-agent/pyproject.toml
COPY --from=builder /app/services/rag-agent/settings.yaml  services/rag-agent/settings.yaml

ENV PATH="/app/services/rag-agent/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/rag-agent

USER app

CMD ["python", "-m", "rag_agent.app"]
