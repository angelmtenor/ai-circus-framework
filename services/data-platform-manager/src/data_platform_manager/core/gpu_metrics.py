"""
- Title:    GPU metrics on /metrics, named like NVIDIA's DCGM exporter (the NVML fallback)
- Author:   Angel Martinez-Tenor

The platform's GPU source is NVIDIA's DCGM exporter where it runs (datacenter GPUs on Linux nodes,
usually from the GPU operator — ADR 0015). It does not run on WSL2 or consumer cards, where this pod's
NVML reading (core/resources.py, the admin Monitor tab's source) is all there is. So the same readings
are published here under DCGM's metric names, labelled `source="nvml"`: the framework's GPU dashboard
and alert rules read either source unchanged (`max by (gpu)` absorbs both being present).

Read at scrape time from `resources.gpu_report()` — no polling thread, and NVML's own retry back-off
applies. `ai_circus_gpu_telemetry_available` is 1 while NVML sees a GPU, so a dashboard or an alert can
tell "no GPU here" from "GPU idle".
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import REGISTRY, Collector, CollectorRegistry

from data_platform_manager.core import resources

SOURCE = "nvml"
MIB = 1024 * 1024
LABELS = ["gpu", "modelName", "source"]


def _fb_used(device: resources.GpuDevice) -> float | None:
    return device.memory_used_bytes / MIB if device.memory_used_bytes is not None else None


def _fb_free(device: resources.GpuDevice) -> float | None:
    if device.memory_used_bytes is None or device.memory_total_bytes is None:
        return None
    return (device.memory_total_bytes - device.memory_used_bytes) / MIB


# (DCGM field name, help, reading) — units as DCGM publishes them.
METRICS: tuple[tuple[str, str, Callable[[resources.GpuDevice], float | None]], ...] = (
    ("DCGM_FI_DEV_GPU_UTIL", "GPU utilization (in %).", lambda d: d.utilization_pct),
    ("DCGM_FI_DEV_FB_USED", "Framebuffer memory used (in MiB).", _fb_used),
    ("DCGM_FI_DEV_FB_FREE", "Framebuffer memory free (in MiB).", _fb_free),
    ("DCGM_FI_DEV_GPU_TEMP", "GPU temperature (in C).", lambda d: d.temperature_c),
    ("DCGM_FI_DEV_POWER_USAGE", "Power draw (in W).", lambda d: d.power_w),
)


class NvmlGpuCollector(Collector):
    """Yields the DCGM-named gauges for every GPU NVML sees, read when Prometheus scrapes."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        """Read NVML once and yield the availability gauge, then one family per DCGM field."""
        report = resources.gpu_report()
        available = GaugeMetricFamily(
            "ai_circus_gpu_telemetry_available", "1 while NVML sees at least one GPU.", labels=["source"]
        )
        available.add_metric([SOURCE], 1.0 if report.available else 0.0)
        yield available
        if not report.available:
            return
        for name, documentation, read in METRICS:
            family = GaugeMetricFamily(name, documentation, labels=LABELS)
            for device in report.devices:
                value = read(device)
                if value is not None:
                    family.add_metric([str(device.index), device.name, SOURCE], float(value))
            yield family


_registered: dict[int, NvmlGpuCollector] = {}


def register_gpu_metrics(registry: CollectorRegistry = REGISTRY) -> NvmlGpuCollector:
    """Add the collector to `registry` (the default one /metrics serves) — once per registry."""
    collector = _registered.get(id(registry))
    if collector is None:
        collector = NvmlGpuCollector()
        registry.register(collector)
        _registered[id(registry)] = collector
    return collector
