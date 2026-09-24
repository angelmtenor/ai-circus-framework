#!/usr/bin/env bash
# Create the local k3d cluster (idempotent) — `make k3s-cluster`. With a GPU (K3S_GPU=1,
# or K3S_GPU=auto and Docker can pass one through: NVIDIA Container Toolkit installed via
# scripts/setup_gpu_containers.sh), the node runs ai-circus/k3s-gpu (infra/k3s-gpu/)
# with `--gpus all`, so pods can request `nvidia.com/gpu` — the admin console's
# in-cluster deep-learning training then uses it. k3d can only attach GPUs at creation:
# an existing CPU-only cluster has to be recreated to gain one (this script says so).
#
# Usage: scripts/k3s_cluster_create.sh <cluster> <auto|0|1> <k3s-version> [subnet]
set -euo pipefail

CLUSTER="$1"
GPU_MODE="$2"
K3S_VERSION="$3"
SUBNET="${4:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_IMAGE="ai-circus/k3s-gpu:${K3S_VERSION}"

docker_has_gpu() {
    docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia
}

case "$GPU_MODE" in
    1 | true | yes) gpu=true ;;
    0 | false | no) gpu=false ;;
    auto)
        if docker_has_gpu; then gpu=true; else gpu=false; fi
        ;;
    *) echo "❌ K3S_GPU must be auto, 1 or 0 (got '$GPU_MODE')" >&2; exit 1 ;;
esac

if k3d cluster list "$CLUSTER" >/dev/null 2>&1; then
    gpus=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' 2>/dev/null \
        | awk '{s+=$1} END {print s+0}')
    echo "✓ k3d cluster '$CLUSTER' already exists (nvidia.com/gpu allocatable: ${gpus:-0})"
    if $gpu && [ "${gpus:-0}" = "0" ]; then
        echo "ℹ️  Docker can pass a GPU through, but this cluster was created without one. k3d attaches GPUs only"
        echo "   at creation (pause/resume can't add one) — recreate it to get one (wipes the cluster's volumes):"
        echo "   k3d cluster delete $CLUSTER && make k3s-all-dl   (then 'make k3s-gpu-smoke')"
    fi
    exit 0
fi

args=(-p "80:80@loadbalancer" -v "$REPO_ROOT/scenarios:/scenarios@all")
[ -n "$SUBNET" ] && args+=(--subnet "$SUBNET")

if $gpu; then
    if ! docker_has_gpu; then
        echo "❌ K3S_GPU=1 but Docker has no nvidia runtime — run: sudo ./scripts/setup_gpu_containers.sh" >&2
        exit 1
    fi
    echo "⚡ creating a GPU-enabled cluster (node image $GPU_IMAGE, --gpus all)"
    docker build -t "$GPU_IMAGE" --build-arg "K3S_VERSION=$K3S_VERSION" "$REPO_ROOT/infra/k3s-gpu"
    # --disable-cloud-controller: on the (slower-starting) Ubuntu-based GPU node, k3s'
    # embedded cloud-controller-manager loses a startup race for its RoleBinding and
    # takes the whole server down in a restart loop (k3s-io/k3s#7328). A single-node
    # k3d cluster doesn't need it — Docker networking already provides node addresses.
    args+=(--image "$GPU_IMAGE" --gpus all --k3s-arg "--disable-cloud-controller@server:*")
else
    args+=(--image "rancher/k3s:$K3S_VERSION")
fi

k3d cluster create "$CLUSTER" "${args[@]}"
echo "✓ k3d cluster '$CLUSTER' ready$($gpu && echo ' (GPU — verify with: make k3s-gpu-smoke)')"
