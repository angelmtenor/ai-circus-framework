# k3s node image with NVIDIA GPU support, for a GPU-enabled local k3d cluster
# (`make k3s-cluster` with K3S_GPU=auto|1 builds it as ai-circus/k3s-gpu:<k3s version>).
# Follows k3d's CUDA guide (https://k3d.io/stable/usage/advanced/cuda/): the stock k3s
# image is Alpine-based, which the NVIDIA container runtime doesn't support, so k3s is
# copied onto Ubuntu together with the NVIDIA Container Toolkit. k3s auto-detects
# /usr/bin/nvidia-container-runtime at start and registers the `nvidia` containerd
# runtime; the device plugin below (auto-deployed from k3s' manifests dir) then
# advertises `nvidia.com/gpu` on the node.
#
# Needs the host's Docker to pass the GPU through (`k3d cluster create --gpus all`),
# i.e. scripts/setup_gpu_containers.sh has run once.
ARG K3S_VERSION=v1.35.5-k3s1
FROM rancher/k3s:${K3S_VERSION} AS k3s

FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
    && curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nvidia-container-toolkit \
    && rm -rf /var/lib/apt/lists/*

COPY --from=k3s / / --exclude=/bin
COPY --from=k3s /bin /bin

# Auto-applied by k3s at start: RuntimeClass `nvidia` + NVIDIA device plugin DaemonSet.
COPY nvidia-device-plugin.yaml /var/lib/rancher/k3s/server/manifests/nvidia-device-plugin.yaml

ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

VOLUME /var/lib/kubelet
VOLUME /var/lib/rancher/k3s
VOLUME /var/lib/cni
VOLUME /var/log

ENV PATH="$PATH:/bin/aux"
ENTRYPOINT ["/bin/k3s"]
CMD ["agent"]
