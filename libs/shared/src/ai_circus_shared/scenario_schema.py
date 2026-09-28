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
    # Human-friendly display name (e.g. "Quadrature-axis current" for column `i_q`) —
    # both UIs render this instead of the raw column name everywhere a feature is shown.
    label: str
    # One-two sentence technical explanation, shown behind an info button rather than
    # inline, for a user who wants the precise (e.g. engineering/domain) meaning.
    info: str | None = None
    min: float
    max: float
    default: float
    step: float = 1.0


class CategoricalFeatureUI(BaseModel):
    """Declarative UI hint for a categorical feature: a fixed set of options."""

    type: Literal["categorical"] = "categorical"
    label: str
    info: str | None = None
    options: list[str]
    default: str


class TextFeatureUI(BaseModel):
    """Declarative UI hint for a free-text feature (e.g. an employee review): a textarea.

    Training vectorizes it with TF-IDF inside the model pipeline (see
    `ai_circus_shared.tabular_ml.TEXT_VECTORIZER_PARAMS`), so the model reads raw text at
    predict time and SHAP explains it word by word. Never a chart axis or a filter chip.
    """

    type: Literal["text"] = "text"
    label: str
    info: str | None = None
    default: str = ""
    # Hard cap on characters — etl-tabular truncates the dataset to it and prediction
    # rejects longer values, bounding TF-IDF/SHAP cost per record.
    max_length: int = Field(default=1000, ge=1, le=5000)
    placeholder: str | None = None


FeatureUI = Annotated[NumericFeatureUI | CategoricalFeatureUI | TextFeatureUI, Field(discriminator="type")]

# The *domain* a scenario belongs to — a second, orthogonal axis to `kind`
# (ML/RAG/form). Powers ui-react's "Domain" filter atop the scenario picker. Mostly
# industries, plus two non-industry domains: `tutorial` (a didactic walkthrough of a
# classic dataset, e.g. `titanic`) and `society_ethics` (social impact, fairness and
# ethics). The YAML/DB key is still `industry` (the field predates the broader
# meaning; renaming it would need a real DB migration for no behavioural gain).
# Closed set (not a free string) so a scenario.yaml typo fails fast at seed time
# rather than silently creating an unfiltered domain nobody meant to add.
Industry = Literal[
    "banking_finance",
    "manufacturing_industry",
    "energy_utilities",
    "retail",
    "logistics",
    "public_sector",
    "healthcare",
    "general",
    "tutorial",
    "society_ethics",
]


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
    # Descriptive columns (e.g. a passenger's `Name`) etl-tabular carries through to
    # the normalized dataset so the UI can show *who* a row is — never a model input
    # (training reads `feature_columns` only). Must be non-null in the raw data.
    display_columns: list[str] = []

    @model_validator(mode="after")
    def _display_columns_are_not_model_inputs(self) -> TabularDataset:
        """A display column must never double as a feature/target, nor resurface a
        column the scenario explicitly excludes for Responsible AI reasons.
        """
        clashing = set(self.display_columns) & {*self.feature_columns, self.target, *self.protected_features_excluded}
        if clashing:
            raise ValueError(
                f"display_columns {sorted(clashing)} are also features/target/protected columns — "
                "a display column is shown to users only, never trained on or re-exposed."
            )
        return self

    @model_validator(mode="after")
    def _default_charts_reference_chartable_columns(self) -> TabularDataset:
        """A default chart must name real columns, and never a free-text one (a
        histogram of 4,000 unique reviews is noise, not a chart)."""
        for chart in self.default_charts:
            check_chart_columns(chart, self, "dataset.default_charts")
        return self

    def text_columns(self) -> list[str]:
        """The `type: text` feature columns, in feature order."""
        return [c for c in self.feature_columns if getattr(self.feature_schema.get(c), "type", None) == "text"]

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


def check_chart_columns(chart: ChartSpec, dataset: TabularDataset, where: str) -> None:
    """Raise if `chart` names a column that isn't a feature/target, or a text feature."""
    named = [c for c in (chart.x, chart.y, chart.z, chart.color_by) if c is not None]
    unknown = sorted(set(named) - {*dataset.feature_columns, dataset.target})
    if unknown:
        raise ValueError(f"{where}: chart columns {unknown} are unknown.")
    text = sorted(c for c in named if getattr(dataset.feature_schema.get(c), "type", None) == "text")
    if text:
        raise ValueError(f"{where}: chart columns {text} are free-text features and cannot be charted.")


class TextChallenger(BaseModel):
    """An opt-in second model for a scenario with a free-text feature: the same tabular
    features, but the text read by a *sentence-transformer* (dense embeddings) instead of
    TF-IDF, then `estimator`. Trained and scored next to the deployed model every run
    and served on demand (`/predict?model=challenger`) so the two can be compared on any
    input — never auto-promoted: the deployed model stays the Green Code winner of
    `candidates`, and the model card flags when the challenger would have won.

    Embeddings come from llm-gateway's `embedding_model` (no torch in training or
    prediction). The whole dataset is embedded once on the host GPU
    (`make k3s-text-embeddings` loads `hf_model_id` — the same weights the gateway
    serves) into a SeaweedFS cache that training reads.
    """

    embedding_model: str = "local-embed"  # an llm-gateway model_name (litellm_config.yaml)
    hf_model_id: str  # the sentence-transformers id llm-gateway serves as embedding_model
    label: str  # e.g. "voyage-4-nano sentence embeddings"
    estimator: str = "lightgbm_small_data"  # a training candidate name


class TabularModel(BaseModel):
    """Model-selection config for a `tabular_ml` scenario (Green Code policy)."""

    task_type: Literal["classification", "regression"]
    candidates: list[str]
    accuracy_gain_threshold_for_complexity: float
    # What the Green Code comparison ranks candidates by — None = the task's default
    # (accuracy for classification, R² for regression). `roc_auc` ranks a classifier
    # by how well it *orders* risk, independent of any 0.5 threshold.
    selection_metric: Literal["accuracy", "roc_auc", "r2"] | None = None
    # 0 = score each candidate once on the 20% hold-out (fast; the default). >= 2 =
    # stratified k-fold cross-validation on the 80% training split instead — a far
    # less noisy comparison on small datasets, where one hold-out of a few hundred
    # rows can swing a metric by several points. The hold-out is still reported.
    cv_folds: int = Field(default=0, ge=0, le=10)
    explainability: Literal["shap"] = "shap"
    # Display-only unit for a regression target (e.g. "days") — None for classification.
    target_units: str | None = None
    # Human-friendly name for the target column (e.g. "Customer churned" for `Exited`)
    # — both UIs render this instead of the raw target column name.
    target_label: str
    # One-sentence plain-language explanation of what the model predicts, shown
    # alongside target_label (e.g. behind an info button).
    target_description: str | None = None
    # Classification only — maps each raw class value (stringified, e.g. "0"/"1") to a
    # friendly label (e.g. {"0": "Stayed", "1": "Churned"}) so the UI never shows a
    # bare 0/1 for the target's real-world meaning. None for regression (no classes).
    target_value_labels: dict[str, str] | None = None
    # Free-text scenarios only — see TextChallenger.
    text_challenger: TextChallenger | None = None

    @model_validator(mode="after")
    def _selection_settings_match_task(self) -> TabularModel:
        if self.cv_folds == 1:
            raise ValueError("model.cv_folds must be 0 (single hold-out) or >= 2.")
        allowed = {"classification": {"accuracy", "roc_auc"}, "regression": {"r2"}}[self.task_type]
        if self.selection_metric is not None and self.selection_metric not in allowed:
            raise ValueError(f"model.selection_metric {self.selection_metric!r} is not valid for {self.task_type}.")
        return self


class TabularServices(BaseModel):
    """Names of the services that implement a `tabular_ml` scenario."""

    etl: str
    training: str
    prediction: str
    assistant: str


class MapRegion(BaseModel):
    """One bubble on a `RegionMapExtra` map: a real-world point plus the categorical
    value (matching one of `group_by`'s `feature_schema` options) it represents.

    `feature_overrides` fixes any other `feature_columns` entry to this region's own
    real value (e.g. its population or an industrial-intensity index) instead of the
    one shared, user-adjustable value ui-react's RegionMapView applies to every
    region for the rest of the form — generic key-value, not tied to any one
    scenario's feature names, so a region-map scenario can pin as many or as few
    region-specific features as its dataset actually has.
    """

    key: str  # must match one of group_by's feature_schema categorical `options`
    label: str  # display name, e.g. "Andalucía"
    lat: float
    lon: float
    feature_overrides: dict[str, float | str] = {}


class RegionMapLevel(BaseModel):
    """One selectable aggregation granularity on a `RegionMapExtra` map (e.g. "region"
    vs "province") — its own `group_by` categorical feature and its own set of
    bubbles. Every level scores against the exact same trained model; a finer level
    is only meaningful if its `regions` entries' `feature_overrides` supply real,
    differentiated values for that granularity (not just a copy of the coarser
    level's numbers) — otherwise its predictions/SHAP would be indistinguishable
    from the coarser level's, which defeats the point of offering it.
    """

    key: str  # stable id for this level, e.g. "region" — used by the UI's selector
    label: str  # display name, e.g. "Comunidad Autónoma" / "Provincia"
    # A categorical entry in dataset.feature_columns whose value ui-react's
    # RegionMapView sets to each bubble's own `key` when building that bubble's
    # /predict request. None when this level has no dedicated trained feature of
    # its own (e.g. a coarser level whose bubbles are fully specified through
    # `feature_overrides` instead — see `luznova_regional_demand`'s "region" level,
    # which reuses `province`'s trained signal rather than training a second,
    # perfectly-collinear "region" feature).
    group_by: str | None = None
    regions: list[MapRegion]


class RegionMapExtra(BaseModel):
    """Opt-in 5th workspace tab: a geographic bubble map of batched predictions,
    grouped by one of `levels`' `group_by` categorical features (see ui-react's
    RegionMapView.tsx — the single generic renderer for this `kind`, reused by any
    `tabular_ml` scenario that sets this block; not scenario-specific UI code). At
    least one level; more than one lets the user switch aggregation granularity
    (e.g. region vs. province) without leaving the tab.
    """

    kind: Literal["region_map"] = "region_map"
    value_label: str  # e.g. "Predicted demand (MWh)"
    levels: list[RegionMapLevel] = Field(min_length=1)


class LivePlantExtra(BaseModel):
    """Opt-in 5th workspace tab: a fictional live plant floor of `machine_count`
    simulated machines, ticking client-side every `tick_seconds`, each scored by this
    scenario's own `/predict/{slug}` on every tick (see ui-react's LivePlantView.tsx —
    the single generic renderer for this `kind`). Simulation and "shut down"/
    "maintenance"/"replace" controls are client-side only — no real telemetry
    ingestion, no real actuation.
    """

    kind: Literal["live_plant"] = "live_plant"
    machine_count: int = 6
    tick_seconds: int = 7
    machine_label_prefix: str = "Machine"
    # How much simulated time one tick represents — the displayed clock advances by
    # this many minutes every `tick_seconds`, so a short real-time session can still
    # show a machine aging meaningfully (see `wear_feature`).
    sim_minutes_per_tick: int = 5
    # A numeric feature_columns entry that accumulates by `sim_minutes_per_tick` each
    # tick while a machine is running (e.g. "Tool wear [min]") — its own real
    # min/max from feature_schema bounds the ramp, and it resets on
    # maintenance/replace. None falls back to a generic bounded random walk with no
    # forced aging trend, for a live_plant scenario with no such feature.
    wear_feature: str | None = None


class CycleTimeModel(BaseModel):
    """How long one unit of product takes on a `ProcessOptimizerExtra` line, as a
    function of the recipe: `minutes_per_unit = fixed_minutes + work_per_unit /
    (rate_scale * product(rate_features))`. The product-of-features form is the
    standard machining "material removal rate" (cutting speed x feed x depth of
    cut), but it's just as valid for any process whose throughput scales with a few
    multiplied setpoints (line speed x lanes, flow x concentration) — with an empty
    `rate_features`, cycle time is simply the constant `fixed_minutes`.
    """

    fixed_minutes: float = Field(ge=0)  # load/unload/approach time the recipe can't change
    work_per_unit: float = Field(gt=0)  # e.g. mm^3 of stock to remove per part
    rate_features: list[str] = []  # numeric feature_columns whose product is the processing rate
    rate_scale: float = 1.0  # unit conversion so work_per_unit / (scale * product) is in minutes
    rate_label: str = "Processing rate"  # display only, e.g. "Material removal rate"
    rate_units: str = ""  # display only, e.g. "mm³/min"


class ToolLifeModel(BaseModel):
    """Optional consumable-tool economics for a `ProcessOptimizerExtra`, via Taylor's
    tool-life equation `V * T^n = C` — i.e. life at setpoint `v` is
    `reference_life_minutes * (reference_value / v) ** (1 / taylor_n)`. `feature` is
    the numeric setpoint that drives wear (cutting speed in turning), so the
    optimizer is charged a per-unit consumable + changeover cost that rises steeply
    with speed — the classic productivity-vs-tooling trade-off. When the scenario
    also names a `wear_feature`, the live-line simulation ages that feature by
    `cutting_minutes * (v / reference_value) ** (1 / taylor_n)` scaled so a tool at
    the reference setpoint reaches the feature's max after exactly
    `reference_life_minutes` of cutting — one consistent physical story for both.
    """

    feature: str
    reference_value: float = Field(gt=0)
    reference_life_minutes: float = Field(gt=0)
    taylor_n: float = Field(default=0.25, gt=0, le=1)
    cost_per_edge: float = Field(ge=0)  # consumable cost per tool change
    change_minutes: float = Field(default=0, ge=0)  # line downtime per tool change


class OptimizerEconomics(BaseModel):
    """The (fictional, per-scenario) cost model a `ProcessOptimizerExtra` scores
    every candidate recipe with — margin per unit = `unit_value` minus machine
    time (`machine_rate_per_hour` x cycle time), tooling (see `tool_life`), and the
    expected out-of-spec cost (`reject_cost` x the model's own probability that
    this recipe misses the chosen spec, from its prediction interval). Every figure
    is display-labelled with `currency`; none is real pricing data.
    """

    currency: str = "€"
    unit_value: float = Field(gt=0)  # revenue attributable to this operation per in-spec unit
    reject_cost: float = Field(ge=0)  # rework/scrap cost per out-of-spec unit
    machine_rate_per_hour: float = Field(ge=0)
    batch_size: int = Field(default=500, gt=0)  # the batch the KPI strip extrapolates to
    cycle_time: CycleTimeModel
    tool_life: ToolLifeModel | None = None


class SpecOption(BaseModel):
    """One selectable quality limit for a `ProcessOptimizerExtra` (e.g. an ISO 1302
    roughness grade): the target must stay <= `value` (objective "minimize") or
    >= `value` ("maximize") for a unit to count as in-spec.
    """

    label: str
    value: float


class ProcessOptimizerExtra(BaseModel):
    """Opt-in 5th workspace tab: a "take action" stage after prediction for a
    regression scenario whose target is a process-quality outcome — the optimizer
    searches the `controllable` setpoints (batched candidate recipes scored by this
    scenario's own, unmodified `/predict/{slug}`, coarse round then a refinement
    round around the best), scores each with `economics`, and recommends the recipe
    that maximizes the chosen objective while keeping the predicted target within
    the selected spec; the user (or an "auto-pilot") can then apply it. A client-side
    live-line simulation ages `wear_feature` with real cutting time so the model's
    own predicted drift, re-optimization, and tool changes play out over a short demo
    session, side by side with a static-recipe baseline. See ui-react's
    ProcessOptimizerView.tsx — the single generic renderer for this `kind`; the
    demo's economics/timing are all data here, never scenario-specific UI code.
    """

    kind: Literal["process_optimizer"] = "process_optimizer"
    # Numeric feature_columns the optimizer may change — the "recipe". Every other
    # feature is fixed context (the customer's material, the current tool wear).
    controllable: list[str] = Field(min_length=1)
    # A controllable feature only orderable at these values (e.g. standard insert
    # nose radii) — searched as a discrete choice instead of a continuous range.
    discrete_values: dict[str, list[float]] = {}
    # Which direction is "better" for the target — minimize (roughness, energy,
    # scrap) or maximize (yield, strength).
    objective: Literal["minimize", "maximize"] = "minimize"
    spec_options: list[SpecOption] = Field(min_length=1)
    spec_default: float
    # A numeric feature that accumulates with cutting time in the live-line
    # simulation and resets on a tool change — needs `economics.tool_life` to know
    # how fast. None disables the aging story (recipes still optimize/apply).
    wear_feature: str | None = None
    tick_seconds: int = Field(default=3, ge=1)
    sim_minutes_per_tick: int = Field(default=5, ge=1)
    economics: OptimizerEconomics


class TriageLane(BaseModel):
    """One care-pathway lane on a `TriageBoardExtra` board: every predicted class in
    `labels` (keys from `deep_learning.labels`) is routed here when the model is
    confident enough (see `TriageBoardExtra.confidence_threshold`).
    """

    key: str
    label: str  # display name, e.g. "Urgent — same-day clinician"
    description: str | None = None
    labels: list[str] = Field(min_length=1)
    # Visual severity only — ui-react maps it to a color, never to routing logic.
    tone: Literal["critical", "warning", "info", "ok"] = "info"


class TriageBoardExtra(BaseModel):
    """Opt-in 5th workspace tab for a `deep_learning` scenario (text or image): a live
    board where incoming items (the held-out test split, replayed client-side every
    `tick_seconds`) are routed by the model's top prediction into `lanes`; anything
    below `confidence_threshold` goes to a human-review lane instead (selective
    prediction) — a patient-message triage board, or a visual-inspection line's
    pass / reject / manual-inspection split. See ui-react's TriageBoardView.tsx — the
    single generic renderer; the wording fields below are its only domain vocabulary.
    """

    kind: Literal["triage_board"] = "triage_board"
    lanes: list[TriageLane] = Field(min_length=1)
    review_lane_label: str = "Human review"
    confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    tick_seconds: int = Field(default=4, ge=1)
    tab_label: str = "Triage Board"
    title: str = "Live triage board"
    item_noun: str = "item"  # singular, e.g. "patient message" / "part" ("s" appended for plurals)
    reviewer_noun: str = "human reviewer"  # who decides the review lane, e.g. "clinician" / "inspector"
    critical_kpi_label: str = "Critical caught"  # KPI for the `tone: critical` lane, if any
    note: str | None = None  # shown under the title, e.g. "Illustrative routing only — not clinical guidance."


class ReadingRoomExtra(BaseModel):
    """Opt-in 5th workspace tab for an image `deep_learning` scenario: a simulated
    reading-room worklist of held-out studies, prioritized by the model's probability
    of `positive_label` (vs. first-in-first-out), with a viewer (window/level, zoom,
    explanation overlay) and reader confirm/override actions. The time-to-read KPI
    replays the same worklist through both orderings with `minutes_per_read` per study
    and one arrival every `arrival_minutes`. See ui-react's ReadingRoomView.tsx.
    """

    kind: Literal["reading_room"] = "reading_room"
    positive_label: str  # a key from deep_learning.labels, e.g. "1" (pneumonia)
    priority_threshold: float = Field(default=0.5, ge=0, le=1)
    minutes_per_read: float = Field(default=4.0, gt=0)
    arrival_minutes: float = Field(default=2.0, gt=0)
    worklist_size: int = Field(default=40, ge=5, le=200)


class ExampleRecord(BaseModel):
    """A named, complete input record (every `feature_columns` entry) — a preset the
    UI scores live against `/predict/{slug}`, e.g. the notebook's "Boy, 3rd class" or
    a what-if persona. Validated against `feature_schema` at seed time.
    """

    label: str
    description: str | None = None
    record: dict[str, float | str]


class VoyageZone(BaseModel):
    """One berth zone on a `VoyageExplorerExtra` vessel — every row whose `zone_by`
    value equals `key` is drawn inside it."""

    key: str  # one of zone_by's categorical `options`
    label: str  # e.g. "First class"
    description: str | None = None  # e.g. "Staterooms on decks A-C, near the lifeboats"


class VoyageExplorerExtra(BaseModel):
    """Opt-in 5th workspace tab for a binary-classification `tabular_ml` scenario whose
    rows are *people aboard a vessel*: every real row is drawn as one person inside an
    illustrated cross-section (`scene`), berthed by `zone_by` (top deck = first zone),
    coloured by the model's predicted probability of the positive class — or, once
    "revealed", by the real outcome. Filters on `filters` (categorical) and
    `range_filters` (numeric) highlight a cohort with its real vs predicted rate;
    clicking a person shows their SHAP-explained probability, and any row (or a
    `personas` preset) can be edited as a what-if. See ui-react's VoyageView.tsx — the
    single generic renderer; the wording fields are its only domain vocabulary.
    """

    kind: Literal["voyage_explorer"] = "voyage_explorer"
    # The built-in illustration: `ocean_liner` (a ship's decks — titanic) or
    # `office_tower` (an office building's floors — toxic_leadership).
    scene: Literal["ocean_liner", "office_tower"] = "ocean_liner"
    tab_label: str = "Voyage"
    title: str
    subtitle: str | None = None
    person_noun: str = "passenger"
    zone_by: str  # a categorical feature_columns entry, e.g. "Pclass"
    zones: list[VoyageZone] = Field(min_length=1)
    filters: list[str] = []  # categorical feature_columns offered as filter chips
    range_filters: list[str] = []  # numeric feature_columns offered as min-max sliders
    name_column: str | None = None  # a dataset.display_columns entry naming each person
    personas: list[ExampleRecord] = []
    # Colour the positive class as the bad outcome (red) — e.g. "bad leadership" —
    # instead of the good one (titanic's "survived").
    positive_is_adverse: bool = False


# A live, data-backed block a tutorial step can embed (see ui-react's TutorialView.tsx):
# dataset_preview = the first rows; class_balance = the target's class split;
# model_card = the training leaderboard + selection protocol; roc_curve /
# confusion_matrix = the selected model on the untouched hold-out; feature_importance
# = global mean(|SHAP|); examples = `tutorial.examples` scored and explained live;
# top_terms = the words/phrases of a text feature that push predictions up vs down
# (the model card's `text_term_importance`; needs a `type: text` feature).
TutorialWidget = Literal[
    "dataset_preview",
    "class_balance",
    "model_card",
    "roc_curve",
    "confusion_matrix",
    "feature_importance",
    "examples",
    "top_terms",
]


class TutorialStep(BaseModel):
    """One chapter of a scenario's Tutorial tab: markdown prose plus optional live
    charts (same ChartSpec as `default_charts`) and widgets."""

    title: str
    body: str  # markdown (headers, bold, code, lists, pipe tables, links)
    takeaway: str | None = None  # one-sentence "key insight" callout
    charts: list[ChartSpec] = []
    widgets: list[TutorialWidget] = []


class TutorialConfig(BaseModel):
    """A guided, data-backed walkthrough of the whole ML workflow for this scenario —
    rendered as its own "Tutorial" tab (tabular_ml only for now: its widgets read the
    prediction service's dataset/model endpoints). Pure content: adding a tutorial to
    another scenario is YAML, never UI code.
    """

    tab_label: str = "Tutorial"
    intro: str  # markdown
    steps: list[TutorialStep] = Field(min_length=1)
    examples: list[ExampleRecord] = []
    # See VoyageExplorerExtra.positive_is_adverse.
    positive_is_adverse: bool = False


class RubricBehaviour(BaseModel):
    """One observable behaviour of a `RubricCheckConfig` rubric."""

    key: str  # stable id the LLM must answer with, e.g. "credit_taking"
    name: str  # e.g. "Self-promotion & credit-taking"
    description: str  # what it looks like in practice — the LLM's definition


class RubricExample(BaseModel):
    """A ready-made description the user can load into the rubric check."""

    label: str
    text: str


class RubricCheckConfig(BaseModel):
    """An LLM-assessed rubric over a free-text *description of behaviour* (tabular_ml
    scenarios with a text feature): the user describes what someone does, `assistant`
    asks the active LLM (via llm-gateway) which `positive` / `negative` behaviours the
    description shows — quoting it as evidence — and ui-react shows that next to the
    trained model(s) scoring the same text as `text_feature`. It assesses the behaviour
    described, never a named person: `guidance` carries the scenario's own guardrails.
    """

    title: str  # e.g. "Leadership check"
    intro: str  # markdown shown above the input
    input_label: str
    placeholder: str | None = None
    max_chars: int = Field(default=1500, ge=100, le=4000)
    text_feature: str  # the dataset text feature the description is also scored as
    subject_noun: str = "person"  # e.g. "leader"
    positive_label: str  # verdict wording, e.g. "Great leadership"
    negative_label: str  # e.g. "Toxic management"
    positive: list[RubricBehaviour] = Field(min_length=1)
    negative: list[RubricBehaviour] = Field(min_length=1)
    guidance: str  # extra instructions for the LLM (tone, caveats, what not to do)
    examples: list[RubricExample] = []


UiExtras = Annotated[
    RegionMapExtra | LivePlantExtra | ProcessOptimizerExtra | VoyageExplorerExtra | TriageBoardExtra | ReadingRoomExtra,
    Field(discriminator="kind"),
]

# ui_extras kinds meant for each scenario kind — a tabular renderer given a
# deep_learning scenario (or vice versa) would have none of the data it needs.
_TABULAR_UI_EXTRAS = (RegionMapExtra, LivePlantExtra, ProcessOptimizerExtra, VoyageExplorerExtra)
_DEEP_LEARNING_UI_EXTRAS = (TriageBoardExtra, ReadingRoomExtra)


class HuggingFaceFilesSource(BaseModel):
    """Raw data fetched from a public Hugging Face Hub *dataset* repo at a pinned
    commit, file by file, each checked against its SHA-256 — so a re-download can never
    silently train on different data than the one reviewed. Text: JSON Lines rows with
    `text_field`. Images: Parquet rows whose `image_field` is the Hub's standard Image
    feature (`{bytes, path}`), optionally with a ground-truth defect `mask_field` of the
    same shape — the layout almost every image dataset on the Hub already uses.
    """

    type: Literal["huggingface_files"] = "huggingface_files"
    repo: str  # e.g. "gretelai/symptom_to_diagnosis"
    revision: str  # full commit sha
    # split name ("train"/"validation"/"test") -> file name within the repo. A
    # missing "validation" split is carved out of "train" (DlTraining.val_fraction).
    files: dict[str, str]
    sha256: dict[str, str]  # file name -> expected hex digest
    format: Literal["jsonl", "parquet"] = "jsonl"
    text_field: str | None = None
    image_field: str | None = None
    mask_field: str | None = None  # image only: per-pixel ground truth (anomaly localization)
    label_field: str

    @model_validator(mode="after")
    def _splits_and_checksums(self) -> HuggingFaceFilesSource:
        if not {"train", "test"} <= set(self.files):
            raise ValueError("huggingface_files source needs at least 'train' and 'test' files.")
        missing = set(self.files.values()) - set(self.sha256)
        if missing:
            raise ValueError(f"huggingface_files source has no sha256 for {sorted(missing)}.")
        if (self.text_field is None) == (self.image_field is None):
            raise ValueError("huggingface_files source needs exactly one of text_field / image_field.")
        if self.image_field is not None and self.format != "parquet":
            raise ValueError("huggingface_files image sources must be format='parquet'.")
        if self.mask_field is not None and self.image_field is None:
            raise ValueError("huggingface_files mask_field needs an image_field.")
        return self


class NpzImagesSource(BaseModel):
    """Raw image data as one public `.npz` archive in the MedMNIST layout
    (`{split}_images` uint8 arrays + `{split}_labels` integer arrays for
    train/val/test), checked against its MD5 (the digest MedMNIST itself publishes).
    """

    type: Literal["npz_images"] = "npz_images"
    url: str
    md5: str


DlDataSource = Annotated[HuggingFaceFilesSource | NpzImagesSource, Field(discriminator="type")]


class DlLabel(BaseModel):
    """One output class, in model output order. `key` is the raw value in the source
    data (a diagnosis string, or a stringified integer class id)."""

    key: str
    label: str
    description: str | None = None


class DlTrainBudget(BaseModel):
    """Training hyper-parameters for one device class. `max_train_samples` (None =
    all) and `trainable_layers` (None = full fine-tune, N = only the top N encoder
    blocks plus the classification head) are the knobs that keep a CPU run tractable.

    `task: classification` needs `epochs` + `learning_rate` (gradient fine-tuning);
    `task: anomaly_detection` trains nothing — it needs `memory_bank_size` instead (how
    many normal patch features the greedy coreset keeps; see DlAnomalyDetection).
    """

    epochs: int | None = Field(default=None, ge=1)
    batch_size: int = Field(ge=1)
    learning_rate: float | None = Field(default=None, gt=0)
    max_train_samples: int | None = Field(default=None, ge=1)
    trainable_layers: int | None = Field(default=None, ge=0)
    memory_bank_size: int | None = Field(default=None, ge=16)


class DlTraining(BaseModel):
    """`gpu` is used when dl-training finds CUDA, `cpu` otherwise — both explicit here
    so an admin sees what a CPU run will actually do before triggering it."""

    gpu: DlTrainBudget
    cpu: DlTrainBudget
    val_fraction: float = Field(default=0.1, gt=0, lt=0.5)
    seed: int = 42
    # Inverse-frequency class weights in the loss — for imbalanced sources.
    class_weighted_loss: bool = False
    # Soft targets (1 - eps on the true class): stops a fine-tune from driving logits to
    # +/-infinity on an easy dataset, i.e. from answering "100%" for everything.
    # dl-training additionally fits a temperature on the validation split after
    # training (post-hoc calibration) — see its core/calibration.py.
    label_smoothing: float = Field(default=0.1, ge=0, lt=0.5)
    # 0 = fit the calibration temperature on the validation split. > 0 = hold out this
    # stratified fraction of the *test* pool instead ("local calibration" on data from the
    # deployment distribution) and evaluate/publish only the untouched rest — for sources
    # whose test set is shifted from the train/val pool (e.g. MedMNIST's PneumoniaMNIST).
    calibration_holdout_fraction: float = Field(default=0.0, ge=0, lt=0.9)


class DlAnomalyDetection(BaseModel):
    """`task: anomaly_detection` (image only): one-class visual anomaly detection that
    learns from *normal* samples only — how industrial inspection is set up, since
    defects are rare, varied and mostly unseen at training time. dl-training runs no
    gradient step: a frozen self-supervised backbone (e.g. DINOv2) embeds every
    14x14-pixel patch of the normal training images, a greedy k-center coreset keeps
    `memory_bank_size` of them (PatchCore, Roth et al. CVPR 2022), and a patch's anomaly
    score is its cosine distance to the nearest normal patch (AnomalyDINO, Damm et al.
    WACV 2025). The image score is the mean of the top `top_k_fraction` patch scores,
    mapped to P(anomaly) by a logistic fit on the calibration hold-out. The whole thing
    — backbone, memory bank, kNN, map — is one ONNX graph, so dl-inference serves it
    with no extra code, and its anomaly map *is* the explanation (no occlusion passes).
    """

    normal_label: str  # key of the defect-free class in `labels`; the other one is "anomalous"
    top_k_fraction: float = Field(default=0.01, gt=0, le=1)


class DeepLearningConfig(BaseModel):
    """Everything dl-training/dl-inference need for a `deep_learning` scenario: where
    the public data comes from, the Hugging Face base model it is fine-tuned from
    (pinned revision), its output classes, and the training budgets.
    """

    modality: Literal["text", "image"]
    # classification = supervised fine-tune (every class has labelled training data);
    # anomaly_detection = learn "normal" only, flag anything else (DlAnomalyDetection).
    task: Literal["classification", "anomaly_detection"] = "classification"
    anomaly: DlAnomalyDetection | None = None
    # Shown on the scenario tab, e.g. "Not a medical device …" — None = no disclaimer.
    disclaimer: str | None = None
    bucket: str
    source: DlDataSource
    labels: list[DlLabel] = Field(min_length=2)
    base_model: str  # Hugging Face model repo id
    base_model_revision: str  # full commit sha
    base_model_params: str  # display only, e.g. "150M"
    input_label: str  # display only, e.g. "Patient's own description of symptoms"
    target_label: str
    target_description: str | None = None
    # text only
    max_length: int = Field(default=128, ge=8, le=1024)
    input_examples: list[str] = []
    # image only — square input side in pixels, and the occlusion grid (N x N patches)
    # dl-inference uses for its explanation heatmap.
    image_size: int = Field(default=224, ge=32, le=512)
    occlusion_grid: int = Field(default=8, ge=2, le=16)
    # int8 dynamic quantization of the exported ONNX model — mainly for the large text
    # encoders; metrics are always measured on the artifact actually deployed.
    quantize: bool = False
    training: DlTraining
    # How many held-out test samples are published for the UI (gallery / worklist /
    # triage stream) and how many training samples are indexed for similar-case search.
    gallery_size: int = Field(default=200, ge=1, le=2000)
    reference_size: int = Field(default=600, ge=1, le=5000)

    @model_validator(mode="after")
    def _modality_matches_source(self) -> DeepLearningConfig:
        source = self.source
        if self.modality == "text":
            ok = isinstance(source, HuggingFaceFilesSource) and source.text_field is not None
            expected = "a huggingface_files source with text_field"
        else:
            ok = isinstance(source, NpzImagesSource) or source.image_field is not None
            expected = "an npz_images source, or huggingface_files with image_field"
        if not ok:
            raise ValueError(f"modality={self.modality!r} needs {expected}.")
        keys = [label.key for label in self.labels]
        if len(set(keys)) != len(keys):
            raise ValueError("deep_learning.labels keys must be unique.")
        return self

    @model_validator(mode="after")
    def _task_settings(self) -> DeepLearningConfig:
        """Each task gets exactly the knobs it uses — an anomaly detector has no epochs,
        a fine-tune has no memory bank — so a typo'd scenario fails at seed time, not
        halfway through a GPU run.
        """
        budgets = {"gpu": self.training.gpu, "cpu": self.training.cpu}
        if self.task == "classification":
            if self.anomaly is not None:
                raise ValueError("deep_learning.anomaly is only for task='anomaly_detection'.")
            for name, budget in budgets.items():
                if budget.epochs is None or budget.learning_rate is None:
                    raise ValueError(f"training.{name} needs epochs and learning_rate for task='classification'.")
            return self
        if self.modality != "image" or self.anomaly is None:
            raise ValueError("task='anomaly_detection' needs modality='image' and an `anomaly` block.")
        if len(self.labels) != 2:
            raise ValueError("task='anomaly_detection' needs exactly two labels (normal, anomalous).")
        if self.anomaly.normal_label not in {label.key for label in self.labels}:
            raise ValueError(f"anomaly.normal_label {self.anomaly.normal_label!r} is not a label key.")
        if not self.training.calibration_holdout_fraction:
            # The training (and validation) data are normal-only: P(anomaly) can only be
            # fitted on a slice of the labelled test pool.
            raise ValueError("task='anomaly_detection' needs training.calibration_holdout_fraction > 0.")
        for name, budget in budgets.items():
            if budget.memory_bank_size is None:
                raise ValueError(f"training.{name} needs memory_bank_size for task='anomaly_detection'.")
        return self


class DeepLearningServices(BaseModel):
    """Names of the services that implement a `deep_learning` scenario."""

    training: str
    inference: str


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

    At least one of `seed_prefix` (a tracked local folder, relative to the scenario's
    directory) or `github_source` (a public GitHub repo folder) must provide the demo
    bootstrap documents — see `etl_vectorize.core.vectorize.ensure_raw_docs`. When both
    are set, `github_source` is tried first and `seed_prefix` is the offline/rate-limit
    fallback if that fetch fails, so upstream stays the source of truth without a
    GitHub outage or unauthenticated-rate-limit hit taking the scenario's RAG index
    down entirely.
    """

    bucket: str
    raw_prefix: str
    seed_prefix: str | None = None
    github_source: GithubDocsSource | None = None
    chunking: DocumentChunking
    embedding: DocumentEmbedding

    @model_validator(mode="after")
    def _at_least_one_seed_source(self) -> DocumentsConfig:
        if self.seed_prefix is None and self.github_source is None:
            raise ValueError("DocumentsConfig requires at least one of seed_prefix or github_source.")
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
    kind: Literal["tabular_ml", "conversational_rag", "assisted_form", "deep_learning"]
    title: str
    description: str
    role_required: str
    icon: str
    industry: Industry
    chat: ChatConfig
    credits: DatasetCredits | None = None

    dataset: TabularDataset | None = None
    model: TabularModel | None = None
    documents: DocumentsConfig | None = None
    vector_store: VectorStoreConfig | None = None
    form: FormConfig | None = None
    deep_learning: DeepLearningConfig | None = None
    # tabular_ml / deep_learning only — opts this scenario into one of ui-react's
    # generic 5th workspace tabs (see UiExtras above). None (the common case) means the
    # plain 4-tab workspace every scenario of that kind already gets.
    ui_extras: UiExtras | None = None
    # tabular_ml only — adds a guided "Tutorial" tab (see TutorialConfig).
    tutorial: TutorialConfig | None = None
    # tabular_ml with a text feature only — an LLM rubric check (see RubricCheckConfig).
    rubric_check: RubricCheckConfig | None = None
    services: TabularServices | RagServices | FormServices | DeepLearningServices

    @model_validator(mode="after")
    def _deep_learning_block_matches_kind(self) -> ScenarioDefinition:
        """`deep_learning` is required for (and only for) kind=deep_learning, and each
        ui_extras renderer only ever receives the kind of scenario it was written for.
        """
        is_dl = self.kind == "deep_learning"
        if is_dl != (self.deep_learning is not None):
            raise ValueError("kind='deep_learning' requires a `deep_learning` block (and only that kind may set one).")
        if is_dl and not isinstance(self.services, DeepLearningServices):
            raise ValueError("kind='deep_learning' requires services: {training, inference}.")
        if self.ui_extras is None:
            return self
        allowed = _DEEP_LEARNING_UI_EXTRAS if is_dl else _TABULAR_UI_EXTRAS
        if not isinstance(self.ui_extras, allowed):
            raise ValueError(f"ui_extras kind {self.ui_extras.kind!r} is not available for kind={self.kind!r}.")
        return self

    @model_validator(mode="after")
    def _deep_learning_ui_extras_reference_real_labels(self) -> ScenarioDefinition:
        """Fail fast if a triage lane / reading-room positive label names a class the
        model doesn't have, or a class is routed to no lane (or to two) — the board
        would silently drop those messages.
        """
        dl, extras = self.deep_learning, self.ui_extras
        if dl is None or extras is None:
            return self
        keys = [label.key for label in dl.labels]
        if isinstance(extras, TriageBoardExtra):
            routed = [key for lane in extras.lanes for key in lane.labels]
            unknown = set(routed) - set(keys)
            if unknown:
                raise ValueError(f"ui_extras.triage_board lanes name unknown labels {sorted(unknown)}.")
            duplicated = sorted({key for key in routed if routed.count(key) > 1})
            unrouted = sorted(set(keys) - set(routed))
            if duplicated or unrouted:
                raise ValueError(
                    f"ui_extras.triage_board must route every label to exactly one lane "
                    f"(duplicated={duplicated}, unrouted={unrouted})."
                )
        if isinstance(extras, ReadingRoomExtra):
            if dl.modality != "image":
                raise ValueError("ui_extras.reading_room requires modality='image'.")
            if extras.positive_label not in keys:
                raise ValueError(f"ui_extras.reading_room positive_label {extras.positive_label!r} is not a label key.")
        return self

    @model_validator(mode="after")
    def _region_map_columns_are_real_features(self) -> ScenarioDefinition:
        """Fail fast if a level's `group_by` or a region's `feature_overrides` key
        doesn't name a real feature — ui-react's RegionMapView would otherwise
        silently send `undefined`/an ignored extra key for that region.
        """
        if not isinstance(self.ui_extras, RegionMapExtra) or self.dataset is None:
            return self
        known = set(self.dataset.feature_columns)
        for level in self.ui_extras.levels:
            if level.group_by is not None and level.group_by not in known:
                raise ValueError(
                    f"ui_extras level {level.key!r}: group_by {level.group_by!r} is not among dataset.feature_columns."
                )
            for region in level.regions:
                unknown = set(region.feature_overrides) - known
                if unknown:
                    raise ValueError(
                        f"ui_extras level {level.key!r} region {region.key!r} "
                        f"feature_overrides {sorted(unknown)} not in dataset.feature_columns."
                    )
        return self

    @model_validator(mode="after")
    def _live_plant_wear_feature_is_real_and_numeric(self) -> ScenarioDefinition:
        """Fail fast if `ui_extras.wear_feature` doesn't name a real numeric
        feature — ui-react's LivePlantView would otherwise silently no-op the aging
        ramp (or crash trying to increment a categorical value).
        """
        if (
            not isinstance(self.ui_extras, LivePlantExtra)
            or self.ui_extras.wear_feature is None
            or self.dataset is None
        ):
            return self
        spec = self.dataset.feature_schema.get(self.ui_extras.wear_feature)
        if spec is None:
            raise ValueError(
                f"ui_extras.wear_feature {self.ui_extras.wear_feature!r} is not among dataset.feature_columns."
            )
        if spec.type != "numeric":
            raise ValueError(
                f"ui_extras.wear_feature {self.ui_extras.wear_feature!r} must be a numeric feature, got {spec.type!r}."
            )
        return self

    @model_validator(mode="after")
    def _process_optimizer_features_are_real_and_numeric(self) -> ScenarioDefinition:
        """Fail fast if a `process_optimizer` block names a feature that isn't a real
        numeric feature, offers a default spec that isn't among its options, or is set
        on a classification scenario — ui-react's ProcessOptimizerView would otherwise
        search over a categorical value, treat a probability as a physical quantity,
        or render a spec selector with no selected entry.
        """
        extras = self.ui_extras
        if not isinstance(extras, ProcessOptimizerExtra) or self.dataset is None:
            return self
        if self.model is not None and self.model.task_type != "regression":
            raise ValueError(
                "ui_extras.process_optimizer requires a regression model (a physical target to keep in spec)."
            )

        def _numeric(feature: str, where: str) -> None:
            spec = self.dataset.feature_schema.get(feature) if self.dataset else None
            if spec is None:
                raise ValueError(f"ui_extras.{where} {feature!r} is not among dataset.feature_columns.")
            if spec.type != "numeric":
                raise ValueError(f"ui_extras.{where} {feature!r} must be a numeric feature, got {spec.type!r}.")

        for feature in extras.controllable:
            _numeric(feature, "controllable")
        for feature in extras.discrete_values:
            if feature not in extras.controllable:
                raise ValueError(f"ui_extras.discrete_values key {feature!r} is not among controllable.")
        for feature in extras.economics.cycle_time.rate_features:
            _numeric(feature, "economics.cycle_time.rate_features")
        if extras.economics.tool_life is not None:
            _numeric(extras.economics.tool_life.feature, "economics.tool_life.feature")
        if extras.wear_feature is not None:
            _numeric(extras.wear_feature, "wear_feature")
            if extras.economics.tool_life is None:
                raise ValueError(
                    "ui_extras.wear_feature is set but economics.tool_life is missing — nothing defines its aging rate."
                )
        if extras.spec_default not in {o.value for o in extras.spec_options}:
            raise ValueError(f"ui_extras.spec_default {extras.spec_default!r} is not among spec_options values.")
        return self

    def _check_example(self, example: ExampleRecord, where: str) -> None:
        """An example record must set every feature, each to a value the form could
        produce — otherwise /predict would 422 (missing column) or score a category
        the model never saw."""
        assert self.dataset is not None
        schema = self.dataset.feature_schema
        missing = [f for f in self.dataset.feature_columns if f not in example.record]
        unknown = sorted(set(example.record) - set(self.dataset.feature_columns))
        if missing or unknown:
            raise ValueError(f"{where} {example.label!r}: missing features {missing}, unknown features {unknown}.")
        for feature, value in example.record.items():
            spec = schema[feature]
            if spec.type == "text" and (not isinstance(value, str) or len(value) > spec.max_length):
                raise ValueError(
                    f"{where} {example.label!r}: {feature} must be text of at most {spec.max_length} chars."
                )
            if spec.type == "categorical" and value not in spec.options:
                raise ValueError(f"{where} {example.label!r}: {feature}={value!r} is not among its options.")
            if spec.type == "numeric" and (isinstance(value, str) or not spec.min <= value <= spec.max):
                raise ValueError(f"{where} {example.label!r}: {feature}={value!r} is outside [{spec.min}, {spec.max}].")

    def _feature_of_type(self, feature: str, expected: str, where: str) -> None:
        assert self.dataset is not None
        spec = self.dataset.feature_schema.get(feature)
        if spec is None:
            raise ValueError(f"{where} {feature!r} is not among dataset.feature_columns.")
        if spec.type != expected:
            raise ValueError(f"{where} {feature!r} must be a {expected} feature, got {spec.type!r}.")

    @model_validator(mode="after")
    def _voyage_explorer_references_real_columns(self) -> ScenarioDefinition:
        """Fail fast if a `voyage_explorer` block would leave a person un-berthed (a
        `zone_by` option with no zone), filter on a column that doesn't exist, or run
        on a scenario with no probability to colour people by."""
        extras = self.ui_extras
        if not isinstance(extras, VoyageExplorerExtra) or self.dataset is None:
            return self
        if self.model is None or self.model.task_type != "classification":
            raise ValueError("ui_extras.voyage_explorer requires a classification model (a probability per person).")
        self._feature_of_type(extras.zone_by, "categorical", "ui_extras.voyage_explorer zone_by")
        zone_spec = self.dataset.feature_schema[extras.zone_by]
        assert isinstance(zone_spec, CategoricalFeatureUI)
        zone_keys = [zone.key for zone in extras.zones]
        if sorted(zone_keys) != sorted(zone_spec.options):
            raise ValueError(
                f"ui_extras.voyage_explorer zones {zone_keys} must list each {extras.zone_by!r} option "
                f"{zone_spec.options} exactly once."
            )
        for feature in extras.filters:
            self._feature_of_type(feature, "categorical", "ui_extras.voyage_explorer filters")
        for feature in extras.range_filters:
            self._feature_of_type(feature, "numeric", "ui_extras.voyage_explorer range_filters")
        if extras.name_column is not None and extras.name_column not in self.dataset.display_columns:
            raise ValueError(f"ui_extras.voyage_explorer name_column {extras.name_column!r} is not a display_column.")
        for persona in extras.personas:
            self._check_example(persona, "ui_extras.voyage_explorer persona")
        return self

    @model_validator(mode="after")
    def _rubric_check_scores_a_real_text_feature(self) -> ScenarioDefinition:
        """The rubric check also runs the description through the model as its text
        feature — so that must be a real `type: text` feature, and keys must be unique."""
        check = self.rubric_check
        if check is None:
            return self
        if self.kind != "tabular_ml" or self.dataset is None or check.text_feature not in self.dataset.text_columns():
            raise ValueError("rubric_check.text_feature must be a `type: text` feature of a tabular_ml scenario.")
        keys = [b.key for b in (*check.positive, *check.negative)]
        if len(keys) != len(set(keys)):
            raise ValueError("rubric_check behaviour keys must be unique across positive and negative.")
        return self

    @model_validator(mode="after")
    def _text_challenger_needs_a_text_feature(self) -> ScenarioDefinition:
        """A sentence-embedding challenger only makes sense with free text to embed."""
        if self.model is None or self.model.text_challenger is None:
            return self
        if self.dataset is None or not self.dataset.text_columns():
            raise ValueError("model.text_challenger requires a `type: text` feature in dataset.feature_schema.")
        return self

    @model_validator(mode="after")
    def _tutorial_references_real_columns(self) -> ScenarioDefinition:
        """A tutorial is tabular_ml-only (its widgets read prediction's endpoints); its
        charts must name real columns and its examples must be scoreable."""
        tutorial = self.tutorial
        if tutorial is None:
            return self
        if self.kind != "tabular_ml" or self.dataset is None:
            raise ValueError("`tutorial` is only available for kind='tabular_ml' scenarios.")
        for step in tutorial.steps:
            for chart in step.charts:
                check_chart_columns(chart, self.dataset, f"tutorial step {step.title!r}")
            if "top_terms" in step.widgets and not self.dataset.text_columns():
                raise ValueError(f"tutorial step {step.title!r} shows top_terms but the dataset has no text feature.")
            if "examples" in step.widgets and not tutorial.examples:
                raise ValueError(
                    f"tutorial step {step.title!r} shows the examples widget but tutorial.examples is empty."
                )
        for example in tutorial.examples:
            self._check_example(example, "tutorial example")
        return self

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

    Only files whose raw `kind` matches are validated at all: `scenarios/` is mounted
    live into every service, so a service must never fail to start because *another*
    kind's scenario uses schema it doesn't know yet (e.g. a newly added `kind`, deployed
    before this service's image is rebuilt). platform-registry's `load_all` stays strict.
    """
    wanted = {slug.strip() for slug in raw_scenarios_env.split(",") if slug.strip()}
    definitions = {}
    for path in sorted(scenarios_dir.glob("*/scenario.yaml")):
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict) or raw.get("kind") != kind:
            continue
        definition = ScenarioDefinition.model_validate(raw)
        if not wanted or definition.slug in wanted:
            definitions[definition.slug] = definition
    return definitions
