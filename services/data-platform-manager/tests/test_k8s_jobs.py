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
