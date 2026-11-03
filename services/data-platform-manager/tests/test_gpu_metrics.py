"""Tests for core/gpu_metrics.py — DCGM-named GPU gauges from NVML."""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from data_platform_manager.core import gpu_metrics, resources

DEVICE = resources.GpuDevice(
    index=0,
    name="NVIDIA GeForce RTX 4070 Laptop GPU",
    utilization_pct=37,
    memory_used_bytes=2 * 1024**3,
    memory_total_bytes=8 * 1024**3,
    temperature_c=61,
    power_w=42.5,
    power_limit_w=None,
)


def _scrape(monkeypatch: pytest.MonkeyPatch, report: resources.GpuReport) -> CollectorRegistry:
    monkeypatch.setattr(resources, "gpu_report", lambda: report)
    registry = CollectorRegistry()
    gpu_metrics.register_gpu_metrics(registry)
    return registry


def test_a_gpu_is_published_under_dcgm_names(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _scrape(monkeypatch, resources.GpuReport(available=True, devices=[DEVICE]))
    labels = {"gpu": "0", "modelName": DEVICE.name, "source": "nvml"}
    assert registry.get_sample_value("DCGM_FI_DEV_GPU_UTIL", labels) == 37
    assert registry.get_sample_value("DCGM_FI_DEV_FB_USED", labels) == 2048
    assert registry.get_sample_value("DCGM_FI_DEV_FB_FREE", labels) == 6144
    assert registry.get_sample_value("DCGM_FI_DEV_GPU_TEMP", labels) == 61
    assert registry.get_sample_value("DCGM_FI_DEV_POWER_USAGE", labels) == pytest.approx(42.5)
    assert registry.get_sample_value("ai_circus_gpu_telemetry_available", {"source": "nvml"}) == 1


def test_a_reading_the_driver_lacks_is_omitted_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    partial = resources.GpuDevice(0, "GPU", None, None, None, None, None, None)
    registry = _scrape(monkeypatch, resources.GpuReport(available=True, devices=[partial]))
    assert b"DCGM_FI_DEV_GPU_UTIL{" not in generate_latest(registry)


def test_no_gpu_only_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _scrape(monkeypatch, resources.GpuReport(available=False, reason="no GPU"))
    assert registry.get_sample_value("ai_circus_gpu_telemetry_available", {"source": "nvml"}) == 0
    assert b"DCGM_FI" not in generate_latest(registry)


def test_registering_twice_is_harmless() -> None:
    registry = CollectorRegistry()
    assert gpu_metrics.register_gpu_metrics(registry) is gpu_metrics.register_gpu_metrics(registry)
