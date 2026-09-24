#!/usr/bin/env bash
# One-time, root-level: let Docker containers use the host's NVIDIA GPU — the prerequisite
# for a GPU-enabled k3d cluster (`make k3s-cluster` with K3S_GPU=auto|1, see
# infra/k3s-gpu/). Installs the NVIDIA Container Toolkit from NVIDIA's apt repository,
# registers its runtime with Docker, restarts Docker, and runs `nvidia-smi` in a
# throw-away container to prove it works.
#
# Usage (from the repo root):  sudo ./scripts/setup_gpu_containers.sh
#
# Works on native Ubuntu (needs a working NVIDIA driver: `nvidia-smi` on the host) and
# on WSL2 (the driver is the Windows one, exposed via /usr/lib/wsl/lib — never install a
# Linux driver there, see docs/windows-wsl.md). Docker Engine itself must already be
# installed. Restarting Docker restarts every container, including a running k3d
# cluster (`make k3s-resume` brings it back).
set -euo pipefail

[[ $EUID -ne 0 ]] && { echo "❌ run with sudo"; exit 1; }
command -v docker >/dev/null || { echo "❌ Docker Engine is not installed"; exit 1; }
# On WSL nvidia-smi lives in /usr/lib/wsl/lib, which sudo's secure_path drops from PATH.
NVIDIA_SMI="$(command -v nvidia-smi || true)"
[ -z "$NVIDIA_SMI" ] && [ -x /usr/lib/wsl/lib/nvidia-smi ] && NVIDIA_SMI=/usr/lib/wsl/lib/nvidia-smi
[ -n "$NVIDIA_SMI" ] && "$NVIDIA_SMI" -L >/dev/null \
    || { echo "❌ no working NVIDIA driver (nvidia-smi not found or failing)"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt_get() { apt-get -o DPkg::Lock::Timeout=600 "$@"; }

apt_get install -y --no-install-recommends curl gnupg ca-certificates
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt_get update -y
apt_get install -y nvidia-container-toolkit

nvidia-ctk runtime configure --runtime=docker
if command -v systemctl >/dev/null && systemctl is-system-running >/dev/null 2>&1; then
    systemctl restart docker
else
    service docker restart
fi

echo "▶ verifying: nvidia-smi inside a container"
docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
echo "✅ Docker can use the GPU — next: 'make dl-gpu-check', then create a GPU k3d cluster (see k8s/README.md, Deep learning)."
