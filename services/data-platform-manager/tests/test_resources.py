"""Tests for core.resources — the Monitor tab's CPU/memory (Metrics API) and GPU (NVML) feed.

No cluster and no GPU needed: the Kubernetes clients and pynvml are monkeypatched, so
these pass the same on a laptop with a real GPU as in CI. The contract under test is
"every failing source becomes available=false + a reason, never an exception".
"""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace

import pynvml
import pytest

from data_platform_manager.core import k8s_jobs, resources

GIB = 1024**3


@pytest.fixture(autouse=True)
def reset_nvml_state() -> Generator[None]:
    resources._nvml_ready = False
    resources._nvml_failed_at = None
    yield
    resources._nvml_ready = False
    resources._nvml_failed_at = None


def _pod(name: str, labels: dict[str, str], limits: list[str | None], phase: str = "Running") -> SimpleNamespace:
    containers = [SimpleNamespace(resources=SimpleNamespace(limits={"memory": m} if m else None)) for m in limits]
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels=labels),
        status=SimpleNamespace(phase=phase),
        spec=SimpleNamespace(containers=containers),
    )


class _FakeMetrics:
    def list_cluster_custom_object(self, group: str, version: str, plural: str) -> dict:
        assert (group, version, plural) == ("metrics.k8s.io", "v1beta1", "nodes")
        return {"items": [{"metadata": {"name": "node-0"}, "usage": {"cpu": "1500m", "memory": "6Gi"}}]}

    def list_namespaced_custom_object(self, group: str, version: str, namespace: str, plural: str) -> dict:
        assert (group, plural, namespace) == ("metrics.k8s.io", "pods", "ai-circus")
        return {
            "items": [
                {"metadata": {"name": "llm-1"}, "containers": [{"usage": {"cpu": "20m", "memory": "300Mi"}}]},
                {"metadata": {"name": "web-a"}, "containers": [{"usage": {"cpu": "5m", "memory": "100Mi"}}]},
                {"metadata": {"name": "web-b"}, "containers": [{"usage": {"cpu": "5m", "memory": "100Mi"}}]},
                {"metadata": {"name": "job-x"}, "containers": [{"usage": {"cpu": "250000000n", "memory": "50Mi"}}]},
            ]
        }


class _FakeCore:
    def list_node(self) -> SimpleNamespace:
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="node-0"),
                    status=SimpleNamespace(
                        allocatable={"cpu": "16", "memory": "12Gi"},
                        conditions=[
                            SimpleNamespace(type="Ready", status="True"),
                            SimpleNamespace(type="MemoryPressure", status="True"),
                        ],
                    ),
                )
            ]
        )

    def list_namespaced_pod(self, namespace: str) -> SimpleNamespace:
        return SimpleNamespace(
            items=[
                _pod("llm-1", {"app": "llm-gateway"}, ["1536Mi"]),
                _pod("web-a", {"app": "web"}, ["512Mi"]),
                _pod("web-b", {"app": "web"}, ["512Mi", None]),  # a container without a limit
                _pod("job-x", {"job-name": "training"}, ["2Gi"]),
                _pod("done", {"job-name": "etl-tabular"}, ["1Gi"], phase="Succeeded"),
                _pod("orphan", {}, ["1Gi"]),
            ]
        )


@pytest.fixture
def fake_k8s(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(resources.client, "CustomObjectsApi", _FakeMetrics)
    monkeypatch.setattr(resources.client, "CoreV1Api", _FakeCore)


@pytest.mark.usefixtures("fake_k8s")
def test_node_usage_against_allocatable() -> None:
    (node,) = resources.node_usage()
    assert node.name == "node-0"
    assert node.cpu_cores == pytest.approx(1.5)
    assert node.cpu_allocatable_cores == 16
    assert node.memory_bytes == 6 * GIB
    assert node.memory_allocatable_bytes == 12 * GIB
    assert node.memory_pressure is True  # the kubelet is evicting — the UI flags it


@pytest.mark.usefixtures("fake_k8s")
def test_workload_usage_groups_by_label_heaviest_first() -> None:
    usage = {w.name: w for w in resources.workload_usage()}
    assert list(usage) == ["llm-gateway", "web", "training"]  # sorted by memory, desc
    assert usage["web"].pods == 2
    assert usage["web"].cpu_cores == pytest.approx(0.01)
    assert usage["web"].memory_bytes == 200 * 1024**2
    assert usage["web"].memory_limit_bytes is None  # one container has no ceiling
    assert usage["llm-gateway"].memory_limit_bytes == 1536 * 1024**2
    assert usage["training"].cpu_cores == pytest.approx(0.25)  # nanocores parsed


def _fake_nvml(monkeypatch: pytest.MonkeyPatch, *, power_limit_supported: bool) -> None:
    def not_supported(*_args: object) -> None:
        raise pynvml.NVMLError(pynvml.NVML_ERROR_NOT_SUPPORTED)

    monkeypatch.setattr(pynvml, "nvmlInit", lambda: None)
    monkeypatch.setattr(pynvml, "nvmlDeviceGetCount", lambda: 1)
    monkeypatch.setattr(pynvml, "nvmlDeviceGetHandleByIndex", lambda i: f"handle-{i}")
    monkeypatch.setattr(pynvml, "nvmlDeviceGetName", lambda h: "RTX Test")
    monkeypatch.setattr(pynvml, "nvmlDeviceGetUtilizationRates", lambda h: SimpleNamespace(gpu=42, memory=10))
    monkeypatch.setattr(pynvml, "nvmlDeviceGetMemoryInfo", lambda h: SimpleNamespace(used=2 * GIB, total=8 * GIB))
    monkeypatch.setattr(pynvml, "nvmlDeviceGetTemperature", lambda h, sensor: 55)
    monkeypatch.setattr(pynvml, "nvmlDeviceGetPowerUsage", lambda h: 14665)
    monkeypatch.setattr(
        pynvml, "nvmlDeviceGetEnforcedPowerLimit", (lambda h: 97048) if power_limit_supported else not_supported
    )
    monkeypatch.setattr(pynvml, "nvmlSystemGetDriverVersion", lambda: "610.62")


def test_gpu_report_reads_nvml(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_nvml(monkeypatch, power_limit_supported=True)
    report = resources.gpu_report()
    assert report.available is True
    assert report.driver_version == "610.62"
    (gpu,) = report.devices
    assert (gpu.name, gpu.utilization_pct, gpu.temperature_c) == ("RTX Test", 42, 55)
    assert (gpu.memory_used_bytes, gpu.memory_total_bytes) == (2 * GIB, 8 * GIB)
    assert (gpu.power_w, gpu.power_limit_w) == (14.7, 97.0)


def test_unsupported_gpu_metric_is_none_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_nvml(monkeypatch, power_limit_supported=False)
    (gpu,) = resources.gpu_report().devices
    assert gpu.power_limit_w is None
    assert gpu.power_w == 14.7


def test_no_driver_is_unavailable_and_not_retried_on_every_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def failing_init() -> None:
        attempts.append(1)
        raise pynvml.NVMLError(pynvml.NVML_ERROR_LIBRARY_NOT_FOUND)

    monkeypatch.setattr(pynvml, "nvmlInit", failing_init)
    first = resources.gpu_report()
    second = resources.gpu_report()
    assert first.available is False and first.reason == resources.NO_GPU_REASON
    assert second.available is False
    assert len(attempts) == 1  # remembered for _NVML_RETRY_SECONDS


def test_collect_outside_a_cluster_still_reports_the_gpu_and_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_nvml(monkeypatch, power_limit_supported=True)
    monkeypatch.setattr(k8s_jobs, "in_cluster_config_available", lambda: False)
    monkeypatch.setattr(
        resources.psutil, "virtual_memory", lambda: SimpleNamespace(total=12 * GIB, available=3 * GIB)
    )
    monkeypatch.setattr(resources.psutil, "swap_memory", lambda: SimpleNamespace(total=3 * GIB, used=GIB))
    report = resources.collect()
    assert report.available is False
    assert report.reason == resources.NO_CLUSTER_REASON
    assert report.gpu.available is True
    assert report.as_dict()["gpu"]["devices"][0]["name"] == "RTX Test"
    assert report.host == resources.HostMemory(
        total_bytes=12 * GIB, available_bytes=3 * GIB, swap_total_bytes=3 * GIB, swap_used_bytes=GIB
    )


def test_unreadable_host_memory_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable() -> None:
        raise OSError("no /proc")

    monkeypatch.setattr(resources.psutil, "virtual_memory", unreadable)
    assert resources.host_memory() is None


@pytest.mark.usefixtures("fake_k8s")
def test_collect_in_cluster_and_a_failing_metrics_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k8s_jobs, "in_cluster_config_available", lambda: True)
    monkeypatch.setattr(resources, "gpu_report", lambda: resources.GpuReport(available=False, reason="none"))
    report = resources.collect()
    assert report.available is True
    assert [n.name for n in report.nodes] == ["node-0"]
    assert report.workloads[0].name == "llm-gateway"

    def metrics_down(*_args: object) -> None:
        raise RuntimeError("the server could not find the requested resource")

    monkeypatch.setattr(_FakeMetrics, "list_cluster_custom_object", metrics_down)
    broken = resources.collect()
    assert broken.available is False
    assert broken.reason is not None and "Metrics API unavailable" in broken.reason
    assert broken.nodes == [] and broken.workloads == []
