# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/platform-registry next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/platform-registry/pyproject.toml services/platform-registry/uv.lock services/platform-registry/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/platform-registry --frozen --no-dev --no-install-project

COPY services/platform-registry services/platform-registry
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/platform-registry --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# tesseract-ocr + poppler-utils back document_extraction.py's OCR fallback for
# scanned PDF pages / photographed images (pytesseract/pdf2image call out to these
# binaries) — small apt packages, no ML model weights, unlike a layout-aware OCR
# stack such as docling.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

COPY --from=builder /app/services/platform-registry/.venv services/platform-registry/.venv
COPY --from=builder /app/services/platform-registry/src   services/platform-registry/src
COPY --from=builder /app/services/platform-registry/pyproject.toml services/platform-registry/pyproject.toml
COPY --from=builder /app/services/platform-registry/settings.yaml  services/platform-registry/settings.yaml

ENV PATH="/app/services/platform-registry/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/platform-registry

USER app

CMD ["python", "-m", "platform_registry.app"]
