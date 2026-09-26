# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/agui-voice next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/agui-voice/pyproject.toml services/agui-voice/uv.lock services/agui-voice/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/agui-voice --frozen --no-dev --no-install-project

COPY services/agui-voice services/agui-voice
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/agui-voice --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
    # faster-whisper (~/.cache/huggingface) and Piper (~/.cache/pipecat) download model
    # weights here; pre-created so a fresh volume mounted on it inherits app ownership.
    && install -d -o app -g app /home/app/.cache

COPY --from=builder /app/services/agui-voice/.venv services/agui-voice/.venv
COPY --from=builder /app/services/agui-voice/src   services/agui-voice/src
COPY --from=builder /app/services/agui-voice/pyproject.toml services/agui-voice/pyproject.toml
COPY --from=builder /app/services/agui-voice/settings.yaml  services/agui-voice/settings.yaml

ENV PATH="/app/services/agui-voice/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/agui-voice

USER app

CMD ["python", "-m", "agui_voice.app"]
