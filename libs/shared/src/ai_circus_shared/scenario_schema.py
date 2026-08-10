"""Pydantic models mirroring `scenarios/*/scenario.yaml`.

`services/platform-registry` is the only service that parses these files directly
(at bootstrap/seed time) — every other service and both UIs resolve scenario
metadata through platform-registry's API (see `entitlements.py`), not the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class NumericFeatureUI(BaseModel):
    """Declarative UI hint for a numeric feature: a bounded slider/number input."""

    type: Literal["numeric"] = "numeric"
    min: float
    max: float
    default: float
    step: float = 1.0


class CategoricalFeatureUI(BaseModel):
    """Declarative UI hint for a categorical feature: a fixed set of options."""

    type: Literal["categorical"] = "categorical"
    options: list[str]
    default: str


FeatureUI = Annotated[NumericFeatureUI | CategoricalFeatureUI, Field(discriminator="type")]


class TabularDataset(BaseModel):
    """Dataset config for a `tabular_ml` scenario."""

    bucket: str
    raw_object: str
    seed_file: str
    index_col: str
    target: str
    protected_features_excluded: list[str] = []
    feature_columns: list[str]
    # Drives both UIs' generic form renderer — keyed by `feature_columns` entries, in
    # the same order, so neither UI needs any scenario-specific form code.
    feature_schema: dict[str, FeatureUI]


class TabularModel(BaseModel):
    """Model-selection config for a `tabular_ml` scenario (Green Code policy)."""

    task_type: Literal["classification", "regression"]
    candidates: list[str]
    accuracy_gain_threshold_for_complexity: float
    explainability: Literal["shap"] = "shap"
    # Display-only unit for a regression target (e.g. "days") — None for classification.
    target_units: str | None = None


class TabularServices(BaseModel):
    """Names of the services that implement a `tabular_ml` scenario."""

    etl: str
    training: str
    prediction: str
    assistant: str


class DocumentChunking(BaseModel):
    """Chunking config for a `conversational_rag` scenario."""

    strategy: str
    chunk_size: int
    chunk_overlap: int


class DocumentEmbedding(BaseModel):
    """Embedding model config for a `conversational_rag` scenario."""

    model: str


class GithubDocsSource(BaseModel):
    """Fetch seed documents straight from a public GitHub repo folder at bootstrap
    time, instead of a locally tracked `seed_prefix` folder — for reference docs that
    should stay in sync with an upstream repo rather than being copy-pasted into this one.
    """

    repo: str  # "owner/name"
    path: str  # folder path within the repo, e.g. "reference"
    ref: str = "main"


class DocumentsConfig(BaseModel):
    """Source-document config for a `conversational_rag` scenario.

    Exactly one of `seed_prefix` (a tracked local folder, relative to the scenario's
    directory) or `github_source` (a public GitHub repo folder) provides the demo
    bootstrap documents — see `etl_vectorize.core.vectorize.ensure_raw_docs`.
    """

    bucket: str
    raw_prefix: str
    seed_prefix: str | None = None
    github_source: GithubDocsSource | None = None
    chunking: DocumentChunking
    embedding: DocumentEmbedding

    @model_validator(mode="after")
    def _exactly_one_seed_source(self) -> DocumentsConfig:
        if (self.seed_prefix is None) == (self.github_source is None):
            raise ValueError("DocumentsConfig requires exactly one of seed_prefix or github_source.")
        return self


class VectorStoreConfig(BaseModel):
    """Vector store config for a `conversational_rag` scenario."""

    backend: Literal["qdrant"]
    collection_prefix: str
    top_k: int


def qdrant_collection_name(vector_store: VectorStoreConfig, org_id: str) -> str:
    """Per-tenant Qdrant collection name: '{collection_prefix}__{org_id}' — the single
    source of truth etl-vectorize (writer) and rag-agent (reader) both call, so the
    two can't drift out of sync on where a tenant's vectors live.
    """
    return f"{vector_store.collection_prefix}__{org_id}"


class RagServices(BaseModel):
    """Names of the services that implement a `conversational_rag` scenario."""

    etl: str
    agent: str


class ChatConfig(BaseModel):
    """Personalizes the scenario's chat assistant, regardless of `kind`.

    `context` grounds the system prompt for `assistant` (tabular_ml) and, for
    `rag-agent` (conversational_rag), also grounds its judgment of whether a
    question is in-domain enough to call the retrieval tool at all. `sample_questions`
    is surfaced as clickable suggestions in both UIs.
    """

    context: str
    sample_questions: list[str] = []


class ScenarioDefinition(BaseModel):
    """Full scenario.yaml schema, discriminated by `kind`."""

    slug: str
    kind: Literal["tabular_ml", "conversational_rag"]
    title: str
    description: str
    role_required: str
    icon: str
    chat: ChatConfig

    dataset: TabularDataset | None = None
    model: TabularModel | None = None
    documents: DocumentsConfig | None = None
    vector_store: VectorStoreConfig | None = None
    services: TabularServices | RagServices

    @classmethod
    def load(cls, path: Path) -> ScenarioDefinition:
        """Parse and validate a single `scenario.yaml` file."""
        raw = yaml.safe_load(path.read_text())
        return cls.model_validate(raw)


def load_all(scenarios_dir: Path) -> list[ScenarioDefinition]:
    """Load every `scenario.yaml` under `scenarios_dir` (one subdirectory per scenario)."""
    return [ScenarioDefinition.load(p) for p in sorted(scenarios_dir.glob("*/scenario.yaml"))]


def resolve_scenarios(scenarios_dir: Path, raw_scenarios_env: str, kind: str) -> dict[str, ScenarioDefinition]:
    """Resolve a `SCENARIOS` env var (comma-separated slugs; empty/unset = "all") to a
    `{slug: ScenarioDefinition}` dict filtered to the given `kind`.

    One consolidated `prediction`/`assistant`/`rag-agent`/`etl-tabular`/`training`/
    `etl-vectorize` instance loads/processes every scenario this resolves to — see
    the root plan's "Consolidation mechanism" decision for why.
    """
    wanted = {slug.strip() for slug in raw_scenarios_env.split(",") if slug.strip()}
    return {d.slug: d for d in load_all(scenarios_dir) if d.kind == kind and (not wanted or d.slug in wanted)}
