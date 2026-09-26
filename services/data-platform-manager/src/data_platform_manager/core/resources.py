"""
- Title:    Resource monitor — CPU, memory and GPU usage of the cluster and every pod
- Author:   Angel Martinez-Tenor

Backs the admin Platform page's "Monitor" tab through GET /platform/resources:

- **CPU / memory** come from the Kubernetes Metrics API (metrics.k8s.io — the
  metrics-server k3s ships by default): the node's usage against its allocatable
  capacity, and every pod's usage grouped by its `app` (or `job-name`) label against the
  memory limit its manifest sets. These are metrics-server's own ~15 s windows, so the
  UI polling faster only repeats a value — it never costs the cluster anything extra.
- **Host memory** (available + swap) comes from this pod's own /proc/meminfo (psutil),
  which — containers share the kernel — is the machine the node runs on: on k3d, the
  Linux/WSL host itself. That, not the node's own usage, is what says the cluster is
  about to run out of memory: other host processes (image builds, a GPU training run)
  eat the same RAM, and the kernel OOM-kills pods when *it* runs out. The node's
  `MemoryPressure` condition (the kubelet starting to evict) is reported alongside.
- **GPU** comes from NVML (nvidia-ml-py), read in-process. This pod never *requests* the
  GPU (that would take the single `nvidia.com/gpu` away from training Jobs): on a GPU
  cluster, `make k3s-up` patches it onto the `nvidia` RuntimeClass with
  NVIDIA_DRIVER_CAPABILITIES=utility (k8s/gpu/), which mounts the driver's monitoring
  library and nvidia-smi only — no CUDA. Anywhere else NVML simply fails to load and the
  report says why.

Everything here is read-only and best-effort: a failing source becomes an
`available: false` + `reason`, never an error response — this runs every few seconds
while the tab is open.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import psutil
import pynvml
from kubernetes import client, config
from kubernetes.utils import parse_quantity

from data_platform_manager.core import k8s_jobs

_METRICS_GROUP = "metrics.k8s.io"
_METRICS_VERSION = "v1beta1"
_NVML_RETRY_SECONDS = 60.0
NO_CLUSTER_REASON = "Resource metrics need the k3s deployment (Kubernetes metrics-server) — not available in docker-compose."
NO_GPU_REASON = (
    "No NVIDIA GPU is visible to data-platform-manager. GPU telemetry needs a GPU cluster "
    "(K3S_GPU — see k8s/README.md), where `make k3s-up` grants this pod read-only GPU access."
)


@dataclass(frozen=True)
class NodeUsage:
    """One node's current usage against what it can allocate to pods."""

    name: str
    cpu_cores: float
    cpu_allocatable_cores: float
    memory_bytes: int
    memory_allocatable_bytes: int
    memory_pressure: bool = False


@dataclass(frozen=True)
class HostMemory:
    """The kernel's view of the machine this pod runs on (containers share it)."""

    total_bytes: int
    available_bytes: int
    swap_total_bytes: int
    swap_used_bytes: int


@dataclass(frozen=True)
class WorkloadUsage:
    """Current usage of every running pod sharing one `app`/`job-name` label."""

    name: str
    pods: int
    cpu_cores: float
    memory_bytes: int
    memory_limit_bytes: int | None


@dataclass(frozen=True)
class GpuDevice:
    """One GPU as NVML reports it — a metric the driver doesn't support (e.g. power limit
    on some laptops/WSL) is None rather than failing the whole device.
    """

    index: int
    name: str
    utilization_pct: int | None
    memory_used_bytes: int | None
    memory_total_bytes: int | None
    temperature_c: int | None
    power_w: float | None
    power_limit_w: float | None


@dataclass(frozen=True)
class GpuReport:
    """Every GPU visible to this pod, or why there is none."""

    available: bool
    reason: str | None = None
    driver_version: str | None = None
    devices: list[GpuDevice] = field(default_factory=list)


@dataclass(frozen=True)
class ResourceReport:
    """The whole Monitor tab payload."""

    checked_at: str
    available: bool
    reason: str | None
    gpu: GpuReport
    host: HostMemory | None = None
    nodes: list[NodeUsage] = field(default_factory=list)
    workloads: list[WorkloadUsage] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form (nested dataclasses included) for the API response."""
        return asdict(self)


def _cores(quantity: str | None) -> float:
    return float(parse_quantity(quantity)) if quantity else 0.0


def _bytes(quantity: str | None) -> int:
    return int(parse_quantity(quantity)) if quantity else 0


def _label(labels: dict[str, str] | None) -> str | None:
    labels = labels or {}
    return labels.get("app") or labels.get("job-name")


def node_usage() -> list[NodeUsage]:
    """Every node's CPU/memory usage (Metrics API) against its allocatable capacity."""
    config.load_incluster_config()
    core = client.CoreV1Api()
    metrics = cast(
        "dict[str, Any]", client.CustomObjectsApi().list_cluster_custom_object(_METRICS_GROUP, _METRICS_VERSION, "nodes")
    )
    usage = {item["metadata"]["name"]: item.get("usage", {}) for item in metrics.get("items", [])}
    nodes = cast("client.V1NodeList", core.list_node())
    result = []
    for node in nodes.items or []:
        name = str(node.metadata.name if node.metadata else "?")
        allocatable = (node.status.allocatable if node.status else None) or {}
        conditions = (node.status.conditions if node.status else None) or []
        used = usage.get(name, {})
        result.append(
            NodeUsage(
                name=name,
                cpu_cores=_cores(used.get("cpu")),
                cpu_allocatable_cores=_cores(allocatable.get("cpu")),
                memory_bytes=_bytes(used.get("memory")),
                memory_allocatable_bytes=_bytes(allocatable.get("memory")),
                memory_pressure=any(c.type == "MemoryPressure" and c.status == "True" for c in conditions),
            )
        )
    return result


def workload_usage() -> list[WorkloadUsage]:
    """Every running pod's usage summed per `app`/`job-name` label, heaviest (memory) first.

    The memory limit is the sum of the pods' container limits, or None when any container
    of the group sets none (then there is no ceiling to show usage against).
    """
    config.load_incluster_config()
    metrics = cast(
        "dict[str, Any]",
        client.CustomObjectsApi().list_namespaced_custom_object(
            _METRICS_GROUP, _METRICS_VERSION, k8s_jobs.NAMESPACE, "pods"
        ),
    )
    usage_by_pod: dict[str, tuple[float, int]] = {}
    for item in metrics.get("items", []):
        containers = item.get("containers", [])
        usage_by_pod[item["metadata"]["name"]] = (
            sum(_cores(c.get("usage", {}).get("cpu")) for c in containers),
            sum(_bytes(c.get("usage", {}).get("memory")) for c in containers),
        )

    pods = cast("client.V1PodList", client.CoreV1Api().list_namespaced_pod(k8s_jobs.NAMESPACE))
    groups: dict[str, dict[str, Any]] = {}
    for pod in pods.items or []:
        meta = pod.metadata
        label = _label(meta.labels if meta else None)
        if not meta or not label or not pod.status or pod.status.phase != "Running":
            continue
        cpu, memory = usage_by_pod.get(str(meta.name), (0.0, 0))
        limits = [
            ((c.resources.limits or {}) if c.resources else {}).get("memory") for c in (pod.spec.containers if pod.spec else [])
        ]
        group = groups.setdefault(label, {"pods": 0, "cpu": 0.0, "memory": 0, "limit": 0})
        group["pods"] += 1
        group["cpu"] += cpu
        group["memory"] += memory
        group["limit"] = None if group["limit"] is None or None in limits else group["limit"] + sum(map(_bytes, limits))
    result = [
        WorkloadUsage(
            name=name,
            pods=g["pods"],
            cpu_cores=g["cpu"],
            memory_bytes=g["memory"],
            memory_limit_bytes=g["limit"] or None,
        )
        for name, g in groups.items()
    ]
    return sorted(result, key=lambda w: w.memory_bytes, reverse=True)


# ── GPU (NVML) ──────────────────────────────────────────────────────────────────
# nvmlInit is process-wide: initialize once and keep it. A failed init (no driver
# mounted into this pod) is remembered for _NVML_RETRY_SECONDS so a polling UI doesn't
# re-dlopen a library that isn't there every few seconds.
_nvml_lock = threading.Lock()
_nvml_ready = False
_nvml_failed_at: float | None = None


def _ensure_nvml() -> bool:
    global _nvml_ready, _nvml_failed_at
    with _nvml_lock:
        if _nvml_ready:
            return True
        if _nvml_failed_at is not None and time.monotonic() - _nvml_failed_at < _NVML_RETRY_SECONDS:
            return False
        try:
            pynvml.nvmlInit()
        except pynvml.NVMLError:
            _nvml_failed_at = time.monotonic()
            return False
        _nvml_ready = True
        return True


def _optional(read: Any, *args: Any) -> Any:  # noqa: ANN401 — NVML's bindings are untyped
    try:
        return read(*args)
    except pynvml.NVMLError:
        return None


def _gpu_device(index: int) -> GpuDevice:
    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
    utilization = _optional(pynvml.nvmlDeviceGetUtilizationRates, handle)
    memory = _optional(pynvml.nvmlDeviceGetMemoryInfo, handle)
    power_mw = _optional(pynvml.nvmlDeviceGetPowerUsage, handle)
    limit_mw = _optional(pynvml.nvmlDeviceGetEnforcedPowerLimit, handle)
    name = pynvml.nvmlDeviceGetName(handle)
    return GpuDevice(
        index=index,
        name=name.decode() if isinstance(name, bytes) else str(name),
        utilization_pct=int(utilization.gpu) if utilization is not None else None,
        memory_used_bytes=int(memory.used) if memory is not None else None,
        memory_total_bytes=int(memory.total) if memory is not None else None,
        temperature_c=_optional(pynvml.nvmlDeviceGetTemperature, handle, pynvml.NVML_TEMPERATURE_GPU),
        power_w=round(power_mw / 1000, 1) if power_mw is not None else None,
        power_limit_w=round(limit_mw / 1000, 1) if limit_mw is not None else None,
    )


def gpu_report() -> GpuReport:
    """Every GPU NVML can see from inside this pod — never raises."""
    if not _ensure_nvml():
        return GpuReport(available=False, reason=NO_GPU_REASON)
    try:
        count = pynvml.nvmlDeviceGetCount()
        devices = [_gpu_device(i) for i in range(count)]
        driver = _optional(pynvml.nvmlSystemGetDriverVersion)
    except pynvml.NVMLError as exc:
        return GpuReport(available=False, reason=f"NVML error: {exc}")
    if not devices:
        return GpuReport(available=False, reason=NO_GPU_REASON)
    return GpuReport(available=True, driver_version=str(driver) if driver else None, devices=devices)


def host_memory() -> HostMemory | None:
    """Available memory and swap of the machine this pod runs on — None if unreadable."""
    try:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
    except OSError:
        return None
    return HostMemory(
        total_bytes=int(memory.total),
        available_bytes=int(memory.available),
        swap_total_bytes=int(swap.total),
        swap_used_bytes=int(swap.used),
    )


def collect() -> ResourceReport:
    """Node + per-workload usage (in-cluster only), host memory and GPU telemetry (wherever
    NVML loads).
    """
    checked_at = datetime.now(UTC).isoformat()
    gpu = gpu_report()
    host = host_memory()
    if not k8s_jobs.in_cluster_config_available():
        return ResourceReport(checked_at=checked_at, available=False, reason=NO_CLUSTER_REASON, gpu=gpu, host=host)
    try:
        nodes = node_usage()
        workloads = workload_usage()
    except Exception as exc:  # metrics-server missing/not ready, RBAC, API hiccup
        reason = f"Kubernetes Metrics API unavailable ({type(exc).__name__}: {getattr(exc, 'reason', exc)})"
        return ResourceReport(checked_at=checked_at, available=False, reason=reason[:300], gpu=gpu, host=host)
    return ResourceReport(
        checked_at=checked_at, available=True, reason=None, gpu=gpu, host=host, nodes=nodes, workloads=workloads
    )
