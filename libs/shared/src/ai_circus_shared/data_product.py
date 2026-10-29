"""
- Title:    `data_product` scenario schema — sources, bronze, transform, serving, access
- Author:   Angel Martinez-Tenor

The `data_product` block of a `kind: data_product` scenario (see docs/data-platform/ and
docs/adr/0002). It is read by the generic data-tier services — `data-ingest` (sources,
contracts, hot path), `data-transform` (the dbt project), `data-api` (datasets, metrics,
records, access) — and by platform-registry, which serves it to ui-react's DataProductView.
Nothing in here is domain-specific: a retail, logistics or banking data product differ only
in the YAML (and in the files next to it: contracts, simulator, dbt project).

Every identifier that ends up in SQL (dataset, column, dimension, metric and source names) is
constrained to `SQL_IDENTIFIER` at load time, so data-api can quote them without ever
interpolating anything a caller sent; metric `expression`s are SQL written by the scenario
author (repository content, reviewed like code — never a request parameter).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

SQL_IDENTIFIER = r"^[a-z][a-z0-9_]{0,47}$"
Identifier = Annotated[str, Field(pattern=SQL_IDENTIFIER)]
# A dotted path into a JSON payload; `[*]` fans out over an array ("lines[*].qty").
PayloadPath = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\[\*\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\*\])?)*$")]
TOPIC = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){2,}\.v[0-9]+$"

# Fixed, generic stages every record can report through `platform.pipeline.events.v1`
# (see ai_circus_shared.pipeline_events); a scenario only relabels them.
FLOW_STAGES = ("produced", "event_log", "validated", "bronze", "hot", "lake", "transformed", "gold", "served")
PIPELINE_EVENTS_TOPIC = "platform.pipeline.events.v1"
DATA_BUCKET = "ai-circus-data"
# Databases inside the shared Postgres: the platform's own (bronze/silver/gold/hot/ops) and
# the one that plays the data product's *operational source systems* (an ERP, a CRM) — kept
# apart so the CDC replication slot only decodes source-system changes, never bronze writes.
WAREHOUSE_DATABASE = "data_platform"
SOURCES_DATABASE = "data_sources"


class ContractRule(BaseModel):
    """A record-level check beyond JSON Schema, applied by data-ingest before bronze.

    - `sum_equals`: the values at `items` (an array path) add up to `total`.
    - `nonzero`: every value at `field` is non-zero.
    - `known_value`: the value at `field` exists in a CDC table column (`reference` =
      "<table>.<column>", from the data product's own operational source).
    - `not_future`: the timestamp at `field` is at most `tolerance_seconds` ahead of now.
    """

    name: Identifier
    type: Literal["sum_equals", "nonzero", "known_value", "not_future"]
    description: str
    items: PayloadPath | None = None
    total: PayloadPath | None = None
    field: PayloadPath | None = None
    reference: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")] | None = None
    tolerance_seconds: int = Field(default=300, ge=0, le=86_400)

    @model_validator(mode="after")
    def _arguments_match_type(self) -> ContractRule:
        needed = {
            "sum_equals": ("items", "total"),
            "nonzero": ("field",),
            "known_value": ("field", "reference"),
            "not_future": ("field",),
        }[self.type]
        missing = [name for name in needed if getattr(self, name) is None]
        if missing:
            raise ValueError(f"rule {self.name!r} ({self.type}) needs {missing}.")
        return self


class KafkaSource(BaseModel):
    """Events published to a Kafka topic, one contract per topic."""

    id: Identifier
    type: Literal["kafka"] = "kafka"
    label: str
    topic: Annotated[str, Field(pattern=TOPIC)]
    contract: str  # path relative to the scenario directory
    business_key: PayloadPath
    event_time: PayloadPath
    # Records whose key repeats with a higher version supersede the earlier ones (an
    # order's status updates); None = one record per key (a receipt).
    version_field: PayloadPath | None = None
    rules: list[ContractRule] = []


class FileControl(BaseModel):
    """The control file delivered next to each data file: what the sender says it sent."""

    file_name: str = "_control.json"
    count_field: str
    sum_field: str
    sum_path: PayloadPath  # summed over the loaded records, compared with sum_field


class FileSource(BaseModel):
    """Daily files dropped into the landing bucket (one folder per delivery), reconciled
    against a control file and landed into the same bronze table as `lands_in`."""

    id: Identifier
    type: Literal["files"] = "files"
    label: str
    prefix: Annotated[str, Field(pattern=r"^[a-z0-9_]+/$")]  # under landing/<org_id>/<slug>/
    data_file: str = "records.jsonl"
    lands_in: Identifier  # a kafka source id — same contract, same bronze table
    control: FileControl


class CdcTable(BaseModel):
    name: Identifier
    key: Identifier
    label: str


class CdcSource(BaseModel):
    """Tables of the data product's operational database captured by logical replication."""

    id: Identifier
    type: Literal["cdc"] = "cdc"
    label: str
    tables: list[CdcTable] = Field(min_length=1)


DataSource = Annotated[KafkaSource | FileSource | CdcSource, Field(discriminator="type")]


class Measure(BaseModel):
    name: Identifier
    agg: Literal["count", "sum"]
    path: PayloadPath | None = None  # required for sum

    @model_validator(mode="after")
    def _sum_needs_a_path(self) -> Measure:
        if self.agg == "sum" and self.path is None:
            raise ValueError(f"measure {self.name!r}: agg=sum needs a path.")
        return self


class HotAggregate(BaseModel):
    """A per-minute aggregate kept up to date as records arrive (approximate, seconds)."""

    name: Identifier
    label: str
    sources: list[Identifier] = Field(min_length=1)  # kafka source ids
    dimensions: dict[Identifier, PayloadPath]  # column -> payload path
    measures: list[Measure] = Field(min_length=1)


class DatasetSpec(BaseModel):
    """A curated table data-api may serve (built by the dbt project)."""

    name: Identifier
    layer: Literal["silver", "gold", "ops"]
    label: str
    description: str
    grain: str
    time_column: Identifier | None = None
    freshness_minutes: int | None = Field(default=None, ge=1)


class MetricSpec(BaseModel):
    """A governed metric — the one definition BI, the API and the assistant share."""

    name: Identifier
    label: str
    description: str
    dataset: Identifier
    expression: str = Field(max_length=500)  # a SQL aggregate over the dataset's columns
    format: Literal["currency_cents", "integer", "number", "percent"] = "number"
    dimensions: list[Identifier] = []

    @model_validator(mode="after")
    def _expression_is_a_plain_aggregate(self) -> MetricSpec:
        # Repository content, but keep it to an aggregate expression: no statements,
        # comments or subqueries that could escape data-api's query template.
        if re.search(r";|--|/\*|\bselect\b|\bfrom\b|\bjoin\b", self.expression, re.IGNORECASE):
            raise ValueError(f"metric {self.name!r}: expression must be a plain aggregate expression.")
        return self


class RecordSpec(BaseModel):
    """An entity looked up by key straight from bronze (as received) with its processing trail."""

    entity: Identifier
    label: str
    source: Identifier  # kafka source id (its bronze table)
    summary: dict[str, PayloadPath]  # label -> payload path, shown in lists
    search: list[PayloadPath] = []  # payload paths matched by free-text search


class RowFilterLabels(BaseModel):
    """Where readable names of the row-filter values live (e.g. store_id S012 -> "Acme Málaga"):
    a declared dataset holding the filter column and `label`. data-api reads it with the
    caller's own access, so a role only ever learns the names of the rows it may see."""

    dataset: Identifier
    label: Identifier


class RowFilter(BaseModel):
    """Restrict rows by a claim of the caller's token (e.g. column store_id, claim stores)."""

    column: Identifier
    claim: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    labels: RowFilterLabels | None = None


class RoleAccess(BaseModel):
    label: str
    rows: Literal["all", "filtered"] = "all"
    hidden: list[Identifier] = []  # columns / payload fields removed from every answer
    layers: list[Literal["bronze", "silver", "gold", "ops", "quarantine"]] = ["silver", "gold"]
    sql: bool = False  # may run governed SQL


class AccessConfig(BaseModel):
    """Who sees what *inside* a tenant (tenancy itself is always `org_id`, see ADR 0008)."""

    row_filter: RowFilter | None = None
    roles: dict[Identifier, RoleAccess] = Field(min_length=1)
    # A Keycloak user holding none of `roles` acts as `default_role`; the platform's own
    # API-key identities (admin, scoped demo tenants) act as `admin_role` and may "view as"
    # any other role to demonstrate the restrictions (see ai_circus_shared.data_policy).
    default_role: Identifier
    admin_role: Identifier
    # Payload fields pseudonymised with HMAC-SHA256 in silver and masked in bronze answers
    # for every role without the bronze layer.
    pseudonymise: list[Identifier] = []

    @model_validator(mode="after")
    def _default_role_exists(self) -> AccessConfig:
        for which in ("default_role", "admin_role"):
            if getattr(self, which) not in self.roles:
                raise ValueError(f"access.{which} {getattr(self, which)!r} is not among access.roles.")
        if self.row_filter is None and any(r.rows == "filtered" for r in self.roles.values()):
            raise ValueError("a role has rows: filtered but access.row_filter is not set.")
        return self


class SimulatorParam(BaseModel):
    name: Identifier
    label: str
    # text: a short string (max_length); items: a bounded list of {id, qty} — e.g. a web-shop cart.
    type: Literal["choice", "int", "bool", "text", "items"]
    options: list[str] = []
    default: str | int | bool | None = None
    min: int | None = None
    max: int | None = None


class SimulatorAction(BaseModel):
    """A named, on-demand event the UI's Simulate panel offers (a sale, a bad record…)."""

    name: Identifier
    label: str
    description: str
    params: list[SimulatorParam] = []
    tone: Literal["normal", "warning"] = "normal"
    # Not offered in the Simulate panel: called by an app of the scenario (e.g. its web shop).
    hidden: bool = False


class SimulatorConfig(BaseModel):
    package: str = "simulator"  # directory under the scenario holding the plugin
    seed: int = 42
    history_days: int = Field(default=60, ge=1, le=2000)
    live_rate_per_second: float = Field(default=1.0, gt=0, le=50)
    actions: list[SimulatorAction] = []


class MapPlaces(BaseModel):
    """The dataset that places a map panel's bubbles: one row per key with its coordinates."""

    dataset: Identifier
    key: Identifier
    label: Identifier
    lat: Identifier
    lon: Identifier
    group: Identifier | None = None  # colour of the bubble (e.g. region)
    # Fixed colour per group (a theme colour), so a group keeps its colour for every role.
    group_colours: dict[str, Literal["blue", "teal", "green", "amber", "red", "pink", "purple", "lime"]] = {}


class DashboardPanel(BaseModel):
    """One chart of the data product's native dashboard (ui-react's DataDashboardView): a
    governed metric split by up to two of its dimensions — never free SQL."""

    title: str
    metric: Identifier
    dims: list[Identifier] = Field(default=[], max_length=2)
    chart: Literal["line", "bar", "hbar", "pie", "map"] = "bar"
    # chart: map — one bubble per place, `metric` (or any of `metrics`, switchable) split by
    # the single dim `places.key`; the coordinates come from the `places` dataset.
    metrics: list[Identifier] = Field(default=[], max_length=8)
    places: MapPlaces | None = None
    period: str | None = None  # None = the dashboard's period selector
    compare: bool = False  # bars next to the comparison period's
    limit: int = Field(default=12, ge=1, le=60)
    wide: bool = False


class StorefrontFields(BaseModel):
    """Columns of the catalogue dataset the web shop reads."""

    id: Identifier
    name: Identifier
    price: Identifier  # cents
    group: Identifier  # aisle / category
    brand: Identifier | None = None
    subtitle: Identifier | None = None  # e.g. size
    rank: Identifier | None = None  # best-seller rank within its group (1 = best)
    available: Identifier | None = None  # boolean: still on sale
    # Drawing: ui-react's BoardArt draws `shape` in `colour` at `length` (no product photos).
    image_shape: Identifier | None = None
    image_colour: Identifier | None = None
    image_length: Identifier | None = None
    details: list[Identifier] = Field(default=[], max_length=8)  # extra attributes on the product page


class StorefrontApp(BaseModel):
    """A fictional web shop on top of the data product: browse the catalogue dataset, get
    recommendations from a rules dataset and check out through a (hidden) simulator action —
    the order then travels the real pipeline (contract, Kafka, bronze, dbt, gold)."""

    title: str
    tagline: str = ""
    disclaimer: str
    catalogue: Identifier  # dataset
    fields: StorefrontFields
    recommendations: Identifier | None = None  # dataset of rules: key column = fields.id
    rec_id: Identifier = "rec_sku"
    rec_evidence: list[Identifier] = Field(default=["together", "confidence", "lift"], max_length=4)
    checkout_action: Identifier
    order_entity: Identifier  # the record entity an order is tracked as


class MlExport(BaseModel):
    """A gold feature mart that trains a `tabular_ml` scenario: after every successful build,
    data-transform writes it (that scenario's index, features and target; rows with a target
    only) as the scenario's raw dataset for every tenant with data — etl-tabular, training and
    prediction then run unchanged."""

    dataset: Identifier  # a gold dataset of this data product
    scenario: Identifier  # the tabular_ml scenario's slug


class SeasonHeatmap(BaseModel):
    """A metric over all its history: one row per value of `rows`, one cell per time bucket."""

    title: str
    metric: Identifier  # split by its dataset's time column and `rows`
    rows: Identifier  # a dimension of the metric (e.g. rider_group)


class ForecastBoard(BaseModel):
    """Live forecasts: the `latest_flag` rows of an ml_export's feature mart, scored by its model
    through the prediction service and laid out as a `row` × `column` board."""

    title: str
    model: Identifier  # an ml_exports scenario
    latest_flag: Identifier  # boolean column of the feature mart: the rows to forecast now
    row: Identifier
    column: Identifier
    baseline: Identifier  # the value the forecast is compared with (e.g. units_this_week)


class InsightsApp(BaseModel):
    """The data product's own control room (ui-react InsightsView)."""

    title: str
    subtitle: str = ""
    heatmap: SeasonHeatmap | None = None
    forecast: ForecastBoard | None = None


class DataProductApps(BaseModel):
    storefront: StorefrontApp | None = None
    insights: InsightsApp | None = None


class TransformConfig(BaseModel):
    dbt_project: str = "dbt"
    interval_minutes: int = Field(default=5, ge=1, le=1440)


class DataProductConfig(BaseModel):
    """The `data_product:` block of a `kind: data_product` scenario."""

    domain: Identifier  # topic prefix and namespace hint (e.g. "retail")
    sources: list[DataSource] = Field(min_length=1)
    hot_path: list[HotAggregate] = []
    transform: TransformConfig = TransformConfig()
    datasets: list[DatasetSpec] = Field(min_length=1)
    metrics: list[MetricSpec] = []
    records: list[RecordSpec] = []
    access: AccessConfig
    simulator: SimulatorConfig | None = None
    stage_labels: dict[str, str] = {}
    kpis: list[Identifier] = []  # headline metrics of the native dashboard
    dashboard: list[DashboardPanel] = []
    bi_dashboards: str | None = None  # path to bi/dashboards.yaml (bi tier)
    apps: DataProductApps = DataProductApps()
    ml_exports: list[MlExport] = []

    def kafka_sources(self) -> list[KafkaSource]:
        return [s for s in self.sources if isinstance(s, KafkaSource)]

    def file_sources(self) -> list[FileSource]:
        return [s for s in self.sources if isinstance(s, FileSource)]

    def cdc_sources(self) -> list[CdcSource]:
        return [s for s in self.sources if isinstance(s, CdcSource)]

    def source(self, source_id: str) -> KafkaSource:
        for s in self.kafka_sources():
            if s.id == source_id:
                return s
        raise KeyError(source_id)

    def dataset(self, name: str) -> DatasetSpec:
        for d in self.datasets:
            if d.name == name:
                return d
        raise KeyError(name)

    @model_validator(mode="after")
    def _references_resolve(self) -> DataProductConfig:
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError(f"data_product.sources ids must be unique: {ids}")
        kafka = {s.id for s in self.kafka_sources()}
        topics = [s.topic for s in self.kafka_sources()]
        if len(topics) != len(set(topics)):
            raise ValueError("two kafka sources share a topic.")
        cdc_tables = {t.name for s in self.cdc_sources() for t in s.tables}
        for s in self.kafka_sources():
            if not s.topic.startswith(f"{self.domain}."):
                raise ValueError(f"source {s.id!r}: topic must start with the domain {self.domain!r}.")
            for rule in s.rules:
                if rule.type == "known_value" and rule.reference.split(".")[0] not in cdc_tables:  # type: ignore[union-attr]
                    raise ValueError(f"rule {rule.name!r}: reference table is not a CDC table.")
        for f in self.file_sources():
            if f.lands_in not in kafka:
                raise ValueError(f"file source {f.id!r}: lands_in {f.lands_in!r} is not a kafka source.")
        for agg in self.hot_path:
            unknown = set(agg.sources) - kafka
            if unknown:
                raise ValueError(f"hot_path {agg.name!r}: unknown sources {sorted(unknown)}.")
        datasets = {d.name for d in self.datasets}
        if len(datasets) != len(self.datasets):
            raise ValueError("data_product.datasets names must be unique.")
        for metric in self.metrics:
            if metric.dataset not in datasets:
                raise ValueError(f"metric {metric.name!r}: dataset {metric.dataset!r} is not declared.")
        if len({m.name for m in self.metrics}) != len(self.metrics):
            raise ValueError("data_product.metrics names must be unique.")
        for record in self.records:
            if record.source not in kafka:
                raise ValueError(f"record {record.entity!r}: source {record.source!r} is not a kafka source.")
        metrics = {m.name: m for m in self.metrics}
        for kpi in self.kpis:
            if kpi not in metrics:
                raise ValueError(f"kpis: {kpi!r} is not a declared metric.")
        row_filter = self.access.row_filter
        if row_filter and row_filter.labels and row_filter.labels.dataset not in datasets:
            raise ValueError(f"access.row_filter.labels: dataset {row_filter.labels.dataset!r} is not declared.")
        for panel in self.dashboard:
            for name in [panel.metric, *panel.metrics]:
                metric = metrics.get(name)
                if metric is None:
                    raise ValueError(f"dashboard panel {panel.title!r}: unknown metric {name!r}.")
                time_column = self.dataset(metric.dataset).time_column
                allowed = set(metric.dimensions) | ({time_column} if time_column else set())
                unknown = [d for d in panel.dims if d not in allowed]
                if unknown:
                    raise ValueError(f"dashboard panel {panel.title!r}: {unknown} are not dimensions of {name!r}.")
            if panel.chart == "map":
                if panel.places is None or panel.dims != [panel.places.key]:
                    raise ValueError(f"dashboard panel {panel.title!r}: a map needs places and dims == [places.key].")
                if panel.places.dataset not in datasets:
                    raise ValueError(f"dashboard panel {panel.title!r}: places dataset is not declared.")
            elif panel.places is not None or panel.metrics:
                raise ValueError(f"dashboard panel {panel.title!r}: places/metrics are for chart: map only.")
        for export in self.ml_exports:
            if export.dataset not in datasets or self.dataset(export.dataset).layer != "gold":
                raise ValueError(f"ml_exports: {export.dataset!r} is not a declared gold dataset.")
        insights = self.apps.insights
        if insights is not None and insights.heatmap is not None:
            metric = metrics.get(insights.heatmap.metric)
            if metric is None or insights.heatmap.rows not in metric.dimensions:
                raise ValueError("apps.insights.heatmap: unknown metric or `rows` is not one of its dimensions.")
        if insights is not None and insights.forecast is not None:
            if insights.forecast.model not in {e.scenario for e in self.ml_exports}:
                raise ValueError("apps.insights.forecast: model is not an ml_exports scenario.")
        shop = self.apps.storefront
        if shop is not None:
            for dataset in [shop.catalogue, *([shop.recommendations] if shop.recommendations else [])]:
                if dataset not in datasets:
                    raise ValueError(f"apps.storefront: dataset {dataset!r} is not declared.")
            actions = {a.name: a for a in (self.simulator.actions if self.simulator else [])}
            if shop.checkout_action not in actions:
                raise ValueError(f"apps.storefront: checkout_action {shop.checkout_action!r} is not a simulator action.")
            if shop.order_entity not in {r.entity for r in self.records}:
                raise ValueError(f"apps.storefront: order_entity {shop.order_entity!r} is not a record entity.")
        unknown_stages = set(self.stage_labels) - set(FLOW_STAGES)
        if unknown_stages:
            raise ValueError(f"stage_labels has unknown stages {sorted(unknown_stages)}; known: {FLOW_STAGES}.")
        return self


def check_files(config: DataProductConfig, scenario_dir: Path) -> list[str]:
    """Every file the block points at, relative to the scenario directory, that is missing
    (an empty list when all exist). A separate step from validation because the schema
    alone doesn't know where the YAML lives; the libs/shared tests run it on every
    shipped data product."""
    paths = [s.contract for s in config.kafka_sources()]
    paths.append(f"{config.transform.dbt_project}/dbt_project.yml")
    if config.simulator is not None:
        paths.append(f"{config.simulator.package}/__init__.py")
    if config.bi_dashboards is not None:
        paths.append(config.bi_dashboards)
    return [p for p in paths if not (scenario_dir / p).is_file()]


def bronze_table(source_id: str) -> str:
    return f"bronze_{source_id}"


def hot_table(name: str) -> str:
    return f"hot_{name}"


def layer_schema(slug: str, layer: str) -> str:
    """Where a layer's tables live (a Postgres schema / a ClickHouse database): dbt builds
    silver and gold into `<slug>_silver` / `<slug>_gold`; data-ingest owns `<slug>`, which
    also holds the ops tables."""
    return {"silver": f"{slug}_silver", "gold": f"{slug}_gold"}.get(layer, slug)


TRANSFORM_RUNS_TABLE = "ops_transform_runs"  # in schema `<slug>`, written by data-transform
