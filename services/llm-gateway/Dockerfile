# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/llm-gateway next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/llm-gateway/pyproject.toml services/llm-gateway/uv.lock services/llm-gateway/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/llm-gateway --frozen --no-dev --no-install-project

COPY services/llm-gateway services/llm-gateway
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/llm-gateway --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable — plus
# /models/hf, the local-embed model's Hugging Face cache, where compose/k8s mount a
# persistent volume (pre-created here so a fresh compose volume inherits app ownership).
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
    && install -d -o app -g app /models/hf

COPY --from=builder /app/services/llm-gateway/.venv services/llm-gateway/.venv
COPY --from=builder /app/services/llm-gateway/src   services/llm-gateway/src
COPY --from=builder /app/services/llm-gateway/pyproject.toml services/llm-gateway/pyproject.toml
COPY --from=builder /app/services/llm-gateway/settings.yaml  services/llm-gateway/settings.yaml
COPY --from=builder /app/services/llm-gateway/litellm_config.yaml services/llm-gateway/litellm_config.yaml

ENV PATH="/app/services/llm-gateway/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
# litellm otherwise calls load_dotenv() on import, reading any stray .env on its path.
ENV LITELLM_MODE=PRODUCTION
# The local-embed model's weights (~670 MB) — downloaded once into the mounted volume.
ENV HF_HOME=/models/hf
WORKDIR /app/services/llm-gateway

USER app

CMD ["python", "-m", "llm_gateway.app"]
