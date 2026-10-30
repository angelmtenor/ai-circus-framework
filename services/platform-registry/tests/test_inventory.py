"""Tests for the CycloneDX AI-BOM: schema validity over the repo's real scenarios, coverage, collectors
and the /governance/inventory endpoint.
"""

from __future__ import annotations

import json
import math
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from ai_circus_shared.scenario_schema import ScenarioDefinition, load_all
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
from fastapi.testclient import TestClient

from platform_registry import governance_api
from platform_registry.api import require_admin
from platform_registry.app import app
from platform_registry.core import governance, inventory, llm_settings
from platform_registry.core.inventory import GatewayRoute, TrainedModel
from tests.conftest import FakeSecret

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "scenarios"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def definitions() -> list[ScenarioDefinition]:
    return load_all(SCENARIOS_DIR)


ROUTES = [
    GatewayRoute("groq-llama", "groq", "llama-3.3-70b-versatile"),
    GatewayRoute("local-embed", "local-embed", "voyageai/voyage-4-nano"),
    GatewayRoute("gemini-embedding-001", "gemini", "gemini-embedding-001"),
]
TABULAR_META = {
    "scenario_slug": "prestaciones_sociales",
    "model_name": "lightgbm",
    "task_type": "classification",
    "test_score": 0.91,
    "metrics": {"roc_auc": 0.93, "accuracy": 0.88, "f1": float("nan")},
    "selection_metric": "roc_auc",
    "training_rows": 800,
    "holdout_rows": 200,
    "checksums": {"model.joblib": "aa", "metadata.json": "bb"},
}
DL_META = {
    "base_model": "facebook/dinov2-small",
    "modality": "image",
    "task": "classification",
    "checksums": {"m": "cc"},
}


def _validate(bom: dict[str, Any]) -> None:
    errors = JsonStrictValidator(SchemaVersion.V1_6).validate_str(json.dumps(bom))
    assert errors is None, f"not a valid CycloneDX 1.6 document: {errors}"


def _bom(definitions: list[ScenarioDefinition], **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "routes": ROUTES,
        "trained": [
            TrainedModel("admin", "prestaciones_sociales", TABULAR_META),
            TrainedModel("demo", "prestaciones_sociales", TABULAR_META),
            TrainedModel("admin", "pcb_visual_inspection", DL_META),
        ],
        "now": NOW,
        "serial_number": "11111111-2222-3333-4444-555555555555",
    }
    return inventory.build_bom(definitions, **(args | overrides))


def _by_ref(bom: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["bom-ref"]: c for c in bom["components"]}


def _props(component: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for prop in component.get("properties", []):
        out.setdefault(prop["name"], []).append(prop["value"])
    return out


def test_the_bom_is_a_valid_cyclonedx_1_6_document_for_every_real_scenario(
    definitions: list[ScenarioDefinition],
) -> None:
    bom = _bom(definitions)
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.6"
    assert bom["serialNumber"] == "urn:uuid:11111111-2222-3333-4444-555555555555"
    _validate(bom)


def test_every_seeded_scenario_is_an_application_component_with_its_ai_system_id(
    definitions: list[ScenarioDefinition],
) -> None:
    refs = _by_ref(_bom(definitions))
    assert len(definitions) >= 30
    for definition in definitions:
        component = refs[f"scenario:{definition.slug}"]
        assert component["type"] == "application"
        assert _props(component)["aicf:ai_system_id"][0] == definition.slug
        assert _props(component)["aicf:scenario_kind"] == [definition.kind]


def test_bom_refs_are_unique_and_every_reference_resolves(definitions: list[ScenarioDefinition]) -> None:
    bom = _bom(definitions)
    refs = [c["bom-ref"] for c in bom["components"]]
    assert len(refs) == len(set(refs))
    known = set(refs) | {bom["metadata"]["component"]["bom-ref"]}
    for dependency in bom["dependencies"]:
        assert dependency["ref"] in known and set(dependency["dependsOn"]) <= known
    for component in bom["components"]:
        for dataset in component.get("modelCard", {}).get("modelParameters", {}).get("datasets", []):
            assert dataset["ref"] in known


def test_the_declared_governance_block_is_exported_as_properties(definitions: list[ScenarioDefinition]) -> None:
    refs = _by_ref(_bom(definitions))
    declared = _props(refs["scenario:prestaciones_sociales"])
    assert declared["aicf:governance_declared"] == ["true"]
    assert declared["aicf:ai_act_risk_category"] == ["high"]
    assert declared["aicf:ai_act_annex_iii_area"] == ["essential_services"]
    assert declared["aicf:human_oversight_mode"] == ["approve"]
    assert declared["aicf:personal_data"] == ["true"] and declared["aicf:special_categories"] == ["false"]
    assert declared["aicf:limitation"] and declared["aicf:known_risk"]

    undeclared = _props(refs["scenario:churn"])
    assert undeclared["aicf:governance_declared"] == ["false"]
    assert undeclared["aicf:ai_act_risk_category"] == ["unassessed"], "never auto-classified"
    assert "aicf:ai_act_role" not in undeclared


def test_gateway_routes_become_model_components_with_the_resolved_provider(
    definitions: list[ScenarioDefinition],
) -> None:
    refs = _by_ref(_bom(definitions))
    chat = refs["llm:groq-llama"]
    assert chat["type"] == "machine-learning-model" and chat["publisher"] == "groq"
    assert chat["modelCard"]["modelParameters"]["task"] == "text-generation"
    assert _props(chat)["aicf:route_model"] == ["llama-3.3-70b-versatile"]
    assert refs["llm:local-embed"]["modelCard"]["modelParameters"]["task"] == "feature-extraction"
    assert refs["llm:gemini-embedding-001"]["modelCard"]["modelParameters"]["task"] == "feature-extraction"
    assert not any("api_base" in json.dumps(c) for c in refs.values()), "internal URLs are not inventory"


def test_trained_models_carry_a_model_card_with_metrics_and_the_declared_limits(
    definitions: list[ScenarioDefinition],
) -> None:
    refs = _by_ref(_bom(definitions))
    admin = refs["model:prestaciones_sociales:admin"]
    assert admin["type"] == "machine-learning-model" and len(admin["version"]) == 12
    card = admin["modelCard"]
    assert card["modelParameters"] == {
        "approach": {"type": "supervised"},
        "task": "classification",
        "modelArchitecture": "lightgbm",
        "datasets": [{"ref": "dataset:prestaciones_sociales"}],
    }
    metrics = {m["type"]: m["value"] for m in card["quantitativeAnalysis"]["performanceMetrics"]}
    assert metrics["roc_auc"] == "0.93" and metrics["accuracy"] == "0.88" and "test_score" in metrics
    assert "f1" not in metrics, "a non-finite metric is dropped, not exported as a string"
    assert all(math.isfinite(float(value)) for value in metrics.values())
    assert card["considerations"]["useCases"] and card["considerations"]["technicalLimitations"]
    assert card["considerations"]["ethicalConsiderations"][0]["name"]
    assert _props(admin)["aicf:tenant"] == ["admin"] and _props(admin)["aicf:training_rows"] == ["800"]
    assert "model:prestaciones_sociales:demo" in refs, "each tenant's own model is listed"

    dl = refs["model:pcb_visual_inspection:admin"]
    assert dl["modelCard"]["modelParameters"]["modelArchitecture"] == "facebook/dinov2-small"


def test_datasets_are_data_components_with_attribution_and_a_personal_data_classification(
    definitions: list[ScenarioDefinition],
) -> None:
    refs = _by_ref(_bom(definitions))
    dataset = refs["dataset:prestaciones_sociales"]
    assert dataset["type"] == "data"
    entry = dataset["data"][0]
    assert entry["type"] == "dataset" and entry["classification"] == "personal data"
    assert entry["contents"]["properties"]
    titanic = refs["dataset:titanic"]["data"][0]
    assert "classification" not in titanic, "nothing declared, nothing claimed"
    assert any(p["name"] == "aicf:credit_source" for p in titanic["contents"]["properties"])
    assert refs["dataset:aml_regulation_kg"]["name"].endswith("-documents")


def test_a_scenario_depends_on_its_dataset_and_its_models(definitions: list[ScenarioDefinition]) -> None:
    deps = {d["ref"]: d["dependsOn"] for d in _bom(definitions)["dependencies"]}
    assert deps["scenario:prestaciones_sociales"] == [
        "dataset:prestaciones_sociales",
        "model:prestaciones_sociales:admin",
        "model:prestaciones_sociales:demo",
    ]
    assert deps["scenario:churn"] == ["dataset:churn"]


def test_uncollectable_parts_are_flagged_not_silently_omitted(definitions: list[ScenarioDefinition]) -> None:
    flags = lambda bom: _props({"properties": bom["metadata"]["properties"]})  # ruff: ignore[lambda-assignment]

    ok = flags(_bom(definitions))
    assert ok["aicf:inventory_llm_routes"] == ["available"] and ok["aicf:inventory_trained_models"] == ["available"]

    down = _bom(definitions, routes=None, trained=None)
    assert flags(down)["aicf:inventory_llm_routes"] == ["unavailable"]
    assert flags(down)["aicf:inventory_trained_models"] == ["unavailable"]
    assert not any(c["bom-ref"].startswith(("llm:", "model:")) for c in down["components"])
    _validate(down)

    partial = _bom(definitions, trained_complete=False)
    assert flags(partial)["aicf:inventory_trained_models"] == ["partial"]


def test_the_bom_carries_no_secrets_or_records(definitions: list[ScenarioDefinition]) -> None:
    text = json.dumps(_bom(definitions)).lower()
    for forbidden in ("api_key", "secret", "password", "authorization", "bearer "):
        assert forbidden not in text


# --- collectors --------------------------------------------------------------------------------


class FakeStore:
    """An in-memory bucket: {org: {key: bytes}}."""

    def __init__(self, tenants: dict[str, dict[str, bytes]]) -> None:
        """Hold the tenants' objects."""
        self._tenants = tenants

    def tenants(self) -> list[str]:
        """The org ids with objects."""
        return sorted(self._tenants)

    def exists(self, org_id: str, key: str) -> bool:
        """Whether the object exists."""
        return key in self._tenants[org_id]

    def get(self, org_id: str, key: str) -> bytes:
        """The object's bytes."""
        return self._tenants[org_id][key]


def test_collect_reads_each_scenarios_own_bucket_for_tenants_that_have_a_model(
    definitions: list[ScenarioDefinition],
) -> None:
    opened: list[str] = []

    def open_store(bucket: str) -> FakeStore:
        opened.append(bucket)
        meta = json.dumps(TABULAR_META).encode()
        return FakeStore({"admin": {"model/metadata.json": meta}, "nomodel": {"datasets/x.csv": b"x"}})

    only = [d for d in definitions if d.slug in {"churn", "pcb_visual_inspection", "aml_regulation_kg"}]
    models, complete = inventory.collect_trained_models(only, open_store)

    assert complete and sorted((m.scenario_slug, m.org_id) for m in models) == [
        ("churn", "admin"),
        ("pcb_visual_inspection", "admin"),
    ]
    assert len(opened) == 2, "RAG scenarios have no trained model and are not even opened"


def test_a_never_trained_bucket_is_not_a_failure_but_a_broken_one_marks_the_list_partial(
    definitions: list[ScenarioDefinition],
) -> None:
    class ClientError(Exception):
        pass

    def open_store(bucket: str) -> FakeStore:
        if "churn" in bucket:
            raise ClientError("An error occurred (NoSuchBucket) when calling the ListObjectsV2 operation")
        raise ConnectionError("seaweedfs down")

    churn = [d for d in definitions if d.slug == "churn"]
    assert inventory.collect_trained_models(churn, open_store) == ([], True)
    titanic = [d for d in definitions if d.slug == "titanic"]
    assert inventory.collect_trained_models(titanic, open_store) == ([], False)


def test_collect_routes_returns_none_when_the_gateway_is_down() -> None:
    def down() -> list[dict[str, str | None]]:
        raise llm_settings.LlmGatewayError("unreachable")

    assert inventory.collect_routes(down) is None
    assert inventory.collect_routes(lambda: [{"alias": "a", "provider": "groq", "model": "m"}]) == [
        GatewayRoute("a", "groq", "m")
    ]


def test_list_routes_resolves_aliases_to_provider_and_model_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {"model_name": "groq-llama", "litellm_params": {"model": "groq/llama-3.3", "api_key": "REDACTED"}},
            {"model_name": "local-embed", "litellm_params": {"model": "local-embed/voyageai/voyage-4-nano"}},
            {"model_name": "bare", "litellm_params": {}},
        ]
    }

    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get(self, path: str) -> SimpleNamespace:
            assert path == "/model/info"
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr(llm_settings, "_client", lambda *_a: Client())
    routes = llm_settings.list_routes("http://gw", "key")
    assert routes == [
        {"alias": "bare", "provider": None, "model": None},
        {"alias": "groq-llama", "provider": "groq", "model": "llama-3.3"},
        {"alias": "local-embed", "provider": "local-embed", "model": "voyageai/voyage-4-nano"},
    ]


# --- endpoint ----------------------------------------------------------------------------------


class _Config:
    SCENARIOS_DIR = str(SCENARIOS_DIR)
    LLM_GATEWAY_URL = "http://gw"
    LLM_GATEWAY_API_KEY = FakeSecret("gw-key")
    OBJECT_STORE_ENDPOINT = "http://s3"
    OBJECT_STORE_ACCESS_KEY = "ak"
    OBJECT_STORE_SECRET_KEY = FakeSecret("sk")


class _Enabled:
    GOVERNANCE_INVENTORY_ENABLED = "true"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    app.dependency_overrides[require_admin] = lambda: None
    monkeypatch.setattr(governance_api, "get_env_config", lambda: _Config())
    monkeypatch.setattr(governance, "_config", lambda: _Enabled())
    monkeypatch.setattr(
        llm_settings, "list_routes", lambda *_a: [{"alias": "groq-llama", "provider": "groq", "model": "llama"}]
    )
    meta = json.dumps(TABULAR_META).encode()
    monkeypatch.setattr(
        governance_api, "_open_store", lambda bucket: FakeStore({"admin": {"model/metadata.json": meta}})
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_the_endpoint_serves_a_valid_ai_bom(client: TestClient) -> None:
    response = client.get("/governance/inventory", params={"format": "cyclonedx"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.cyclonedx+json")
    bom = response.json()
    _validate(bom)
    refs = _by_ref(bom)
    assert "llm:groq-llama" in refs and "model:churn:admin" in refs and "scenario:churn" in refs
    flags = _props({"properties": bom["metadata"]["properties"]})
    assert flags["aicf:inventory_llm_routes"] == ["available"]


def test_the_default_format_is_cyclonedx_and_other_formats_are_rejected(client: TestClient) -> None:
    assert client.get("/governance/inventory").status_code == 200
    assert client.get("/governance/inventory", params={"format": "spdx"}).status_code == 422


def test_the_endpoint_does_not_exist_until_enabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class Off:
        GOVERNANCE_INVENTORY_ENABLED = None

    monkeypatch.setattr(governance, "_config", lambda: Off())
    assert client.get("/governance/inventory").status_code == 404


def test_a_gateway_or_object_store_outage_degrades_the_bom_instead_of_failing_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*_a: object) -> list[dict[str, str | None]]:
        raise llm_settings.LlmGatewayError("gateway down")

    monkeypatch.setattr(llm_settings, "list_routes", down)

    class NoStore(_Config):
        OBJECT_STORE_ENDPOINT = None

    monkeypatch.setattr(governance_api, "get_env_config", lambda: NoStore())

    response = client.get("/governance/inventory")

    assert response.status_code == 200
    bom = response.json()
    _validate(bom)
    flags = _props({"properties": bom["metadata"]["properties"]})
    assert flags["aicf:inventory_llm_routes"] == ["unavailable"]
    assert flags["aicf:inventory_trained_models"] == ["unavailable"]
    assert "scenario:churn" in _by_ref(bom), "the scenarios themselves are always inventoried"
