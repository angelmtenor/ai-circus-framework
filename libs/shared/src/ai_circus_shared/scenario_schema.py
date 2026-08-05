"""Pydantic models mirroring `scenarios/*/scenario.yaml`.

`services/platform-registry` is the only service that parses these files directly
(at bootstrap/seed time) — every other service and both UIs resolve scenario
metadata through platform-registry's API (see `entitlements.py`), not the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field


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


class DocumentsConfig(BaseModel):
    """Source-document config for a `conversational_rag` scenario."""

    bucket: str
    raw_prefix: str
    seed_prefix: str
    chunking: DocumentChunking
    embedding: DocumentEmbedding


class VectorStoreConfig(BaseModel):
    """Vector store config for a `conversational_rag` scenario."""

    backend: Literal["qdrant"]
    collection_prefix: str
    top_k: int


class RagServices(BaseModel):
    """Names of the services that implement a `conversational_rag` scenario."""

    etl: str
    agent: str


class ScenarioDefinition(BaseModel):
    """Full scenario.yaml schema, discriminated by `kind`."""

    slug: str
    kind: Literal["tabular_ml", "conversational_rag"]
    title: str
    description: str
    role_required: str
    icon: str

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
