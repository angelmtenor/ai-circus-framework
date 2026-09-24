#!/usr/bin/env bash
# Fine-tune deep_learning scenario models on THIS machine (the practical GPU path: k3d
# pods can't see the host GPU without nvidia-container-toolkit), then publish them to
# the running stack's SeaweedFS through Traefik (objectstore.localhost) — dl-inference
# picks them up on its next load (`make k3s-dl-reload`).
#
# Usage: ./scripts/dl_train_host.sh [comma,separated,slugs]   (empty = every deep_learning scenario)
#        ./scripts/dl_train_host.sh --download-only [slugs]  (fetch + verify + store raw data only)
#
# GPU: detected with nvidia-smi. With a GPU, torch comes from the CUDA wheel index into a
# separate `.venv-gpu` (so `make check`'s CPU `.venv` is never flipped back and forth);
# DL_DEVICE=auto then trains with each scenario's `training.gpu` budget.
# No GPU: a CPU run is minutes-to-tens-of-minutes and uses the reduced `training.cpu`
# budget — it only proceeds with DL_ALLOW_CPU=1 or an interactive "y".
#
# Needs OBJECT_STORE_ACCESS_KEY/OBJECT_STORE_SECRET_KEY in the environment (the root
# Makefile exports .env) and a running stack (make k3s-all / make up) for SeaweedFS.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$REPO_ROOT/services/dl-training"

download_only=false
if [ "${1:-}" = "--download-only" ]; then
    download_only=true
    shift
fi
SCENARIOS="${1:-}"

# SeaweedFS endpoint for this host process. Browsers/curl resolve *.localhost by
# themselves, but Python goes through libc — many hosts (e.g. `hosts: files dns`) don't
# resolve it. Prefer Traefik's objectstore.localhost when it does resolve; otherwise
# port-forward straight to the k3s SeaweedFS Service for the duration of this run.
if [ -z "${OBJECT_STORE_ENDPOINT:-}" ]; then
    if getent hosts objectstore.localhost >/dev/null 2>&1 && curl -s -o /dev/null --max-time 5 "http://objectstore.localhost/"; then
        export OBJECT_STORE_ENDPOINT="http://objectstore.localhost"
    elif kubectl -n ai-circus get svc seaweedfs >/dev/null 2>&1; then
        port="${DL_OBJECT_STORE_PORT:-18333}"
        # Supervised: kubectl port-forward dies on the first API-server hiccup (e.g. a
        # busy laptop), which would otherwise abort the upload of a finished model.
        (
            while true; do
                kubectl -n ai-circus port-forward svc/seaweedfs "$port:8333" >>"${TMPDIR:-/tmp}/dl-train-port-forward.log" 2>&1
                sleep 1
            done
        ) &
        pf_pid=$!
        trap 'pkill -P $pf_pid 2>/dev/null; kill $pf_pid 2>/dev/null' EXIT
        for _ in $(seq 1 20); do curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$port/" && break; sleep 0.5; done
        export OBJECT_STORE_ENDPOINT="http://127.0.0.1:$port"
    else
        echo "❌ no SeaweedFS reachable: start the stack (make k3s-all / make up), or set OBJECT_STORE_ENDPOINT," >&2
        echo "   or add '127.0.0.1 objectstore.localhost' to /etc/hosts." >&2
        exit 1
    fi
fi
echo "▶ SeaweedFS endpoint: $OBJECT_STORE_ENDPOINT"

if $download_only; then
    echo "▶ downloading + verifying raw data for: ${SCENARIOS:-every deep_learning scenario}"
    cd "$PROJECT"  # the local profile's SCENARIOS_DIR is relative to the service dir
    uv sync -q --frozen --reinstall-package ai-circus-shared
    SCENARIOS="$SCENARIOS" APP_ENVIRONMENT=local uv run --no-sync dl-training-download
    exit 0
fi

if command -v nvidia-smi >/dev/null 2>&1 && gpu="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)" && [ -n "$gpu" ]; then
    echo "⚡ GPU detected: $gpu — full fine-tuning (training.gpu budget)"
    export UV_PROJECT_ENVIRONMENT="$PROJECT/.venv-gpu"
    # uv-managed CPython (not the distro's): torch's CUDA path JIT-compiles Triton
    # kernels, which needs Python.h — shipped by uv's builds, absent without python3-dev.
    export UV_PYTHON_PREFERENCE=only-managed
    # --reinstall-package: libs/shared is a non-editable path dependency whose version
    # never changes, so a plain sync would keep a stale copy after editing it.
    (cd "$PROJECT" && uv sync -q --frozen --no-default-groups --group gpu --reinstall-package ai-circus-shared)
    device="${DL_DEVICE:-cuda}"
else
    echo "⚠️  No NVIDIA GPU detected — training on CPU with each scenario's reduced training.cpu budget"
    echo "   (slower, and a less accurate model than a GPU run)."
    if [ "${DL_ALLOW_CPU:-0}" != "1" ]; then
        if [ -t 0 ]; then
            read -r -p "   Continue on CPU? [y/N] " answer
            [ "$answer" = "y" ] || [ "$answer" = "Y" ] || { echo "Aborted."; exit 1; }
        else
            echo "❌ refusing a non-interactive CPU run — set DL_ALLOW_CPU=1 to confirm." >&2
            exit 1
        fi
    fi
    (cd "$PROJECT" && uv sync -q --frozen --reinstall-package ai-circus-shared)
    device="${DL_DEVICE:-cpu}"
fi

echo "▶ training: ${SCENARIOS:-every deep_learning scenario} (DL_DEVICE=$device)"
cd "$PROJECT"
SCENARIOS="$SCENARIOS" DL_DEVICE="$device" APP_ENVIRONMENT=local uv run --no-sync dl-training
