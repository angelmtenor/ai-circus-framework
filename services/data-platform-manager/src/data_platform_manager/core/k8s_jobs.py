"""
- Title:    Kubernetes Job control for the pipeline (etl-tabular, training, etl-vectorize)
- Author:   ai-circus-framework contributors

Mirrors k8s/jobs/*.yaml exactly — kept in sync by hand, the same "documented,
tested hand-sync" convention scripts/k3s_generate_secrets.sh already uses for its
own SECRET_SPECS vs. docker-compose.yml's per-service env blocks (see that
script's module comment). tests/test_k8s_jobs.py parses those YAML files and
asserts the two agree, so a re-YAML of one without the other fails `make check`
locally instead of drifting silently. Building typed `kubernetes.client.V1Job`
objects here — rather than loading+applying the YAML files at runtime — means no
new volume mount is needed on this pod just to read them.

Only usable when this process is itself inside a real cluster (see
`in_cluster_config_available`) — a docker-compose deployment has no Kubernetes
API to call, and api.py reports that clearly rather than pretending job control
is available there.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

NAMESPACE = "ai-circus"

_IN_CLUSTER_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def in_cluster_config_available() -> bool:
    """Whether this process is running as a real k8s pod with a mounted
    ServiceAccount token — the one thing docker-compose can never provide.
    """
    return _IN_CLUSTER_TOKEN.exists()


def _container(name: str, image: str, secret_name: str) -> client.V1Container:
    return client.V1Container(
        name=name,
        image=image,
        image_pull_policy="Never",
        security_context=client.V1SecurityContext(
            allow_privilege_escalation=False, capabilities=client.V1Capabilities(drop=["ALL"])
        ),
        env_from=[
            client.V1EnvFromSource(config_map_ref=client.V1ConfigMapEnvSource(name="shared-config")),
            client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name=secret_name)),
        ],
        volume_mounts=[client.V1VolumeMount(name="scenarios", mount_path="/app/scenarios", read_only=True)],
    )


def _job(name: str, container: client.V1Container) -> client.V1Job:
    return client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE),
        spec=client.V1JobSpec(
            backoff_limit=0,
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    automount_service_account_token=False,
                    security_context=client.V1PodSecurityContext(
                        run_as_non_root=True,
                        run_as_user=1000,
                        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                    ),
                    containers=[container],
                    volumes=[
                        client.V1Volume(
                            name="scenarios",
                            host_path=client.V1HostPathVolumeSource(path="/scenarios", type="Directory"),
                        )
                    ],
                )
            ),
        ),
    )


def _etl_vectorize_container() -> client.V1Container:
    """etl-vectorize additionally forwards LITELLM_MASTER_KEY as LLM_GATEWAY_API_KEY —
    only reached when EMBEDDING_PROVIDER=local (llm-gateway's `local-embed` model).
    """
    container = _container("etl-vectorize", "ai-circus/etl-vectorize:local", "etl-vectorize-secrets")
    container.env = [
        client.V1EnvVar(
            name="LLM_GATEWAY_API_KEY",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name="etl-vectorize-secrets", key="LITELLM_MASTER_KEY")
            ),
        )
    ]
    return container


PIPELINE_JOBS: dict[str, client.V1Job] = {
    "etl-tabular": _job("etl-tabular", _container("etl-tabular", "ai-circus/etl-tabular:local", "etl-tabular-secrets")),
    "training": _job("training", _container("training", "ai-circus/training:local", "training-secrets")),
    "etl-vectorize": _job("etl-vectorize", _etl_vectorize_container()),
}


@dataclass(frozen=True)
class JobStatus:
    """One pipeline job's current state, as reported by the Kubernetes API."""

    name: str
    state: str  # "not_run" | "running" | "succeeded" | "failed"
    started_at: str | None
    completed_at: str | None


def _load_config() -> None:
    config.load_incluster_config()


def get_job_status(name: str) -> JobStatus:
    """Read one Job's current state. "not_run" both before its first trigger and
    after `trigger_job` deletes it mid-cycle — a Job resource simply doesn't exist
    at either point.
    """
    _load_config()
    batch = client.BatchV1Api()
    try:
        # kubernetes-client's own type hints don't distinguish this call's real
        # return type (V1Job) from the `async_req=True` overload's (ApplyResult) —
        # we never pass that kwarg, so this is always a V1Job at runtime.
        job = cast("client.V1Job", batch.read_namespaced_job_status(name, NAMESPACE))
    except ApiException as exc:
        if exc.status == 404:
            return JobStatus(name=name, state="not_run", started_at=None, completed_at=None)
        raise
    status = cast("client.V1JobStatus", job.status)
    if status.succeeded:
        state = "succeeded"
    elif status.failed:
        state = "failed"
    elif status.active:
        state = "running"
    else:
        state = "not_run"
    return JobStatus(
        name=name,
        state=state,
        started_at=status.start_time.isoformat() if status.start_time else None,
        completed_at=status.completion_time.isoformat() if status.completion_time else None,
    )


def trigger_job(name: str) -> None:
    """Delete any previous run and create a fresh one — mirrors `make k3s-pipeline`'s
    own delete-then-apply (Jobs don't restart on their own once Complete/Failed).
    """
    if name not in PIPELINE_JOBS:
        raise KeyError(name)
    _load_config()
    batch = client.BatchV1Api()
    try:
        batch.delete_namespaced_job(name, NAMESPACE, propagation_policy="Background")
    except ApiException as exc:
        if exc.status != 404:
            raise
    batch.create_namespaced_job(NAMESPACE, PIPELINE_JOBS[name])


# ── Deep learning (optional overlay) ─────────────────────────────────────────
# One Job per deep_learning scenario, mirroring k8s/jobs/dl-training-job.yaml with its
# __JOB_NAME__/__SCENARIOS__ placeholders filled in (tests/test_k8s_jobs.py renders
# the YAML the same way `make k3s-dl-train` does and compares). Unlike PIPELINE_JOBS
# these are built per request: the scenario is a parameter, and whether to request a
# GPU depends on what the cluster's nodes advertise at trigger time.

DL_TRAINING_IMAGE = "ai-circus/dl-training:local"
GPU_RESOURCE = "nvidia.com/gpu"
# k3s creates this RuntimeClass automatically once the NVIDIA container runtime is
# installed on the node — see k8s/README.md "Deep learning".
GPU_RUNTIME_CLASS = "nvidia"
_DL_CACHE_VOLUME = "cache"


def dl_job_name(scenario_slug: str) -> str:
    """Kubernetes-safe Job name for one deep_learning scenario (underscores aren't allowed)."""
    return f"dl-training-{scenario_slug.replace('_', '-')}"


def dl_training_job(scenario_slug: str, *, gpu: bool) -> client.V1Job:
    """The per-scenario dl-training Job — requests one GPU only when `gpu` is set."""
    container = _container("dl-training", DL_TRAINING_IMAGE, "dl-training-secrets")
    container.env = [
        client.V1EnvVar(name="SCENARIOS", value=scenario_slug),
        client.V1EnvVar(name="DL_DEVICE", value="auto"),
    ]
    limits = {"cpu": "4", "memory": "4Gi"}
    if gpu:
        # CUDA torch keeps ~1.5-2 GB of runtime in host RAM during the ONNX export.
        limits |= {"memory": "6Gi", GPU_RESOURCE: "1"}
    container.resources = client.V1ResourceRequirements(requests={"cpu": "500m", "memory": "1Gi"}, limits=limits)
    container.volume_mounts = [
        *(container.volume_mounts or []),
        client.V1VolumeMount(name=_DL_CACHE_VOLUME, mount_path="/tmp/dl-cache"),  # ruff: ignore[hardcoded-temp-file]
    ]
    job = _job(dl_job_name(scenario_slug), container)
    labels = {"app": "dl-training", "scenario": scenario_slug}
    metadata = cast("client.V1ObjectMeta", job.metadata)
    metadata.labels = labels
    spec = cast("client.V1JobSpec", job.spec)
    template = cast("client.V1PodTemplateSpec", spec.template)
    template.metadata = client.V1ObjectMeta(labels=labels)
    pod = cast("client.V1PodSpec", template.spec)
    pod.volumes = [
        *(pod.volumes or []),
        client.V1Volume(name=_DL_CACHE_VOLUME, empty_dir=client.V1EmptyDirVolumeSource(size_limit="4Gi")),
    ]
    if gpu:
        pod.runtime_class_name = GPU_RUNTIME_CLASS
    return job


@dataclass(frozen=True)
class NodeGpus:
    """How many NVIDIA GPUs one node advertises as allocatable."""

    name: str
    gpus: int


def cluster_gpus() -> list[NodeGpus]:
    """Every node with its allocatable `nvidia.com/gpu` count (0 without the NVIDIA
    device plugin — the default on k3d).
    """
    _load_config()
    nodes = cast("client.V1NodeList", client.CoreV1Api().list_node())
    result = []
    for node in nodes.items or []:
        allocatable = (node.status.allocatable if node.status else None) or {}
        try:
            count = int(allocatable.get(GPU_RESOURCE, "0"))
        except ValueError:
            count = 0
        result.append(NodeGpus(name=str(node.metadata.name if node.metadata else "?"), gpus=count))
    return result


def trigger_dl_training(scenario_slug: str) -> bool:
    """Delete any previous run of this scenario's Job and start a fresh one.

    Returns:
        Whether the new Job requested a GPU.
    """
    gpu = sum(n.gpus for n in cluster_gpus()) > 0
    _load_config()
    batch = client.BatchV1Api()
    name = dl_job_name(scenario_slug)
    try:
        batch.delete_namespaced_job(name, NAMESPACE, propagation_policy="Background")
        _wait_until_gone(batch, name)
    except ApiException as exc:
        if exc.status != 404:
            raise
    batch.create_namespaced_job(NAMESPACE, dl_training_job(scenario_slug, gpu=gpu))
    return gpu


def _wait_until_gone(batch: client.BatchV1Api, name: str, timeout_seconds: float = 15.0) -> None:
    """Deletion is asynchronous — recreating the same Job name too early is a 409."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            batch.read_namespaced_job(name, NAMESPACE)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise
        time.sleep(0.5)
