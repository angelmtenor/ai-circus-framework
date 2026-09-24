"""Tests for core.k8s_jobs — in particular a drift check against the real
k8s/jobs/*.yaml files this module's PIPELINE_JOBS is meant to mirror (see that
module's docstring on why this is a documented, tested hand-sync rather than a
runtime YAML load). If someone edits one without the other, this test — not a
silent production mismatch — is what catches it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data_platform_manager.core import k8s_jobs

_K8S_JOBS_DIR = Path(__file__).parents[3] / "k8s" / "jobs"


def _load_yaml_job(filename: str) -> dict:
    with (_K8S_JOBS_DIR / filename).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize(
    ("job_name", "yaml_file"),
    [
        ("etl-tabular", "etl-tabular-job.yaml"),
        ("training", "training-job.yaml"),
        ("etl-vectorize", "etl-vectorize-job.yaml"),
    ],
)
def test_job_spec_matches_the_real_k8s_jobs_yaml(job_name: str, yaml_file: str) -> None:
    yaml_job = _load_yaml_job(yaml_file)
    py_job = k8s_jobs.PIPELINE_JOBS[job_name]
    py_container = py_job.spec.template.spec.containers[0]
    yaml_container = yaml_job["spec"]["template"]["spec"]["containers"][0]

    assert py_job.metadata.name == yaml_job["metadata"]["name"]
    assert py_job.metadata.namespace == yaml_job["metadata"]["namespace"]
    assert py_job.spec.backoff_limit == yaml_job["spec"]["backoffLimit"]
    assert py_container.name == yaml_container["name"]
    assert py_container.image == yaml_container["image"]
    assert py_container.image_pull_policy == yaml_container["imagePullPolicy"]

    yaml_secret_refs = {ef["secretRef"]["name"] for ef in yaml_container["envFrom"] if "secretRef" in ef}
    py_secret_refs = {ef.secret_ref.name for ef in py_container.env_from if ef.secret_ref is not None}
    assert py_secret_refs == yaml_secret_refs


def test_etl_vectorize_forwards_llm_gateway_api_key_from_the_same_secret_as_yaml() -> None:
    yaml_job = _load_yaml_job("etl-vectorize-job.yaml")
    yaml_env = yaml_job["spec"]["template"]["spec"]["containers"][0]["env"]
    yaml_entry = next(e for e in yaml_env if e["name"] == "LLM_GATEWAY_API_KEY")

    py_container = k8s_jobs.PIPELINE_JOBS["etl-vectorize"].spec.template.spec.containers[0]
    py_entry = py_container.env[0]

    assert py_entry.name == "LLM_GATEWAY_API_KEY"
    assert py_entry.value_from.secret_key_ref.name == yaml_entry["valueFrom"]["secretKeyRef"]["name"]
    assert py_entry.value_from.secret_key_ref.key == yaml_entry["valueFrom"]["secretKeyRef"]["key"]


def test_in_cluster_config_available_is_false_outside_a_pod() -> None:
    # This test suite never runs inside a real k8s pod, so the ServiceAccount
    # token path this checks for genuinely doesn't exist — exercising the real
    # filesystem check, not a mock of it.
    assert k8s_jobs.in_cluster_config_available() is False


def _rendered_dl_job(slug: str) -> dict:
    """k8s/jobs/dl-training-job.yaml with its placeholders filled exactly like `make k3s-dl-train`."""
    text = (_K8S_JOBS_DIR / "dl-training-job.yaml").read_text(encoding="utf-8")
    text = text.replace("__JOB_NAME__", k8s_jobs.dl_job_name(slug)).replace("__SCENARIOS__", slug)
    return yaml.safe_load(text)


def test_dl_training_job_matches_the_rendered_yaml_template() -> None:
    yaml_job = _rendered_dl_job("chest_xray_pneumonia")
    py_job = k8s_jobs.dl_training_job("chest_xray_pneumonia", gpu=False)
    yaml_pod = yaml_job["spec"]["template"]["spec"]
    yaml_container = yaml_pod["containers"][0]
    py_container = py_job.spec.template.spec.containers[0]

    assert py_job.metadata.name == yaml_job["metadata"]["name"] == "dl-training-chest-xray-pneumonia"
    assert py_job.metadata.labels == yaml_job["metadata"]["labels"]
    assert py_job.spec.template.metadata.labels == yaml_job["spec"]["template"]["metadata"]["labels"]
    assert py_job.spec.backoff_limit == yaml_job["spec"]["backoffLimit"]
    assert py_container.image == yaml_container["image"]
    assert {e.name: e.value for e in py_container.env} == {e["name"]: e["value"] for e in yaml_container["env"]}
    assert py_container.resources.limits == yaml_container["resources"]["limits"]
    assert py_container.resources.requests == yaml_container["resources"]["requests"]
    assert {(m.name, m.mount_path) for m in py_container.volume_mounts} == {
        (m["name"], m["mountPath"]) for m in yaml_container["volumeMounts"]
    }
    assert {v.name for v in py_job.spec.template.spec.volumes} == {v["name"] for v in yaml_pod["volumes"]}
    yaml_secret_refs = {ef["secretRef"]["name"] for ef in yaml_container["envFrom"] if "secretRef" in ef}
    assert {ef.secret_ref.name for ef in py_container.env_from if ef.secret_ref is not None} == yaml_secret_refs
    assert py_job.spec.template.spec.runtime_class_name is None


def test_dl_training_job_requests_a_gpu_only_when_asked() -> None:
    job = k8s_jobs.dl_training_job("symptom_triage", gpu=True)
    container = job.spec.template.spec.containers[0]
    assert container.resources.limits[k8s_jobs.GPU_RESOURCE] == "1"
    assert container.resources.limits["memory"] == "6Gi"
    assert job.spec.template.spec.runtime_class_name == "nvidia"
    assert (
        k8s_jobs.GPU_RESOURCE
        not in k8s_jobs.dl_training_job("symptom_triage", gpu=False).spec.template.spec.containers[0].resources.limits
    )


def test_cluster_gpus_reads_node_allocatable(monkeypatch: pytest.MonkeyPatch) -> None:
    from kubernetes import client

    nodes = client.V1NodeList(
        items=[
            client.V1Node(
                metadata=client.V1ObjectMeta(name="cpu-node"), status=client.V1NodeStatus(allocatable={"cpu": "8"})
            ),
            client.V1Node(
                metadata=client.V1ObjectMeta(name="gpu-node"),
                status=client.V1NodeStatus(allocatable={"cpu": "8", "nvidia.com/gpu": "2"}),
            ),
        ]
    )
    monkeypatch.setattr(k8s_jobs, "_load_config", lambda: None)
    monkeypatch.setattr(client.CoreV1Api, "list_node", lambda self: nodes)

    assert k8s_jobs.cluster_gpus() == [k8s_jobs.NodeGpus("cpu-node", 0), k8s_jobs.NodeGpus("gpu-node", 2)]
