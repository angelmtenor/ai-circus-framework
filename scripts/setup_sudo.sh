#!/bin/bash
#
# Ubuntu 26.04 Simple Setup Script — one-time, root-level machine provisioning for
# ai-circus-framework (WSL2, native Ubuntu, or a cloud/on-prem VM). Pair with
# scripts/setup_user.sh (per-user, no sudo). Docker Engine is deliberately NOT
# installed here — follow https://docs.docker.com/engine/install/ubuntu/ (see
# docs/windows-wsl.md §9).
#
# Usage (from the repo root):
#   sudo ./scripts/setup_sudo.sh [--gpu]
#
# Parameters:
#   --gpu
#       Optional. Installs NVIDIA GPU support by:
#         • Adding the graphics-drivers PPA
#         • Installing the driver `ubuntu-drivers autoinstall` selects
#         • Installing CUDA toolkit and utilities (e.g., nvtop)
#       A reboot is recommended when using this flag.
#       On WSL2 only the CUDA toolkit is installed — the GPU driver is the
#       Windows one (exposed via /usr/lib/wsl/lib); a Linux driver would break it.
#
# Description:
#   This script performs a streamlined, non-interactive initial setup for
#   Ubuntu 26.04 systems. It:
#     • Ensures execution as root
#     • Sets system timezone to UTC
#     • Checks for internet connectivity
#     • Updates and upgrades the system
#     • Installs a curated set of development and utility packages
#     • Optionally configures GPU driver and CUDA support
#     • Performs basic post-installation verification
#
# Notes:
#   - Must be run with sudo or as root.
#   - Designed to be simple, readable, and easy to modify.
#   - Safe to re-run; package installation is idempotent.


set -e

# --- Colors ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log(){ echo -e "$2${1}${NC}"; }

# --- Must run as root ---
[[ $EUID -ne 0 ]] && { log "${RED}❌ Run as root"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

# Wait (up to 10 min) for the apt/dpkg locks instead of failing — on a freshly booted
# Ubuntu the apt-daily/unattended-upgrades timers often hold them for a while.
apt_get() { apt-get -o DPkg::Lock::Timeout=600 "$@"; }

# Force UTC timezone and pre-seed tzdata
rm -f /etc/localtime /etc/timezone
echo "Etc/UTC" > /etc/timezone
ln -fs /usr/share/zoneinfo/UTC /etc/localtime
debconf-set-selections <<EOF
tzdata tzdata/Areas select Etc
tzdata tzdata/Zones/Etc select UTC
EOF

# --- Check internet ---
if command -v curl &>/dev/null; then curl -fsSI --max-time 10 https://archive.ubuntu.com >/dev/null
elif command -v wget &>/dev/null; then wget -q --spider --timeout=10 https://archive.ubuntu.com
else apt_get update -y >/dev/null; fi || { log "${RED}❌ No internet detected"; exit 2; }

# --- Package list (easy to edit) ---
PACKAGES=(
  git git-flow make curl wget ca-certificates
  nano htop gcc g++ clang linux-libc-dev pipx xclip
  python3 python3-pip python3-venv
)

log "${GREEN}ℹ️ Updating system..."
apt_get update -y
apt_get full-upgrade -y

log "${GREEN}ℹ️ Installing base packages..."
apt_get install -y --no-install-recommends "${PACKAGES[@]}"
apt_get autoremove -y

# --- Optional GPU setup ---
GPU=false
[[ ${1:-} == "--gpu" ]] && GPU=true

if $GPU && grep -qi microsoft /proc/version; then
    # WSL: the Windows NVIDIA driver already exposes the GPU via /usr/lib/wsl/lib/libcuda.so;
    # installing a Linux driver here overwrites it and breaks CUDA. Toolkit only.
    log "${YELLOW}⚡ GPU flag on WSL: skipping Linux NVIDIA driver (provided by Windows), installing CUDA toolkit only..."
    apt_get install -y nvidia-cuda-toolkit nvtop
    GPU=false   # no reboot needed
elif $GPU; then
    log "${YELLOW}⚡ GPU flag detected: installing NVIDIA/CUDA..."

    apt_get install -y software-properties-common
    add-apt-repository -y ppa:graphics-drivers/ppa
    apt_get update -y

    # `ubuntu-drivers autoinstall` selects the right driver package; there is no
    # "nvidia-driver-latest" package in the archive or the PPA.
    apt_get install -y ubuntu-drivers-common
    ubuntu-drivers autoinstall
    apt_get install -y nvidia-cuda-toolkit nvtop

    log "${GREEN}✅ NVIDIA/CUDA installed. Reboot recommended."
fi

# --- Verification (minimal) ---
for cmd in git curl gcc python3; do
    command -v "$cmd" &>/dev/null || { log "${RED}❌ $cmd missing."; exit 3; }
done

log "${GREEN}✅ Setup complete!"
if $GPU; then log "${YELLOW}⚠️ Reboot required for GPU drivers."; fi
exit 0
