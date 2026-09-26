"""
- Title:    Optional-service control — start/stop the platform's opt-in workloads
- Author:   Angel Martinez-Tenor

Backs the admin Platform page's Start/Stop buttons (POST /platform/components/{name}/
start|stop) and the "stopped" state on its Health cards. Stopping a service scales its
workload(s) to 0 replicas — exactly what `make k3s-lite` does for K3S_LITE_SKIP — so every
PVC, Secret and ConfigMap stays and Start (back to 1 replica) resumes it with its data.

Only OPTIONAL_SERVICES can be controlled: the core platform (Postgres, Keycloak,
platform-registry, this very service, ...) has nothing to gain from a stop button and
everything to lose. That allowlist is enforced twice — here, and by the Role's
`resourceNames` on the `*/scale` subresources in k8s/base/data-platform-manager.yaml
(tests/test_workloads.py pins the two together) — so even a bug here cannot scale
anything else. The overlay workloads (dl-inference, kafka) can be started/stopped once
deployed but never *created* from here: that needs their manifests (`make k3s-dl-up`,
`make k3s-data-platform-up`), the same boundary core/k8s_jobs.py keeps for Jobs.

Only usable in-cluster (see k8s_jobs.in_cluster_config_available); docker-compose has no
API to scale anything with.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from data_platform_manager.core.k8s_jobs import NAMESPACE

WorkloadKind = Literal["deployment", "statefulset"]
ServiceState = Literal["running", "starting", "stopping", "stopped", "partial", "not_deployed"]


@dataclass(frozen=True)
class Workload:
    """One Deployment/StatefulSet in the ai-circus namespace."""

    kind: WorkloadKind
    name: str


@dataclass(frozen=True)
class OptionalService:
    """A platform component the admin may stop to free RAM and start again later.

    `workloads` are listed in start order (dependencies first) and stopped in reverse;
    `deploy_hint` is how to deploy it when it doesn't exist in the cluster at all, and
    `stop_effect` is what the platform loses while it is stopped.
    """

    workloads: tuple[Workload, ...]
    deploy_hint: str
    stop_effect: str


# Keyed by core.platform_status.TARGETS name — the Health card that shows the buttons.
OPTIONAL_SERVICES: dict[str, OptionalService] = {
    "agui-voice": OptionalService(
        (Workload("deployment", "agui-voice"),),
        deploy_hint="make k3s-up",
        stop_effect="Voice mode is unavailable while stopped.",
    ),
    "mlflow": OptionalService(
        (Workload("deployment", "mlflow"),),
        deploy_hint="make k3s-up",
        stop_effect="Training runs still succeed but are not mirrored to the MLOps monitor.",
    ),
    "langfuse": OptionalService(
        (
            Workload("statefulset", "clickhouse"),
            Workload("deployment", "langfuse-web"),
            Workload("deployment", "langfuse-worker"),
        ),
        deploy_hint="make k3s-up",
        stop_effect="LLM calls keep working, but their traces are not recorded while stopped (web + worker + ClickHouse).",
    ),
    "dl-inference": OptionalService(
        (Workload("deployment", "dl-inference"),),
        deploy_hint="make k3s-dl-build k3s-dl-up",
        stop_effect="The deep_learning scenarios cannot serve predictions while stopped.",
    ),
    "kafka": OptionalService(
        (Workload("statefulset", "kafka"),),
        deploy_hint="make k3s-data-platform-up",
        stop_effect="Pipeline-trigger and CDC events are no longer published (best-effort by design).",
    ),
}


def controllable_workload_names() -> set[str]:
    """Every workload name any optional service may scale — what the RBAC Role's
    `resourceNames` must equal (tests/test_workloads.py asserts it).
    """
    return {w.name for service in OPTIONAL_SERVICES.values() for w in service.workloads}


@dataclass(frozen=True)
class Replicas:
    """A workload's desired vs. ready replica count."""

    desired: int
    ready: int


class NotDeployedError(Exception):
    """None of an optional service's workloads exist in the cluster."""


def list_replicas() -> dict[str, Replicas]:
    """Desired/ready replicas of every Deployment and StatefulSet in the namespace, by name."""
    config.load_incluster_config()
    apps = client.AppsV1Api()
    result: dict[str, Replicas] = {}
    deployments = cast("client.V1DeploymentList", apps.list_namespaced_deployment(NAMESPACE))
    statefulsets = cast("client.V1StatefulSetList", apps.list_namespaced_stateful_set(NAMESPACE))
    for item in [*(deployments.items or []), *(statefulsets.items or [])]:
        name = item.metadata.name if item.metadata else None
        if not name:
            continue
        desired = item.spec.replicas if item.spec and item.spec.replicas is not None else 1
        ready = (item.status.ready_replicas if item.status else None) or 0
        result[name] = Replicas(desired=desired, ready=ready)
    return result


def service_state(service: OptionalService, replicas: dict[str, Replicas]) -> ServiceState:
    """Fold an optional service's workloads into one state for its Start/Stop button."""
    present = [replicas[w.name] for w in service.workloads if w.name in replicas]
    if not present:
        return "not_deployed"
    if all(r.desired == 0 for r in present):
        return "stopping" if any(r.ready > 0 for r in present) else "stopped"
    if all(r.desired > 0 for r in present):
        return "running" if all(r.ready >= r.desired for r in present) else "starting"
    return "partial"


def _scale(apps: client.AppsV1Api, workload: Workload, replicas: int) -> None:
    body = {"spec": {"replicas": replicas}}
    if workload.kind == "deployment":
        apps.patch_namespaced_deployment_scale(workload.name, NAMESPACE, body)
    else:
        apps.patch_namespaced_stateful_set_scale(workload.name, NAMESPACE, body)


def set_running(name: str, running: bool) -> None:
    """Scale every workload of one optional service to 1 (start) or 0 (stop) replicas.

    Workloads missing from the cluster are skipped (an overlay applied only in part);
    if none exists at all this raises NotDeployedError — deploying is a `make` target's job.

    Raises:
        KeyError: `name` is not an optional service.
        NotDeployedError: none of its workloads exists.
    """
    service = OPTIONAL_SERVICES[name]
    config.load_incluster_config()
    apps = client.AppsV1Api()
    order = service.workloads if running else tuple(reversed(service.workloads))
    scaled = 0
    for workload in order:
        try:
            _scale(apps, workload, 1 if running else 0)
        except ApiException as exc:
            if exc.status == 404:
                continue
            raise
        scaled += 1
    if scaled == 0:
        raise NotDeployedError(name)
