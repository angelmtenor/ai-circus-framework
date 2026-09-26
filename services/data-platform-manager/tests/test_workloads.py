"""Tests for core.workloads — the optional-service Start/Stop allowlist and its RBAC twin.

The allowlist is enforced twice (Python + the Role's `resourceNames`), so the first test
pins the two together: adding a service to OPTIONAL_SERVICES without granting it in
k8s/base/data-platform-manager.yaml (or the reverse) fails here, not on a live cluster.
No real Kubernetes API: AppsV1Api methods are monkeypatched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from data_platform_manager.core import platform_status, workloads
from data_platform_manager.core.workloads import OptionalService, Replicas, Workload

_MANIFEST = Path(__file__).parents[3] / "k8s" / "base" / "data-platform-manager.yaml"
_GPU_PATCH = Path(__file__).parents[3] / "k8s" / "gpu" / "data-platform-manager-gpu-telemetry.yaml"


def _role_rules() -> list[dict]:
    docs = list(yaml.safe_load_all(_MANIFEST.read_text(encoding="utf-8")))
    role = next(d for d in docs if d and d["kind"] == "Role")
    return role["rules"]


def test_rbac_scale_permission_is_exactly_the_optional_services_workloads() -> None:
    scale_rules = [r for r in _role_rules() if any(res.endswith("/scale") for res in r["resources"])]
    assert len(scale_rules) == 1
    rule = scale_rules[0]
    assert set(rule["resources"]) == {"deployments/scale", "statefulsets/scale"}
    assert set(rule["resourceNames"]) == workloads.controllable_workload_names()
    assert "create" not in rule["verbs"] and "delete" not in rule["verbs"]


def test_core_platform_is_never_controllable() -> None:
    names = workloads.controllable_workload_names()
    for core in ("postgres", "keycloak", "platform-registry", "data-platform-manager", "llm-gateway", "valkey"):
        assert core not in names


def test_every_optional_service_is_a_dashboard_component() -> None:
    target_names = {t.name for t in platform_status.TARGETS}
    assert set(workloads.OPTIONAL_SERVICES) <= target_names


def test_gpu_patch_targets_the_real_container_and_never_requests_a_gpu() -> None:
    patch = yaml.safe_load(_GPU_PATCH.read_text(encoding="utf-8"))
    pod = patch["spec"]["template"]["spec"]
    assert pod["runtimeClassName"] == "nvidia"
    (container,) = pod["containers"]
    docs = list(yaml.safe_load_all(_MANIFEST.read_text(encoding="utf-8")))
    deployment = next(d for d in docs if d and d["kind"] == "Deployment")
    assert container["name"] == deployment["spec"]["template"]["spec"]["containers"][0]["name"]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["NVIDIA_DRIVER_CAPABILITIES"] == "utility"  # NVML only — no CUDA
    assert "resources" not in container


_SERVICE = OptionalService(
    (Workload("statefulset", "db"), Workload("deployment", "web")), deploy_hint="make up", stop_effect="none"
)


@pytest.mark.parametrize(
    ("replicas", "expected"),
    [
        ({}, "not_deployed"),
        ({"db": Replicas(1, 1), "web": Replicas(1, 1)}, "running"),
        ({"db": Replicas(1, 1), "web": Replicas(1, 0)}, "starting"),
        ({"db": Replicas(0, 0), "web": Replicas(0, 0)}, "stopped"),
        ({"db": Replicas(0, 0), "web": Replicas(0, 1)}, "stopping"),
        ({"db": Replicas(1, 1), "web": Replicas(0, 0)}, "partial"),
        ({"web": Replicas(1, 1)}, "running"),  # only part of an overlay applied
    ],
)
def test_service_state(replicas: dict[str, Replicas], expected: str) -> None:
    assert workloads.service_state(_SERVICE, replicas) == expected


class _FakeApps:
    """Records every scale patch; `missing` names answer 404, `broken` ones 403."""

    def __init__(self, missing: set[str] = frozenset(), broken: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.calls: list[tuple[str, str, int]] = []
        self.missing = missing
        self.broken = broken

    def _patch(self, kind: str, name: str, body: dict) -> None:
        if name in self.missing:
            raise ApiException(status=404, reason="Not Found")
        if name in self.broken:
            raise ApiException(status=403, reason="Forbidden")
        self.calls.append((kind, name, body["spec"]["replicas"]))

    def patch_namespaced_deployment_scale(self, name: str, namespace: str, body: dict) -> None:
        assert namespace == "ai-circus"
        self._patch("deployment", name, body)

    def patch_namespaced_stateful_set_scale(self, name: str, namespace: str, body: dict) -> None:
        assert namespace == "ai-circus"
        self._patch("statefulset", name, body)


@pytest.fixture
def fake_apps(monkeypatch: pytest.MonkeyPatch) -> _FakeApps:
    fake = _FakeApps()
    monkeypatch.setattr(workloads.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(workloads.client, "AppsV1Api", lambda: fake)
    return fake


def test_start_scales_dependencies_first_and_stop_in_reverse(fake_apps: _FakeApps) -> None:
    workloads.set_running("langfuse", running=True)
    assert fake_apps.calls == [
        ("statefulset", "clickhouse", 1),
        ("deployment", "langfuse-web", 1),
        ("deployment", "langfuse-worker", 1),
    ]
    fake_apps.calls.clear()
    workloads.set_running("langfuse", running=False)
    assert fake_apps.calls == [
        ("deployment", "langfuse-worker", 0),
        ("deployment", "langfuse-web", 0),
        ("statefulset", "clickhouse", 0),
    ]


def test_missing_workloads_are_skipped_but_none_at_all_is_not_deployed(fake_apps: _FakeApps) -> None:
    fake_apps.missing = {"langfuse-worker"}
    workloads.set_running("langfuse", running=False)
    assert [name for _, name, _ in fake_apps.calls] == ["langfuse-web", "clickhouse"]

    fake_apps.missing = {"kafka"}
    with pytest.raises(workloads.NotDeployedError):
        workloads.set_running("kafka", running=True)


def test_other_api_errors_propagate(fake_apps: _FakeApps) -> None:
    fake_apps.broken = {"mlflow"}
    with pytest.raises(ApiException):
        workloads.set_running("mlflow", running=False)


def test_unknown_service_is_a_key_error(fake_apps: _FakeApps) -> None:
    with pytest.raises(KeyError):
        workloads.set_running("postgres", running=False)
    assert fake_apps.calls == []


def test_list_replicas_reads_deployments_and_statefulsets(monkeypatch: pytest.MonkeyPatch) -> None:
    def workload(name: str, desired: int | None, ready: int | None) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name),
            spec=SimpleNamespace(replicas=desired),
            status=SimpleNamespace(ready_replicas=ready),
        )

    class Apps:
        def list_namespaced_deployment(self, namespace: str) -> SimpleNamespace:
            return SimpleNamespace(items=[workload("mlflow", 0, None), workload("assistant", 1, 1)])

        def list_namespaced_stateful_set(self, namespace: str) -> SimpleNamespace:
            return SimpleNamespace(items=[workload("postgres", None, 1)])

    monkeypatch.setattr(workloads.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(client, "AppsV1Api", Apps)
    assert workloads.list_replicas() == {
        "mlflow": Replicas(0, 0),
        "assistant": Replicas(1, 1),
        "postgres": Replicas(1, 1),  # unset spec.replicas means 1
    }
