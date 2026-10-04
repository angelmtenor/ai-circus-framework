# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use (seconds
# for litellm/langchain/sklearn-sized trees). `copy` because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/dl-training next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/dl-training/pyproject.toml services/dl-training/uv.lock services/dl-training/
# torch comes from exactly one uv dependency group (see pyproject.toml): "cpu" (default —
# ~1 GB smaller, what k3d/compose can actually use) or "gpu" (CUDA wheels, only useful on
# a cluster whose nodes expose nvidia.com/gpu — `make k3s-dl-build DL_TRAINING_TORCH=gpu`).
ARG DL_TRAINING_TORCH=cpu
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/dl-training --frozen --no-default-groups --group "$DL_TRAINING_TORCH" --no-install-project

COPY services/dl-training services/dl-training
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/dl-training --frozen --no-default-groups --group "$DL_TRAINING_TORCH"

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
# Pull the Debian security fixes published since the base image was built, and drop the
# base image's own pip: nothing installs at runtime, and its vendored urllib3/msgpack are
# what CI's Trivy scan (HIGH/CRITICAL, fixed-only) flags.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y --root-user-action=ignore pip
WORKDIR /app

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only $HOME (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

# libgomp1: OpenMP runtime torch/onnxruntime/scikit-learn's compiled extensions dlopen.
# gpu build only: gcc + libc headers — on CUDA, torch's native ops JIT-compile a small
# Triton C stub against Python.h (which python:*-slim already ships) at first use.
ARG DL_TRAINING_TORCH=cpu
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 $([ "$DL_TRAINING_TORCH" = "gpu" ] && echo gcc libc6-dev) \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/services/dl-training/.venv services/dl-training/.venv
COPY --from=builder /app/services/dl-training/src   services/dl-training/src
COPY --from=builder /app/services/dl-training/pyproject.toml services/dl-training/pyproject.toml
COPY --from=builder /app/services/dl-training/settings.yaml  services/dl-training/settings.yaml

ENV PATH="/app/services/dl-training/.venv/bin:$PATH"
# PYTHONUNBUFFERED: logs reach `kubectl logs` immediately, and aren't lost on a crash.
# MALLOC_ARENA_MAX=2: glibc otherwise gives each of uvicorn's worker threads its own
# malloc arena, fragmenting the heap and pinning RSS near its peak (measured on
# dl-inference: ~1.0 GB -> ~0.6 GB steady state).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/dl-training

USER app

CMD ["python", "-m", "dl_training.app"]
