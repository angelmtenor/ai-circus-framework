"""
- Title:    Tests for the architecture catalog: complete, generated files in sync, scenario subsets
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ai_circus_shared.architecture import Catalog, load_catalog, scenario_architecture
from ai_circus_shared.scenario_schema import load_all

ROOT = Path(__file__).resolve().parents[3]
WORKLOAD_KINDS = {"Deployment", "StatefulSet", "Job", "CronJob", "DaemonSet"}


def _manifest_workloads() -> set[str]:
    names: set[str] = set()
    for folder in (ROOT / "k8s", ROOT / "observability" / "bundle"):
        for path in folder.rglob("*.yaml"):
            for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if isinstance(doc, dict) and doc.get("kind") in WORKLOAD_KINDS:
                    names.add(doc["metadata"]["name"])
    return names


def _generator():  # the script, imported as a module
    spec = importlib.util.spec_from_file_location(
        "generate_architecture", ROOT / "scripts" / "generate_architecture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_architecture"] = module
    spec.loader.exec_module(module)
    return module


def test_every_workload_in_the_manifests_is_in_the_catalog_and_back() -> None:
    catalog = load_catalog()
    listed = {w for c in catalog.components for w in c.workloads}
    deployed = _manifest_workloads()
    assert deployed - listed == set(), "add these workloads to architecture_catalog.yaml"
    assert listed - deployed == set(), "these catalog workloads no longer exist under k8s/ or observability/bundle/"


def test_the_generated_files_match_the_catalog() -> None:
    drifted = [
        p.relative_to(ROOT).as_posix()
        for p, text in _generator().expected(ROOT).items()
        if p.read_text(encoding="utf-8") != text
    ]
    assert drifted == [], "run: libs/shared/.venv/bin/python scripts/generate_architecture.py"


def test_the_catalog_rejects_a_link_or_flow_to_nowhere() -> None:
    data = load_catalog().model_dump(by_alias=True)
    data["components"][0]["talks_to"] = [{"to": "nowhere", "label": "x"}]
    with pytest.raises(ValidationError, match="unknown component"):
        Catalog.model_validate(data)
    data = load_catalog().model_dump(by_alias=True)
    data["flows"][0]["steps"][0]["from"] = "nowhere"
    with pytest.raises(ValidationError, match="unknown component"):
        Catalog.model_validate(data)


def test_every_scenario_gets_a_subset_with_reasons_from_its_own_yaml() -> None:
    catalog = load_catalog()
    ids = {c.id for c in catalog.components}
    by_slug = {d.slug: d for d in load_all(ROOT / "scenarios")}
    for definition in by_slug.values():
        view = scenario_architecture(definition)
        assert set(view["components"]) <= ids and view["components"]
        assert set(view["reasons"]) == set(view["components"])
        assert {"traefik", "keycloak", "platform-registry", "ui-react"} <= set(view["components"])
    water = scenario_architecture(by_slug["water_reservoirs_drought"])
    assert "drought watch" in water["reasons"]["assistant"] and "predict" in water["flows"] and "chat" in water["flows"]
    fraud = scenario_architecture(by_slug["financial_crime_desk"])
    assert {"qdrant", "etl-vectorize"} <= set(fraud["components"]) and "3 models" in fraud["reasons"]["prediction"]
    reference = scenario_architecture(by_slug["ai_circus_reference"])
    assert {"rag-agent", "qdrant", "chat-ui"} <= set(reference["components"]) and "rag" in reference["flows"]
    surf = scenario_architecture(by_slug["surf_retail"])
    assert {"kafka", "data-ingest", "data-api"} <= set(surf["components"]) and "data-product" in surf["flows"]
    churn = scenario_architecture(by_slug["churn"])
    assert "qdrant" not in churn["components"] and "kafka" not in churn["components"]
