"""
- Title:    AI-BOM: the inventory of AI systems, models and datasets as a CycloneDX 1.6 BOM
- Author:   Angel Martinez-Tenor

`GET /governance/inventory?format=cyclonedx` answers "what AI is running here?" in a format an
external governance tool already reads (CycloneDX ML-BOM, spec 1.6):

- each **scenario** is an `application` component carrying its declared governance block as
  `aicf:*` properties (owner, purpose, EU AI Act role / risk category, human oversight, data
  categories) — declared by the owner, never inferred (see ai_circus_shared.governance);
- each **gateway route** (chat and embedding aliases, resolved to provider and model id) is a
  `machine-learning-model` component;
- each **trained model** (tabular and deep learning, per tenant that has one) is a
  `machine-learning-model` component with a `modelCard`: task, architecture, holdout metrics,
  and the scenario's declared use case, limitations and known risks;
- each scenario's **dataset** (or document corpus) is a `data` component, with its attribution
  and a personal-data classification when the owner declared one.

Dependencies link a scenario to its datasets and models. Content-free: names, ids, versions,
declared metadata and aggregate metrics — never records, prompts, or keys.

This module is pure (`build_bom`) plus two best-effort collectors; platform-registry's endpoint
wires them to the gateway and the object store. A collector that fails marks that part of the BOM
`unavailable` or `partial` in `metadata.properties` instead of failing the whole export.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_circus_shared.decision_records import model_version
from ai_circus_shared.deep_learning import DL_METADATA_KEY
from ai_circus_shared.governance import resolve_governance
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import MODEL_METADATA_KEY

from platform_registry import __version__, get_logger

logger = get_logger(__name__)

SPEC_VERSION = "1.6"
MEDIA_TYPE = "application/vnd.cyclonedx+json; version=1.6"
_NS = "aicf:"


@dataclass(frozen=True)
class GatewayRoute:
    """One alias llm-gateway serves, resolved to the provider and model behind it."""

    alias: str
    provider: str | None
    model: str | None


@dataclass(frozen=True)
class TrainedModel:
    """A model one tenant has trained for one scenario, as its stored metadata describes it."""

    org_id: str
    scenario_slug: str
    metadata: Mapping[str, Any]


def _properties(pairs: Iterable[tuple[str, object]]) -> list[dict[str, str]]:
    """CycloneDX `properties` (namespaced name/value strings); None values are left out."""
    out: list[dict[str, str]] = []
    for name, value in pairs:
        if value is None or value == "":
            continue
        text = ("true" if value else "false") if isinstance(value, bool) else str(value).strip()
        if text:
            out.append({"name": f"{_NS}{name}", "value": text})
    return out


def _metric_value(value: float) -> str:
    return f"{value:.6g}"


def _scenario_component(definition: ScenarioDefinition) -> dict[str, Any]:
    record = resolve_governance(definition.slug, definition.governance)
    ai_act = record["ai_act"]
    oversight = record["human_oversight"]
    data = record["data"]
    pairs: list[tuple[str, object]] = [
        ("ai_system_id", record["ai_system_id"]),
        ("title", definition.title),
        ("scenario_kind", definition.kind),
        ("industry", definition.industry),
        ("governance_declared", record["declared"]),
        ("owner", record["owner"]),
        ("intended_purpose", record["intended_purpose"]),
        ("ai_act_role", ai_act["role"]),
        ("ai_act_risk_category", ai_act["risk_category"]),
        ("ai_act_annex_iii_area", ai_act["annex_iii_area"]),
        ("human_oversight_mode", oversight["mode"]),
        ("human_oversight", oversight["description"]),
        ("personal_data", data["personal_data"]),
        ("special_categories", data["special_categories"]),
    ]
    pairs += [("limitation", item) for item in record["limitations"]]
    pairs += [("known_risk", item) for item in record["known_risks"]]
    return {
        "type": "application",
        "bom-ref": f"scenario:{definition.slug}",
        "name": definition.slug,
        "description": " ".join(definition.description.split()),
        "properties": _properties(pairs),
    }


def _dataset_component(definition: ScenarioDefinition) -> dict[str, Any] | None:
    """The scenario's training data (or document corpus) as a `data` component, if it has any."""
    record = resolve_governance(definition.slug, definition.governance)
    data = record["data"]
    if definition.dataset is not None:
        kind, bucket = "dataset", definition.dataset.bucket
        facts: list[tuple[str, object]] = [
            ("bucket", bucket),
            ("target", definition.dataset.target),
            ("feature_count", len(definition.dataset.feature_columns)),
        ]
    elif definition.deep_learning is not None:
        kind, bucket = "dataset", definition.deep_learning.bucket
        facts = [
            ("bucket", bucket),
            ("modality", definition.deep_learning.modality),
            ("task", definition.deep_learning.task),
            ("label_count", len(definition.deep_learning.labels)),
            ("source_type", definition.deep_learning.source.type),
        ]
    elif definition.documents is not None:
        kind, bucket = "documents", definition.documents.bucket
        facts = [("bucket", bucket), ("raw_prefix", definition.documents.raw_prefix)]
    else:
        return None

    entry: dict[str, Any] = {
        "type": "dataset",
        "name": f"{definition.slug}-{kind}",
        "description": f"Data behind the {definition.slug} scenario",
    }
    if data["personal_data"] is not None:
        entry["classification"] = "personal data" if data["personal_data"] else "no personal data"
    if data["special_categories"]:
        entry["sensitiveData"] = ["special categories of personal data"]
    contents: dict[str, Any] = {"properties": _properties(facts + [("declared_dataset", d) for d in data["datasets"]])}
    attribution = definition.credits
    if attribution is not None:
        contents["properties"] += _properties([("credit_source", attribution.source)])
        if attribution.url.startswith(("http://", "https://")):
            contents["url"] = attribution.url
    entry["contents"] = contents
    return {
        "type": "data",
        "bom-ref": f"dataset:{definition.slug}",
        "name": f"{definition.slug}-{kind}",
        "data": [entry],
    }


def _llm_component(route: GatewayRoute) -> dict[str, Any]:
    is_embedding = "embed" in route.alias or (route.provider or "").endswith("embed")
    component: dict[str, Any] = {
        "type": "machine-learning-model",
        "bom-ref": f"llm:{route.alias}",
        "name": route.alias,
        "modelCard": {
            "modelParameters": {"task": "feature-extraction" if is_embedding else "text-generation"},
        },
        "properties": _properties([("provider", route.provider), ("route_model", route.model), ("via", "llm-gateway")]),
    }
    if route.provider:
        component["publisher"] = route.provider
    return component


def _trained_component(model: TrainedModel, definition: ScenarioDefinition | None) -> dict[str, Any]:
    meta = model.metadata
    architecture = str(meta.get("base_model") or meta.get("model_name") or "unknown")
    task = str(meta.get("task_type") or meta.get("task") or "classification")
    metrics = {
        name: float(value)
        for name, value in (meta.get("metrics") or {}).items()
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    }
    if (
        isinstance(meta.get("test_score"), int | float)
        and math.isfinite(meta["test_score"])
        and "test_score" not in metrics
    ):
        metrics["test_score"] = float(meta["test_score"])
    unsupervised = "anomaly" in task
    card: dict[str, Any] = {
        "modelParameters": {
            "approach": {"type": "unsupervised" if unsupervised else "supervised"},
            "task": task,
            "modelArchitecture": architecture,
        },
        "quantitativeAnalysis": {
            "performanceMetrics": [
                {"type": name, "value": _metric_value(value)} for name, value in sorted(metrics.items())
            ]
        },
    }
    if definition is not None and (definition.dataset is not None or definition.deep_learning is not None):
        card["modelParameters"]["datasets"] = [{"ref": f"dataset:{model.scenario_slug}"}]
    if definition is not None:
        record = resolve_governance(definition.slug, definition.governance)
        considerations: dict[str, Any] = {}
        if record["intended_purpose"]:
            considerations["useCases"] = [record["intended_purpose"]]
        if record["limitations"]:
            considerations["technicalLimitations"] = list(record["limitations"])
        if record["known_risks"]:
            considerations["ethicalConsiderations"] = [{"name": risk} for risk in record["known_risks"]]
        if considerations:
            card["considerations"] = considerations
    return {
        "type": "machine-learning-model",
        "bom-ref": f"model:{model.scenario_slug}:{model.org_id}",
        "name": f"{model.scenario_slug}-{architecture.split('/')[-1]}",
        "version": model_version(meta),
        "modelCard": card,
        "properties": _properties([
            ("tenant", model.org_id),
            ("scenario", model.scenario_slug),
            ("selection_metric", meta.get("selection_metric")),
            ("training_rows", meta.get("training_rows")),
            ("holdout_rows", meta.get("holdout_rows")),
            ("modality", meta.get("modality")),
        ]),
    }


def build_bom(
    definitions: Sequence[ScenarioDefinition],
    *,
    routes: Sequence[GatewayRoute] | None,
    trained: Sequence[TrainedModel] | None,
    trained_complete: bool = True,
    now: datetime | None = None,
    serial_number: str | None = None,
) -> dict[str, Any]:
    """The whole BOM. `routes` / `trained` of None mean "could not be collected" and are flagged
    `unavailable` in `metadata.properties` (a partial trained-model list is flagged `partial`).
    """
    by_slug = {definition.slug: definition for definition in definitions}
    components: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    models_by_slug: dict[str, list[str]] = {}

    for model in trained or []:
        models_by_slug.setdefault(model.scenario_slug, []).append(f"model:{model.scenario_slug}:{model.org_id}")

    for definition in sorted(definitions, key=lambda d: d.slug):
        components.append(_scenario_component(definition))
        depends: list[str] = []
        dataset = _dataset_component(definition)
        if dataset is not None:
            components.append(dataset)
            depends.append(dataset["bom-ref"])
        depends += sorted(models_by_slug.get(definition.slug, []))
        dependencies.append({"ref": f"scenario:{definition.slug}", "dependsOn": depends})

    for route in sorted(routes or [], key=lambda r: r.alias):
        components.append(_llm_component(route))
    known = {c["bom-ref"] for c in components}
    for model in sorted(trained or [], key=lambda m: (m.scenario_slug, m.org_id)):
        component = _trained_component(model, by_slug.get(model.scenario_slug))
        if component["bom-ref"] in known:
            continue
        components.append(component)
        known.add(component["bom-ref"])

    moment = now or datetime.now(UTC)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{serial_number or uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": moment.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "tools": {"components": [{"type": "application", "name": "platform-registry", "version": __version__}]},
            "component": {"type": "platform", "bom-ref": "platform:ai-circus-framework", "name": "ai-circus-framework"},
            "properties": _properties([
                ("inventory_scenarios", len(definitions)),
                ("inventory_llm_routes", "unavailable" if routes is None else "available"),
                (
                    "inventory_trained_models",
                    "unavailable" if trained is None else ("available" if trained_complete else "partial"),
                ),
            ]),
        },
        "components": components,
        "dependencies": dependencies,
    }


def collect_routes(fetch: Callable[[], list[dict[str, str | None]]]) -> list[GatewayRoute] | None:
    """The gateway's routes, or None when the gateway cannot be asked."""
    try:
        return [GatewayRoute(str(r["alias"]), r.get("provider"), r.get("model")) for r in fetch()]
    except Exception as exc:
        logger.warning("AI-BOM: gateway routes unavailable ({})", type(exc).__name__)
        return None


def collect_trained_models(
    definitions: Iterable[ScenarioDefinition], open_store: Callable[[str], ObjectStore]
) -> tuple[list[TrainedModel], bool]:
    """Every (tenant, scenario) model with stored metadata, read from each scenario's own bucket.

    Returns `(models, complete)`; `complete` is False if any scenario's bucket could not be read
    (a bucket that was never written to is not a failure — it simply has no models).
    """
    models: list[TrainedModel] = []
    complete = True
    for definition in definitions:
        if definition.dataset is not None:
            bucket, key = definition.dataset.bucket, MODEL_METADATA_KEY
        elif definition.deep_learning is not None:
            bucket, key = definition.deep_learning.bucket, DL_METADATA_KEY
        else:
            continue
        try:
            store = open_store(bucket)
            for org_id in store.tenants():
                if store.exists(org_id, key):
                    models.append(TrainedModel(org_id, definition.slug, json.loads(store.get(org_id, key))))
        except Exception as exc:
            if type(exc).__name__ in {"NoSuchBucket", "ClientError"} and "NoSuchBucket" in str(exc):
                continue  # never trained: no bucket, no models
            logger.warning("AI-BOM: models of {} unavailable ({})", definition.slug, type(exc).__name__)
            complete = False
    return models, complete
