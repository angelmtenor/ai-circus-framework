"""Pydantic models mirroring `scenarios/*/scenario.yaml`.

`services/platform-registry` is the only service that parses these files directly
(at bootstrap/seed time) — every other service and `ui-react` resolve scenario
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


class ChartSpec(BaseModel):
    """One chart in a scenario's default "Data" dashboard combination.

    `x`/`y`/`z`/`color_by` name real dataset columns (feature, or the target — same
    columns `ui-react`'s Data tab already offers). `ui-react`'s chartBuilder.ts is the
    single place that interprets these per `type` (e.g. `bar` aggregates `y` by `x`
    using `agg` when both are given, or falls back to value-counts of `x` alone).
    """

    type: Literal["histogram", "bar", "line", "scatter", "scatter3d", "box", "pie", "heatmap"]
    x: str | None = None
    y: str | None = None
    z: str | None = None
    color_by: str | None = None
    agg: Literal["count", "sum", "mean", "min", "max"] = "count"


class TabularDataset(BaseModel):
    """Dataset config for a `tabular_ml` scenario."""

    bucket: str
    raw_object: str
    seed_file: str
    index_col: str
    target: str
    protected_features_excluded: list[str] = []
    feature_columns: list[str]
    # Drives ui-react's generic form renderer — keyed by `feature_columns` entries, in
    # the same order, so the UI needs no scenario-specific form code.
    feature_schema: dict[str, FeatureUI]
    # Seeds ui-react's Data tab dashboard on first load (still user-editable/addable
    # there) — optional; an empty list falls back to the UI's own generic default.
    default_charts: list[ChartSpec] = []

    @model_validator(mode="after")
    def _protected_features_actually_excluded(self) -> TabularDataset:
        """Fail fast if a column listed as protected (Responsible AI) also appears in
        `feature_columns` — etl_tabular.core.etl.clean() trusts this list is accurate
        when it logs "dropped protected columns", so a scenario.yaml authoring mistake
        here must not silently ship a model trained on a column it claims to exclude.
        """
        leaked = set(self.protected_features_excluded) & set(self.feature_columns)
        if leaked:
            raise ValueError(
                f"protected_features_excluded columns {sorted(leaked)} also appear in "
                "feature_columns — they would not actually be excluded from training."
            )
        return self


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


class RequiredIf(BaseModel):
    """Generic conditional-requirement rule for a `FormFieldSpec`: the field becomes
    required only when *another* field (any field, not necessarily the scenario's
    classification field) currently holds one of `in_values`. Kept generic — not tied
    to classification — so a future `assisted_form` scenario can condition any field
    on any other field without a shared-schema change.
    """

    field: str
    in_values: list[str]


class FormFieldSpec(BaseModel):
    """One field in an `assisted_form` scenario's form — drives ui-react's generic
    form renderer the same way `FeatureUI` drives the tabular_ml prediction form.

    `validation` is a small set of generic, reusable primitives (never a
    country/domain-specific rule baked into shared code) — `pattern`/`min_length`
    supply the scenario-specific detail as plain data.
    """

    id: str
    label: str
    type: Literal["text", "textarea", "email", "tel", "select", "date"]
    required: bool = False
    required_if: RequiredIf | None = None
    options: list[str] | None = None  # for type="select"
    validation: Literal["none", "email", "phone", "pattern", "min_length"] = "none"
    pattern: str | None = None  # for validation="pattern"
    min_length: int | None = None  # for validation="min_length"
    helper_text: str | None = None

    @model_validator(mode="after")
    def _validation_params_present(self) -> FormFieldSpec:
        if self.validation == "pattern" and not self.pattern:
            raise ValueError(f"field {self.id!r}: validation='pattern' requires `pattern`.")
        if self.validation == "min_length" and self.min_length is None:
            raise ValueError(f"field {self.id!r}: validation='min_length' requires `min_length`.")
        if self.type == "select" and not self.options:
            raise ValueError(f"field {self.id!r}: type='select' requires `options`.")
        return self


class FormConfig(BaseModel):
    """Form config for an `assisted_form` scenario.

    `classification_field`/`classification_options` are optional: set them only when
    the scenario wants the assistant to categorize the request via RAG (see
    `documents`/`vector_store`, reused as-is from `conversational_rag`) — a scenario
    with no such concept (a plain contact/intake form) simply omits both, and its
    agent runs with no retrieval tool at all.
    """

    title: str
    fields: list[FormFieldSpec]
    classification_field: str | None = None
    classification_options: list[str] | None = None

    @model_validator(mode="after")
    def _classification_field_is_a_real_field(self) -> FormConfig:
        if self.classification_field is None:
            return self
        if self.classification_field not in {f.id for f in self.fields}:
            raise ValueError(f"classification_field {self.classification_field!r} is not among `fields`.")
        if not self.classification_options:
            raise ValueError("classification_field is set but classification_options is empty.")
        return self


class FormServices(BaseModel):
    """Names of the services that implement an `assisted_form` scenario."""

    etl: str
    agent: str


class ChatConfig(BaseModel):
    """Personalizes the scenario's chat assistant, regardless of `kind`.

    `context` grounds the system prompt for `assistant` (tabular_ml) and, for
    `rag-agent` (conversational_rag), also grounds its judgment of whether a
    question is in-domain enough to call the retrieval tool at all. `sample_questions`
    is surfaced as clickable suggestions in `ui-react`.
    """

    context: str
    sample_questions: list[str] = []


class DatasetCredits(BaseModel):
    """Attribution for a scenario's real-world dataset — surfaced to the end user
    (`ui-react`'s Data tab) alongside `description`, not just left in a YAML comment.
    `None` for a scenario whose content is original (e.g. `ai_circus_reference`),
    rather than ported from a public dataset.
    """

    source: str  # e.g. "Kaggle", "AWS Supply Chain Workshop", "UCI Machine Learning Repository"
    url: str
    note: str | None = None


class ScenarioDefinition(BaseModel):
    """Full scenario.yaml schema, discriminated by `kind`."""

    slug: str
    kind: Literal["tabular_ml", "conversational_rag", "assisted_form"]
    title: str
    description: str
    role_required: str
    icon: str
    chat: ChatConfig
    credits: DatasetCredits | None = None

    dataset: TabularDataset | None = None
    model: TabularModel | None = None
    documents: DocumentsConfig | None = None
    vector_store: VectorStoreConfig | None = None
    form: FormConfig | None = None
    services: TabularServices | RagServices | FormServices

    @model_validator(mode="after")
    def _classification_needs_a_retrieval_source(self) -> ScenarioDefinition:
        """A form that classifies via RAG needs something to retrieve against —
        fail fast at load time rather than the agent silently having no tool for it.
        """
        needs_retrieval = self.form is not None and self.form.classification_field is not None
        if needs_retrieval and (self.documents is None or self.vector_store is None):
            raise ValueError(
                "form.classification_field is set but documents/vector_store is missing — "
                "classification needs a document catalog to retrieve against."
            )
        return self

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
