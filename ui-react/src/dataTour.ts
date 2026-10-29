// The Data Platform's guided tour — platform-level content (the capabilities don't change from
// one data product to another), filled at render time with the data product's own sources,
// metrics and roles. Three depths per step: `simple` (anyone), `detailed` (how this platform
// does it, with its components) and `engineer` (alternatives, contracts, SQL, live JSON).

export type TourDepth = "simple" | "detailed" | "engineer";

export type MapNode =
  | "src_stream"
  | "src_files"
  | "src_db"
  | "kafka"
  | "landing"
  | "cdc"
  | "contract"
  | "quarantine"
  | "bronze"
  | "hot"
  | "lake"
  | "dbt"
  | "silver"
  | "gold"
  | "api"
  | "dashboards"
  | "sql"
  | "assistant"
  | "catalog"
  | "models";

export type TourAction =
  | { kind: "simulate"; action: string; label: string; params?: Record<string, string | number | boolean> }
  | { kind: "viewAs"; label: string }
  | { kind: "chat"; label: string }
  | { kind: "tab"; tab: "flow" | "dashboards" | "explore" | "quality" | "insights" | "shop"; label: string };

export type TourProof =
  | "sources"
  | "capture"
  | "quarantine"
  | "bronze"
  | "hot"
  | "lake"
  | "transform"
  | "metric"
  | "doors"
  | "governance"
  | "models"
  | "capabilities";

export type TourStep = {
  key: string;
  chapter: string;
  title: string;
  nodes: MapNode[];
  simple: string;
  detailed: string;
  engineer: string;
  components: { name: string; role: string; tier: "core" | "analytics" | "bi" | "governance" }[];
  alternatives?: string;
  proof: TourProof;
  actions?: TourAction[];
};

export const TOUR: TourStep[] = [
  {
    key: "sources",
    chapter: "1 · Sources",
    title: "Where the data is born",
    nodes: ["src_stream", "src_files", "src_db"],
    simple:
      "Every business runs on many systems that were never designed to talk to each other. Some send events the moment something happens, some send a file once a day, and some only keep a database up to date.",
    detailed:
      "This data product has {sourceCount} sources of three kinds: live event streams, daily files with a control file saying what was sent, and an operational database. A simulator plays them, so the demo runs anywhere — but it only ever writes through the same doors a real system would.",
    engineer:
      "Sources are declared in scenario.yaml (`data_product.sources`): `kafka` (topic + JSON Schema contract + business key), `files` (landing prefix + control file) and `cdc` (tables of the operational database). The simulator plugin (`ai_circus_shared.data_simulator`) produces through Kafka, the landing bucket and the `data_sources` database only.",
    components: [{ name: "Scenario simulator", role: "plays the source systems", tier: "core" }],
    proof: "sources",
  },
  {
    key: "capture",
    chapter: "2 · Capture",
    title: "Capture: events, files and database changes",
    nodes: ["kafka", "landing", "cdc", "src_stream", "src_files", "src_db"],
    simple:
      "First, nothing gets lost. Live events go into an event log that keeps them in order, files land in a storage area, and changes in the database are picked up automatically, row by row.",
    detailed:
      "Events go to Kafka — one topic per source and per customer organisation. Files land in an object-storage bucket next to their control file. Database changes are read from Postgres' own change log (change data capture), so the source system needs no code change.",
    engineer:
      "Topics are `tenant-<org>.<domain>.<source>.v<N>`: the tenant comes from where a record was published, never from its payload. CDC uses a logical replication slot (test_decoding) on the sources database, with `max_slot_wal_keep_size` so an idle slot can't fill the disk.",
    components: [
      { name: "Apache Kafka (KRaft)", role: "event log", tier: "core" },
      { name: "SeaweedFS (S3)", role: "landing bucket", tier: "core" },
      { name: "Postgres logical replication", role: "change data capture", tier: "core" },
    ],
    alternatives: "Redpanda for Kafka; Debezium for production-grade, multi-database CDC; Ceph or a cloud bucket for S3.",
    proof: "capture",
    actions: [{ kind: "simulate", action: "sell", label: "Ring up a sale", params: { store_id: "S001", promo: true, loyalty: true } }],
  },
  {
    key: "contract",
    chapter: "3 · Quality gate",
    title: "A contract checks every record",
    nodes: ["contract", "quarantine"],
    simple:
      "Before anything is trusted, it is checked. If a receipt doesn't add up, it is not thrown away and it doesn't pollute the numbers: it goes to a quarantine where someone can fix it.",
    detailed:
      "Each source has a contract: the shape every record must have, plus business rules ({ruleCount} here, e.g. “{firstRule}”). A record breaking any of them is quarantined with every reason; a data steward can fix it and replay it through the same path.",
    engineer:
      "Contracts are JSON Schema (Draft 2020-12) files under `contracts/`, plus declared rules (`sum_equals`, `nonzero`, `known_value` against the CDC'd reference tables, `not_future`), all checked by `ai_circus_shared.contracts` in data-ingest. Rejects go to `<slug>.ops_quarantine`; replay re-publishes to the topic.",
    components: [
      { name: "JSON Schema contracts", role: "shape of every record", tier: "core" },
      { name: "data-ingest", role: "validation + quarantine", tier: "core" },
    ],
    alternatives: "Avro/Protobuf with a schema registry (Apicurio, Karapace); Great Expectations or Soda for rule suites.",
    proof: "quarantine",
    actions: [
      { kind: "simulate", action: "sell_bad", label: "Send a broken receipt", params: { store_id: "S001" } },
      { kind: "tab", tab: "explore", label: "Open the quarantine" },
    ],
  },
  {
    key: "bronze",
    chapter: "4 · Bronze",
    title: "Bronze: keep everything, exactly once",
    nodes: ["bronze"],
    simple:
      "Every valid record is stored exactly as it arrived. If a till sends the same receipt twice, it is counted once — and if a record is corrected later, the correction is kept too.",
    detailed:
      "Bronze is the record of truth: one row per business key (and version), with the original payload. Re-sends are recognised by a fingerprint of their content and ignored; corrections replace the row and are counted. You can rebuild everything else from here.",
    engineer:
      "Postgres JSONB table `bronze_<source>` keyed by (org_id, business key, version) with a SHA-256 payload hash: one `INSERT … ON CONFLICT DO UPDATE WHERE hash differs` per batch. Kafka offsets are committed only after the batch is durable — at-least-once delivery, exactly-once in bronze.",
    components: [{ name: "PostgreSQL (JSONB)", role: "bronze record store", tier: "core" }],
    alternatives: "MongoDB or any document store; a raw table in the analytical engine.",
    proof: "bronze",
    actions: [{ kind: "simulate", action: "duplicate", label: "Re-send the last receipt" }],
  },
  {
    key: "hot",
    chapter: "5 · Live tile",
    title: "Numbers that move in seconds",
    nodes: ["hot", "dashboards"],
    simple:
      "Managers want to see today's sales now, not tomorrow. A small live counter is updated as each record arrives — fast, and good enough for a screen on the wall.",
    detailed:
      "A hot path keeps per-minute totals as records land. It is labelled “approximate” on purpose: the curated numbers (gold) are the truth, and the two converge within minutes.",
    engineer:
      "Declared in `data_product.hot_path` (dimensions + measures); data-ingest upserts `<slug>.hot_<name>` per minute for the *first* sighting of each key, so re-sends and corrections never double-count.",
    components: [{ name: "data-ingest hot path", role: "per-minute aggregates", tier: "core" }],
    alternatives: "ClickHouse materialized views, RisingWave or Flink for stateful streaming.",
    proof: "hot",
    actions: [{ kind: "tab", tab: "flow", label: "Watch the live flow" }],
  },
  {
    key: "lake",
    chapter: "6 · Lake",
    title: "An open archive anyone can read",
    nodes: ["lake"],
    simple:
      "A copy of everything goes into an open-format archive. Any tool can read it, years later, even without this platform.",
    detailed:
      "Every bronze record is appended to Apache Iceberg tables (Parquet files) in object storage. That's the reprocessing source and the exit door: DuckDB, Spark or Trino read it directly.",
    engineer:
      "PyIceberg SQL catalog in Postgres, data in bucket `ai-circus-data/lake/<slug>_bronze/<source>`, partitioned by event day, appended every minute; a correction is appended again (newest wins by received_at).",
    components: [
      { name: "Apache Iceberg + Parquet", role: "open table format", tier: "core" },
      { name: "SeaweedFS (S3)", role: "object storage", tier: "core" },
    ],
    alternatives: "Delta Lake or Hudi; plain Parquet when versioning isn't needed; Lakekeeper or Polaris as REST catalog.",
    proof: "lake",
  },
  {
    key: "transform",
    chapter: "7 · Transform",
    title: "Transformations as code, with tests",
    nodes: ["dbt", "silver"],
    simple:
      "Raw data is cleaned, joined and checked by a recipe written as code — so every number can be traced, reviewed and rebuilt. If a check fails, the bad numbers never reach the dashboards.",
    detailed:
      "dbt turns bronze into silver (one clean model across channels, personal ids replaced by a pseudonym, prices as they were on the day of the sale) and gold. Reference data lives in the project too — the store coordinates behind the dashboards' map are a versioned CSV (a dbt seed). Every build runs its tests; on the analytics tier Dagster triggers it whenever new data arrives.",
    engineer:
      "One dbt project per data product (`scenarios/<slug>/dbt/`): incremental silver with a 10-minute lookback, SCD2 product dimension from the CDC change log, reference data as dbt seeds (`seeds/city_coordinates.csv` → `mart_stores`), HMAC-SHA256 pseudonymisation via a private Postgres function (the key never appears in SQL), reconciliation tests bronze→silver→gold. Core: CronJob; analytics: Dagster assets + sensor + asset checks.",
    components: [
      { name: "dbt-core", role: "SQL models + tests", tier: "core" },
      { name: "Dagster", role: "orchestration", tier: "analytics" },
    ],
    alternatives: "SQLMesh for dbt; Airflow or Prefect for Dagster; Kubernetes CronJobs for simple schedules.",
    proof: "transform",
    actions: [{ kind: "tab", tab: "quality", label: "See runs, tests and lineage" }],
  },
  {
    key: "gold",
    chapter: "8 · Gold",
    title: "Gold marts and governed metrics",
    nodes: ["gold"],
    simple:
      "The business asks questions like “how much did we sell this week?”. Gold tables answer them fast, and each metric has one single definition that every screen and the assistant share.",
    detailed:
      "Gold marts (daily sales, top products, promotions, click & collect, returns, stores on the map) are built for the questions people ask. Metrics like “{metricLabel}” are declared once and served by name — so a dashboard and the assistant can never disagree.",
    engineer:
      "Marts are tables in `<slug>_gold` on Postgres (core) or ClickHouse (analytics: columnar copies of silver read through ClickHouse's PostgreSQL engine). Metrics (`data_product.metrics`) are SQL aggregates over a declared dataset with whitelisted dimensions — the semantic layer data-api, Superset and the assistant all use.",
    components: [
      { name: "PostgreSQL", role: "marts (core)", tier: "core" },
      { name: "ClickHouse", role: "columnar marts", tier: "analytics" },
    ],
    alternatives: "DuckDB for single-node analytics; StarRocks or Doris; Cube or MetricFlow as the semantic layer.",
    proof: "metric",
    actions: [{ kind: "tab", tab: "dashboards", label: "Open the dashboards" }],
  },
  {
    key: "serve",
    chapter: "9 · Serve",
    title: "One dataset, many doors",
    nodes: ["api", "dashboards", "sql", "assistant"],
    simple:
      "The same trusted data reaches everyone the way they need it: dashboards and a map of every store for managers, SQL for analysts, an API for apps, and an assistant you can just ask.",
    detailed:
      "Every door goes through the same governed API: records with their processing trail, metrics, read-only SQL over the curated tables, live events — and the assistant uses exactly those tools, so its numbers are the dashboard's numbers.",
    engineer:
      "data-api (FastAPI): `/metrics/{name}`, `/records/{entity}/{key}`, `/query` (sqlglot rewrites every table to its tenant-filtered subquery, read-only, timeout, row cap), `/events/stream` (SSE), `/lineage`. The assistant's LangGraph tools call it with the user's own token; Superset reads the marts with the same metric definitions.",
    components: [
      { name: "data-api", role: "governed REST + SQL + SSE", tier: "core" },
      { name: "Assistant (LangGraph + LiteLLM)", role: "questions in plain words", tier: "core" },
      { name: "Apache Superset", role: "BI dashboards", tier: "bi" },
    ],
    alternatives: "Metabase, Grafana or Lightdash for BI; an MCP server for external agents.",
    proof: "doors",
    actions: [
      { kind: "chat", label: "Ask the assistant" },
      { kind: "tab", tab: "explore", label: "Try governed SQL" },
    ],
  },
  {
    key: "learn",
    chapter: "10 · Learn",
    title: "Machine learning on trusted data",
    nodes: ["gold", "models", "assistant"],
    simple:
      "Five years of clean, tested sales are exactly what a model needs. The platform trains one to forecast next week's sales of every shop, and another to spot the customers about to buy a new board — and the web shop recommends what riders buy together.",
    detailed:
      "dbt builds feature tables in gold (one row per shop × rider group × week; one per pseudonymised customer). After every successful build they become the training data of two models (LightGBM, explained with SHAP, tracked in MLflow) served by the prediction service; the assistant and the control room ask them for forecasts, and association rules over every basket power the web shop's recommendations.",
    engineer:
      "`data_product.ml_exports` maps a gold feature mart to a `tabular_ml` scenario: data-transform writes it as that scenario's raw CSV per tenant, then etl-tabular → training (LightGBM, SHAP, 90% quantile interval, MLflow) → prediction, unchanged. Lags are joins on week offsets so the same SQL runs on Postgres and ClickHouse; the latest complete week is the row forecast now. Recommendations are a gold mart of association rules (support, confidence, lift) read through the same governed API.",
    components: [
      { name: "dbt feature marts", role: "training data", tier: "core" },
      { name: "LightGBM + SHAP", role: "models + explanations", tier: "core" },
      { name: "MLflow", role: "experiment tracking", tier: "core" },
    ],
    alternatives: "Feast for a feature store; Kubeflow or Metaflow for pipelines; BentoML or KServe for serving.",
    proof: "models",
    actions: [
      { kind: "tab", tab: "insights", label: "Open the control room" },
      { kind: "tab", tab: "shop", label: "Try the web shop" },
    ],
  },
  {
    key: "governance",
    chapter: "11 · Governance",
    title: "Who sees what — everywhere at once",
    nodes: ["catalog", "api"],
    simple:
      "A store manager should only see their own store, and nobody should see customers' personal data. Those rules are written once and applied on every screen, every query and every answer of the assistant.",
    detailed:
      "Each organisation only ever sees its own data. Inside it, roles decide rows and columns: {roleList}. A catalogue lists every table, topic, model and dashboard with its lineage, owner and quality.",
    engineer:
      "`org_id` is the first predicate of every query; `ai_circus_shared.data_policy` turns Keycloak roles + token claims (`stores`) into row filters and masks for the API, SQL, MCP and the assistant; Superset row-level security mirrors them. OpenMetadata ingests Postgres, Kafka (with the contracts as topic schemas), ClickHouse, dbt and Superset, plus capture lineage and a Data Product entity.",
    components: [
      { name: "Keycloak", role: "identity + organisations", tier: "core" },
      { name: "data_policy", role: "row filters + masks", tier: "core" },
      { name: "OpenMetadata", role: "catalogue + lineage", tier: "governance" },
    ],
    alternatives: "DataHub or Marquez/OpenLineage for the catalogue; any OIDC provider for identity.",
    proof: "governance",
    actions: [{ kind: "viewAs", label: "View as the Tarifa (S001) shop manager" }],
  },
  {
    key: "capabilities",
    chapter: "12 · The stack",
    title: "Capabilities fixed, components interchangeable",
    nodes: [],
    simple:
      "Everything you just saw is open source. Each capability is what matters; the tool behind it can be swapped for another one your organisation already uses.",
    detailed:
      "The WHAT (capability) is fixed; the HOW (component) is a reference choice with open-source alternatives, chosen per context. Tiers switch on one by one, so the whole stack fits on one laptop.",
    engineer:
      "Each tier is a kustomize overlay (`k8s/data-platform/{core,analytics,bi,governance}`); every choice is an ADR in docs/adr. Status below is live: which tiers this cluster is running right now.",
    components: [],
    proof: "capabilities",
  },
];

/** The data stack capability map (WHAT → reference HOW → alternatives), with the tier that runs it. */
export const CAPABILITIES: { capability: string; reference: string; alternatives: string; tier: "core" | "analytics" | "bi" | "governance" | "platform" }[] = [
  { capability: "Object storage", reference: "SeaweedFS (S3)", alternatives: "Ceph · cloud object stores (MinIO avoided: end-of-life risk)", tier: "core" },
  { capability: "File / table formats", reference: "Parquet + Apache Iceberg (PyIceberg, catalog in Postgres)", alternatives: "Delta Lake · Hudi", tier: "core" },
  { capability: "Event streaming", reference: "Apache Kafka (KRaft)", alternatives: "Redpanda · Pulsar", tier: "core" },
  { capability: "Change data capture", reference: "Postgres logical replication", alternatives: "Debezium (production, multi-source)", tier: "core" },
  { capability: "Contracts & data quality", reference: "JSON Schema + record rules + dbt tests", alternatives: "Avro + schema registry · Great Expectations · Soda", tier: "core" },
  { capability: "Relational / serving store", reference: "PostgreSQL (+ row-level policies)", alternatives: "—", tier: "core" },
  { capability: "Transformation as code", reference: "dbt-core", alternatives: "SQLMesh", tier: "core" },
  { capability: "Pipeline orchestration", reference: "Kubernetes CronJobs → Dagster", alternatives: "Apache Airflow · Prefect", tier: "analytics" },
  { capability: "Analytical engine", reference: "ClickHouse (marts) · DuckDB (embedded)", alternatives: "Apache Spark · StarRocks · Doris", tier: "analytics" },
  { capability: "Semantic layer", reference: "Governed metrics in scenario.yaml, served by data-api", alternatives: "Cube · MetricFlow", tier: "core" },
  { capability: "BI / dashboards", reference: "Apache Superset + native React dashboards", alternatives: "Metabase · Grafana · Lightdash", tier: "bi" },
  { capability: "Catalogue & lineage", reference: "OpenMetadata", alternatives: "DataHub · OpenLineage + Marquez", tier: "governance" },
  { capability: "Identity & tenancy", reference: "Keycloak (organisations) + data_policy", alternatives: "Any OIDC provider", tier: "platform" },
  { capability: "AI on the data", reference: "LiteLLM gateway + LangGraph assistant with governed tools", alternatives: "MCP server for external agents", tier: "platform" },
  { capability: "Runtime & ingress", reference: "Kubernetes (k3s) + Traefik", alternatives: "OpenShift · AKS/EKS/GKE · k0s", tier: "platform" },
];

export const DEPTH_LABELS: Record<TourDepth, string> = { simple: "Simple", detailed: "Detailed", engineer: "Engineer" };
