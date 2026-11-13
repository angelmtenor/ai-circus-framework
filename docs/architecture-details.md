# Architecture details

How each feature works, for when you touch it. `CLAUDE.md` holds the rules that apply to every task and points
here; a rule in `CLAUDE.md` wins if the two ever differ.

## Scenarios and the UI's generic renderers

**Scenario-driven, not per-feature code.** A scenario (`scenarios/<slug>/scenario.yaml`,
schema in `ai_circus_shared/scenario_schema.py`) is the unit of product content, of one `kind`:
`tabular_ml`, `conversational_rag`, `assisted_form`, `deep_learning`. Adding one is a YAML file
plus a `platform-registry` restart (it seeds scenarios) — never new UI or service code. **One
service instance serves every scenario of its kind**, routed by a `{scenario_slug}` path segment:
`prediction`/`assistant` (tabular_ml), `rag-agent` (conversational_rag), `form-agent`
(assisted_form), optional `dl-inference` (deep_learning; onnxruntime only, models from the
one-shot `dl-training`, artifact contract in `ai_circus_shared/deep_learning.py`). A
`deep_learning` scenario is `task: classification` (fine-tune) or `task: anomaly_detection`
(frozen DINOv2 + patch memory bank; same ONNX interface plus an `anomaly_map` output). A classification scenario may read **frame sequences** (`source.type: http_frame_sequences`: per-camera `.tgz` of
`<ts>_<±offset s>.jpg`, label from the offset, split by camera/station so test cameras are never seen; `clear_only`
archives are no-event controls): dl-training publishes each sample's `group` (camera) and `seq` in recording order,
and `ui_extras: camera_wall` (`wildfire_smoke_watch`, `CameraWallView.tsx` + `cameraWallLogic.ts`) plays a pool of them on
one clock, really scoring every frame through dl-inference (`live` vs `replay`), with an in-app camera picker, an alert
rule (threshold, N frames, `early_grace_seconds`) and `control_margin_seconds` for controls. `ui-react`
mirrors this: `ScenarioPicker` renders whatever the entitlements API returns; `TabularView`/
`RagView`/`AssistedFormView`/`DeepLearningView` are generic renderers driven by each scenario's
`ScenarioSummary`; an optional 5th tab comes from `ui_extras` (`region_map`, `live_plant`,
`process_optimizer`, `risk_watchlist`, `network_explorer`, `dispatch_tower`, `shipment_globe`, `money_trail`, `camera_wall`, …). A `tutorial:` block (tabular_ml) adds a guided Tutorial tab; `industry` is the
scenario's *domain* (industries plus `tutorial`/`society_ethics`), shown as the picker's Domain filter. `scenario.yaml` is otherwise read only by `platform-registry` and, as
build-time config, `etl-tabular`/`training`/`prediction`.

## Official paper forms (`assisted_form`)

An `assisted_form` whose `form` has `sections` renders as an official paper sheet
(`OfficialFormSheet.tsx`: numbered boxes/`casilla`, `variants` = general + specific models chosen by
the classification field, `locale` for messages); form-agent prints the same YAML as PDF
(`core/pdf.py`, reportlab: draft + filed copy with receipt) and serves `form.sample_uploads`.

## Forecasts (`task_type: forecast`)

A tabular scenario can be a **forecast** (`model.task_type: forecast` + `time_series:`, ADR 0016, first user
`energy_building`): etl-tabular's `clean_series` fails on gaps instead of dropping rows; training backtests
`seasonal_naive`/`auto_ets`/`lightgbm_lags` by MASE (features in `ai_circus_shared.forecasting`, shared with prediction so
they cannot skew; no Nixtla libraries, they pin `pandas<3`); prediction serves `GET /forecast/{slug}`, `POST
/forecast/{slug}/what-if` and `GET /model/{slug}/backtest`, and row-scoring routes answer 409; the assistant gets
`get_forecast`/`get_forecast_backtest`; `ForecastViews.tsx` replaces the model tab's Predictions/Insights and adds `ui_extras: forecast_board`.

**Several models in one scenario** (`extra_models:`, ADR 0018): each model is a validated view of the scenario, addressed as
`slug~id` (bare slug = main model), with its own dataset and bucket. etl-tabular, training and prediction loop over the views;
entitlement is to the scenario; the assistant gets every model's tools (an extra model's with `_<id>` appended); the UI has one
tab per model with a Predictions/Insights switch.

**Recommenders** (`model.task_type: recommendation` + `recommender:`, first user `surf_ml~recommender`): baskets in, no customers.
`training/core/recommender.py` scores `association_rules` and `item_factors` (truncated SVD) on items hidden from held-out
baskets, next to a popularity baseline (both rank by share of an item's baskets; ranking by lift or cosine found half as many hidden items); every candidate is served side by side by `GET /recommend/{slug}` and `/items`.

## Fleet health, row triage, case tracker, source library

`ui_extras: fleet_health` (`FleetHealthView.tsx`, DOM and CSS only, no frame loop) replays real dataset rows unit by unit,
scoring each unit's current row at every tick; health is the model's score, or actual minus predicted when the target is
not itself a health measure (`health.value: residual`). Its thresholds should come from the real distribution.
`ui_extras: row_triage` (`RowTriageView.tsx`) routes rows by a threshold over the precomputed out-of-fold scores, so a tick
calls nothing. **A new tab must leave the page quiet**: prefer DOM/SVG over canvas, cap what it draws, and measure it idle.
An `assisted_form` may add `ui_extras: case_tracker` (`CaseTrackerView.tsx`; form-agent `GET /submissions/{slug}`, scoped by
tenant *and* user, reading each object because the owner lives inside it, hence the scan cap). A `conversational_rag` may add
`ui_extras: source_library` (`SourceLibraryView.tsx`; rag-agent `GET /documents/{slug}` and `/passages`, reading the tenant's
Qdrant collection, since rag-agent has no object store; etl-vectorize stores each chunk's `chunk` position, so a collection
indexed earlier shows passages unordered until re-indexed).

## Free-text features, challenger and rubric

A tabular feature can be `type: text` (free text): TF-IDF step `text_<col>` inside the pipeline,
SHAP terms rolled up server-side (`tabular_ml.original_feature`), per-word spans via `/predict`
`explain_text`; `explain: false` is the fast bulk path (TreeExplainer SHAP ≈5 ms/row). Such a
scenario may add a `model.text_challenger` (sentence embeddings from llm-gateway's `local-embed`,
cached from the host GPU — the cluster's CPU embedder is ~0.6 s/text; served as `/predict`
`model: "challenger"`, never auto-promoted) and a `rubric_check` (assistant `POST /rubric-check`: the
active LLM reads a *description of behaviour* against a YAML rubric — never judges named people).

## Networks, out-of-fold scores, money trail

A tabular scenario may ship a network next to its rows (`dataset.graph`, contract
`ai_circus_shared.network_graph.NetworkGraph`: `row`/`entity`/`context` nodes, typed edges with
per-period `series`): etl-tabular validates it and restricts it to the cleaned rows
(`processed/graph.json`), prediction serves it at `GET /graph/{slug}` (same entitlement,
fallback-org and TTL cache as the dataset sample) and the `network_explorer` tab draws it
(canvas + d3-force, `networkScene.ts`; `NetworkTab.tsx` adds a person-centred *Investigate* workspace —
ego stage, routes to targets, live-scored hypothetical individuals via `ui_extras.tie_features`). Graph position reaches the model only as ordinary
numeric features computed offline. `model.out_of_fold_scores` (classification only)
makes training cross-fit every row's probability + SHAP with the selected model — served at
`GET /model/{slug}/out-of-fold` (per-row SHAP up to 5,000 rows, probabilities only above) — because
the deployed model is refit on every row and its own scores of them are optimistic; `network_explorer`,
`shipment_globe` and `money_trail` require it (they reveal real outcomes). `money_trail`
(`aml_money_trail`, IBM AMLworld) reads a graph whose entities are banks, holders are
rows/context and flow edges carry an *hourly* `series` (`YYYY-MM-DDTHH` periods): a world map
replaying the payments hour by hour, with a per-typology reveal — plus a second, *Network graph* view
that feeds the same network to `network_explorer`'s renderer (`networkExplorerExtras()`).

## Business rules, decision policy, case desk

A tabular scenario whose rows are *applications* (`prestaciones_sociales`) may gate each one through
**deterministic business rules before the model** (`dataset.business_rules`, `rule_columns`,
`ai_circus_shared.business_rules`: families of rules `missing/equals/in/lt/gt/contains_any…` with
outcomes `reject`/`request_info` (blocking) or `review`) and turn the binary model's probability into
a proposal with `model.decision_policy` (approve ≥ `approve_at`, deny ≤ `deny_at`, else manual review):
etl-tabular evaluates the rules before `dropna()` and keeps blocked rows for analytics, training never
fits on them (`rules_excluded_rows`), prediction's `/predict` returns `rules`/`gate`/`decision` (the
rules are server-side only — the thresholds are mirrored client-side because they are sliders).
`text_language: es` swaps TF-IDF's stop words (negations kept) and the challenger embeds every text
column. `ui_extras: case_desk` («Mesa de valoración», `CaseDeskView.tsx`): the rules → model → trays
circuit, an official paper sheet with per-box SHAP heat, and scanned-document intake — the browser OCRs
the file (platform-registry `/documents/extract`), assistant's `POST /extract-record/{slug}` maps the
text onto the boxes with strictly validated, quote-verified JSON, and `GET /intake-samples/{slug}/{file}`
serves the listed sample scans. Audit-only columns (`audit_columns`, e.g. sex, nationality) are kept out
of the model and only feed the equity panel.

## A document search as an assistant tool

A `tabular_ml` scenario may also give its assistant a **document search as one more tool**
(`documents` + `documents.tool` {name, label, description, sample_questions} + `vector_store`, e.g.
`prestaciones_sociales`' `consultar_normativa` over real BOE/EUR-Lex articles + fictional local rules,
fetched by `scripts/prepare_prestaciones_normativa.py`): etl-vectorize indexes it like a RAG scenario,
assistant builds the tool per request (`ai_circus_shared.retrieval` — the one `retrieve`/`format_retrieved`
rag-agent and form-agent share), serves `GET /documents/{slug}`, and honours AG-UI `forwardedProps.scope:
"documents"` by building **that tool alone**. ChatPanel labels every reply with the sources its tool
calls really used (`chatProvenance.ts`); case_desk adds a reading room (`NormativaDesk.tsx`, «📖 Ver norma»
on a fired rule's `legal_basis` — a libs/shared test checks each one resolves to a shipped document).

## The MCP door (docs/adr/0019)

A `conversational_rag` scenario may open a **read-only MCP door** on its documents (`mcp: {expose: [list_sources,
search_documents, read_document]}`, needs `vector_store`; first on `ai_circus_reference`). rag-agent mounts one stateless
FastMCP app (Streamable HTTP, JSON responses) at `/mcp`; `McpDoor` (`core/mcp_door.py`) takes `/mcp/{slug}`, answers 404
for a scenario without a door and 401/403 from `resolve_caller_identity` *before* any MCP handling, then puts the caller
in the request scope's state: the tools read the tenant from there, never from an argument. Middleware lists and runs
only the exposed tools. The tools are the chat's and the reading room's own reads (`retrieval.retrieve`, `library.list_sources`/
`passages_of`), `top_k` capped at `vector_store.top_k`, and call no model; results say passage text is data. Logs carry
the tool name and outcome only; FastMCP's spans carry names and ids, and arguments are already in
`DROPPED_ATTRIBUTE_PREFIXES`. RagView adds a "Connect via MCP" tab (`McpConnectView.tsx`: endpoint, client snippets, a
live `tools/list` with the session token). The chat itself has `list_sources`/`read_document` beside `retrieve_docs`
(`build_document_tools`), and a scenario with `mcp.consume` gets a "📚 Direct · 🔌 Via MCP" switch: `forwardedProps.route:
"mcp"` makes `agui_endpoint` load the tools from those doors instead (`core/mcp_consumer.py`: only doors this instance
serves, over loopback, with the caller's token, `mcp__<server>__<tool>`, `MAX_MCP_AGENT_STEPS`), and `chatProvenance.ts`
labels the reply "🔌 MCP". MCP clients on the local cluster use `make k3s-mcp-portforward` (`localhost:8031`, the
card's snippets): `*.localhost` does not resolve for every client. Nothing works before `make k3s-vectorize`.

A `data_product` opens the same kind of door on **data-api** (`mcp.expose` from `MCP_DATA_TOOLS`): `core/mcp_door.py`'s
`McpDoor` settles product, caller and `identity.access_for` (the REST dependency's own code: role, row filter, masks,
X-Data-View-As for platform keys) before MCP runs, and each tool is a REST route's core call (`metrics.query_metric`,
`records.*`, `governed_sql.run`; `get_record` publishes the same "served" event). `run_sql` is listed only when the role
may run SQL. DataProductView's "Connect via MCP" tab reuses `McpConnectView` with data-api's URL, `localhost:8032` and the
View-as headers. OAuth for external clients,
`data-api`, models as tools and tool policy are later steps of the ADR.

**One address for every door** (docs/adr/0020): llm-gateway's LiteLLM is also the MCP gateway. `litellm_config.yaml`'s
`mcp_servers` lists each door under its scenario slug, all `auth_type: true_passthrough`, so `/mcp/{slug},{slug}` reaches
several doors with the caller's own token, the tools come back prefixed (`surf_retail-query_metric`), and each door still
decides tenant, role and tools; `extra_headers` forwards data-api's View-as pair. With several doors in the URL, LiteLLM
withholds a request-wide `Authorization` (one token is never replayed to several upstreams) and the client sends one
`x-mcp-{slug}-authorization` per door; a door that refuses just contributes no tools. `/mcp` without a server falls back
to LiteLLM's own key check. `tests/test_mcp_gateway.py` pins the contract (passthrough only, no credential, in-cluster
doors of scenarios with `mcp.expose`, LiteLLM's own gating and no fan-out). The gateway has no Traefik router; `make
k3s-mcp-portforward` adds `localhost:8033`.

**An optional chat UI with real sign-in** (docs/adr/0021): `make k3s-chat-ui-up` serves Open WebUI at `chat.localhost`
(`k8s/chat-ui/`, compose profile `chat-ui`), Keycloak-only sign-in through the confidential `open-webui` client, models
and embeddings through llm-gateway, and one MCP connection per door to the gateway with `auth_type: system_oauth`, so
each door sees the signed-in user's own token and applies its tenant and roles (an analyst gets `run_sql`, the S012 store
manager does not and sees only S012). `scripts/chat_ui_identity.py` brings the live realm up to `realm-export.json`
(which now issues `sub`, realm roles, the `{alias: {id}}` organization claim and row-filter claims such as `stores`) and
creates the `lite-demo` tenant. The client's tokens live 15 minutes because Open WebUI refreshes within 5 and the gateway
binds an MCP session to its token.

**A second chat UI, LibreChat** (docs/adr/0026, MIT): `make k3s-librechat-up` serves it at `librechat.localhost`
(`k8s/chat-ui-librechat/` with its own MongoDB, compose profile `chat-ui-librechat`; one chat UI at a time on 16 GB).
Keycloak-only sign-in through the confidential `librechat` client with `OPENID_REUSE_TOKENS`, so each MCP server's
`Authorization: Bearer {{LIBRECHAT_OPENID_ACCESS_TOKEN}}` is the user's own Keycloak token; models from llm-gateway
(`gemini-flash` by default). Its `librechat-config.yaml` holds the catalog-generated `mcpServers` and the gateway as the
only exemption from its private-address block (`mcpSettings.allowedAddresses`). Lab only: LibreChat's OpenID client
accepts only https providers, so `oidc-allow-http.mjs` (a Node load hook, `NODE_OPTIONS=--import`) turns on the library's
`allowInsecureRequests`; delete it with an https identity provider. LibreChat keeps an MCP connection across a token
refresh, so the first call after one gets the gateway's 403 owner mismatch, the connection reopens and the model retries;
idle connections close after 5 minutes to keep that rare.

**The MCP lite profile** (docs/adr/0027): `make k3s-mcp-lite` runs the MCP Lite Stack alone on its own k3d cluster
(`mcp-lite`, own kubeconfig file, port 80, so the framework cluster is paused meanwhile): `k8s/mcp-lite/` lists base's
Postgres, Keycloak, Valkey and llm-gateway (Langfuse off) plus `k8s/mcp-examples`, and `k8s/mcp-lite/open-webui|librechat`
add one chat UI with the kit servers only (generated from the catalog; scenario doors need the full framework). Built
with `kubectl kustomize --load-restrictor LoadRestrictionsNone`, since it lists the framework's own files. `make
k3s-mcp-lite-verify` (`scripts/mcp_lite_check.py`) signs the demo users in through Keycloak's login pages with the chat
UI's client and checks each kit server's tools, a granted call and a refused one, against the catalog, without a model.
`OBS=1` adds the observability bundle on the lite cluster (never run `make k3s-obs-up` bare while it is up: it acts on
the current context); the check then also finds the calls' metrics, audit lines and traces, and no token in any log.
LibreChat traces through its own OpenTelemetry switch (`OBS_LIBRECHAT_ENV`), wired by `k3s-obs-up` in both profiles.

**An MCP server in front of an existing API** (docs/adr/0022) is built with `libs/mcp-kit` (`mcp_kit`): it validates the
user's token (T1, audience = the server's Keycloak client), applies `policy.yaml` (tools per realm role, optionally per
tenant: `tools/list` filtered, other calls refused, 403 when nothing is granted), exchanges T1 at Keycloak for a token whose
only audience is its API (T2, RFC 8693, cached per T1), and gives tools `kit.context()`: the identity, read-only, and an API
client that already holds T2. Tools take no identity arguments; every call writes one JSON audit line (call
`configure_audit_output()` at start-up). In Keycloak each server is a confidential client with standard token exchange and
one audience scope, and the `mcp-servers` scope puts it in the user token's audience. The example pair, `crm-mcp` in front of
`crm-api` (`k8s/mcp-examples/`, `make k3s-mcp-examples-up`), is the gateway's `crm` door and a chat UI connection. The kit reports for every server (docs/adr/0023):
`mcp_kit_*` metrics on the service's own `/metrics` (labels: server, tool, outcome, reason only), audit lines in the
platform's log shape with `trace_id`, and one trace from the server inwards (its Keycloak exchange and API call); the
"AI Circus · MCP servers" dashboard and the `ai-circus.mcp` alert group read them. LiteLLM injects no trace context into
MCP calls, so every gateway route forwards the caller's `traceparent`/`tracestate` (never `baggage`, docs/adr/0028): a
chat UI that sends them (LibreChat) gets one trace through the server to its API; Open WebUI's MCP client sends none, so
its hop is correlated through the audit lines. The collector masks tokens (`<token>`) and email addresses (`<email>`) in
every stored log line, whoever wrote it (a chat UI logs the signed-in user's email).

**One catalog for the whole path** (docs/adr/0024): `mcp/catalog.yaml` lists the gateway's address, every server
(`scenario_door`: a scenario's own door, whose tools its entitlements and `scenario.yaml` roles decide, so no group lists
it; `kit_server`: service, API, tenants, tools) and every group (a realm role → tools per kit server, or `"*"`).
`scripts/generate_mcp_catalog.py` writes the gateway's `mcp_servers`, both chat UIs' connections (Open WebUI's in k8s and
compose, LibreChat's `mcpServers` in `k8s/chat-ui-librechat/librechat-config.yaml`, and the lite profile's kit-only
variants in `k8s/mcp-lite/`), each kit
server's `policy.yaml` and the Keycloak entries (exchange client, API client, `<api>-audience` scope, `mcp-servers` mapper,
all marked "Generated from mcp/catalog.yaml."); hand-written files keep their comments around `BEGIN`/`END` markers. It
refuses a catalog that would route, grant or exchange by mistake (names with `-`, undeclared doors, credential headers,
tools other than the server's `@kit.tool()` functions, grants on doors, groups that are not realm roles, `$` or `{{` in a text).
`make mcp-catalog` writes, `make mcp-catalog-check` and `libs/shared/tests/test_mcp_catalog.py` fail on drift, `make
k3s-mcp-catalog-apply` syncs Keycloak and rebuilds llm-gateway and the kit servers. A kit server's secret is
`<CLIENT_ID>_CLIENT_SECRET` in `.env` (`chat_ui_identity.py` derives it). `make mcp-catalog-portal` serves a local page
(127.0.0.1, per-run token, nonce CSP) to tick tools per group, add a server, review the diffs and publish (= the generator;
never deploys or commits). Chat UI visibility per group is not generated: Open WebUI grants by internal group ids, and
LibreChat shows every server to every user.

**Level 3, for regulated deployments** (docs/adr/0025): `level: 3` on a kit server of the catalog. The per-API token
is DPoP-bound to a key the MCP server holds in memory only (`mcp_kit.DpopKey`): proof on the exchange, a fresh proof on
every API call; the API checks method, URL, freshness, the token hash, a never-seen `jti` and the bound key, so a copied
token is useless. The user's token stays a bearer token (Keycloak refuses to exchange a sender-constrained token for
another client) and is introspected on every request; the API introspects its own token too, so signing a user out
stops both at once, and an unreachable Keycloak is a refusal (503), never a pass. The per-API token is lightweight
(ids only) and the API reads roles and the store claim from introspection. One CA per server/API pair
(`scripts/mcp_tls_certs.py`, `make k3s-mcp-tls`, `infra/mcp-tls/` gitignored) gives mutual TLS on the API port; a
plain port serves only `/healthz` and `/metrics`. The catalog writes the Keycloak flags explicitly both ways (Keycloak
merges attribute updates). Not covered: gateway → MCP server mutual TLS (LiteLLM's open-source MCP client cannot present
a client certificate) and a DPoP-bound user token. The `crm` example runs at level 3.

## Knowledge graphs

A `conversational_rag` scenario may ship a **knowledge graph** (`documents.knowledge_graph`, same
`NetworkGraph` contract: typed concept nodes, relations with `citation` + verbatim `evidence`, one node
per source document linked by `cited_in`): extracted *once, offline* by an LLM and committed
(`scripts/prepare_aml_regulation_kg.py` — quotes not found in the text are dropped; never extract in
the pipeline), bootstrapped by etl-vectorize into the tenant's bucket (`KNOWLEDGE_GRAPH_KEY`, re-synced when the committed
seed's SHA-256 changes; a tenant's own graph is never overwritten) and
indexed node-by-node and relation-by-relation in `kg_collection_name`. rag-agent then swaps
`retrieve_docs` for `graph_search` (HippoRAG 2-style: the question *plus 2-4 `key_concepts` the agent
names in the same call* → node/relation seeds weighted by specificity → Personalized PageRank within
`hops` over relations, `cited_in` and etl-vectorize's inferred `similar_to` synonymy edges → lines
ranked by degree-normalised mass, capped at `max_triples` + `max_passages` chunks and at
`MAX_GRAPH_SEARCHES_PER_RUN` — keep the caps, they are the per-question token bill) and `graph_path`, emits a `knowledge_graph_trace` AG-UI custom event
before `RUN_FINISHED`, and serves `GET /knowledge-graph/{slug}`; `ui_extras: knowledge_graph` splits
RagView into chat | `KnowledgeGraphView` (colour = role, glyph = class) lighting up each answer's subgraph.

## Data Platform

**Data Platform (`kind: data_product`, docs/data-platform/ + docs/adr/).** A data product is a
`data_product` block (schema `ai_circus_shared.data_product`) plus files next to its
scenario.yaml: JSON Schema `contracts/`, a `simulator/` plugin (`ai_circus_shared.data_simulator`:
produces only through Kafka / the landing bucket / the `data_sources` ERP database), a `dbt/`
project and `bi/dashboards.yaml`. Generic services: `data-ingest` (Kafka topics per tenant
`tenant-<org>.<topic>` → contract → bronze JSONB by key in database `data_platform` schema
`<slug>`, quarantine, file reconciliation, CDC from `data_sources`, hot-path minute aggregates,
Iceberg lake), `data-transform` (dbt → `<slug>_silver`/`<slug>_gold` on Postgres, then ClickHouse
copies silver and rebuilds gold; CronJob in core, Dagster in analytics; `bi_sync` for Superset),
`data-api` (the governed door: metrics, records + trail, sqlglot-rewritten SQL, lineage, SSE
pipeline events, simulator; access = `ai_circus_shared.data_policy`: org first, then the role's
row filter/masks; admin "view as" via `X-Data-View-As`), and `assistant`'s data tools
(`core/data_tools.py`). Four switchable tiers under `k8s/data-platform/{core,analytics,bi,governance}`
(`make k3s-data-*-up/down`, `k3s-data-showcase`), never part of `k3s-all`; first data product:
`surf_retail` — Angel's Surf Co., fictional (ui-react `DataProductView` + `Data*View.tsx`). A data product may
declare `apps` (`storefront`: a fictional web shop on its catalogue/recommendation datasets whose checkout is a hidden
simulator action, `StorefrontView.tsx` + `BoardArt.tsx`; `insights`: season heatmap + live forecast board,
`InsightsView.tsx`) and `ml_exports` (a gold feature mart → a `tabular_ml` scenario's raw CSV, written by
data-transform after each Postgres build; `make k3s-data-ml` trains them; the assistant gets `ml_predict`).

## Observability

**Observability (admin-only)**, all reachable from ui-react's admin **Platform** view: the health
dashboard (`data-platform-manager` `GET /platform/status`, `core/platform_status.py` — a
hand-synced list of every component; add a row for any new container, `tests/test_platform_status.py`
pins the names; Start/Stop only for `core/workloads.py`'s `OPTIONAL_SERVICES`, whose workloads
must equal the Role's `resourceNames` — `tests/test_workloads.py`); the resource monitor
(`GET /platform/resources`, `core/resources.py`: metrics-server + NVML via `k8s/gpu/`'s patch); **Langfuse v4** for GenAI (fed only by llm-gateway's `langfuse_otel` callback —
never add a Langfuse SDK to an agent; pass request `metadata` via `langfuse_request_metadata`);
**MLflow** for ML (`training`'s `core/mlflow_tracking.py`, must never fail the job). The
metrics/logs/traces *stack* is a platform prerequisite, never shipped in `k8s/base` (ADR 0015); each
service honours the contract — `/metrics`, `telemetry.configure_tracing(app, service)` right after
`configure_metrics` (OTLP only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; it imports nothing otherwise),
and `LOG_FORMAT=json` logs with `trace_id` through `core/logger.py`. Exported spans pass
`RedactingSpanExporter` — a new content-carrying attribute goes into `DROPPED_ATTRIBUTE_PREFIXES`, and
log messages never carry prompts or personal data. It must stay the only exporter: never add an
OpenTelemetry distro/auto-instrumentation, and keep `configure_tracing(app, …)` on every FastAPI app —
it also switches off FastAPI's own `OTEL_*` auto-export, which otherwise ships an unredacted copy.
The AI-specific pieces live in `observability/` (collector config, dashboards — generated by
`scripts/generate_observability_dashboards.py` —, alert rules, the optional bundle): llm-gateway's GenAI
metrics, `prediction`'s drift PSI (`core/drift_monitor.py` against the `drift_reference` training writes,
`ai_circus_shared.drift`; windows are per tenant — `GET /model/{slug}/drift` and ML Insights' *Data drift* card
show the caller's own traffic, /metrics sums tenants without a tenant label), DCGM-named GPU gauges
(`data-platform-manager` `core/gpu_metrics.py`). The admin Platform page lists the bundle's `grafana` and
`otel-collector` as optional rows. A new
FastAPI deployment gets the `prometheus.io/*` pod annotations; metric labels never carry tenants or values.

## Governance

**Governance-ready by standards (`docs/governance/`, ADR 0011).** The framework is governable by an
external tool through open contracts only — never a product name in code, config or docs; it *supports* an
AI management system, it is not one ("supports"/"provides evidence for", never "compliant"). **Everything
is off by default** and changes nothing when off: declared `governance:` metadata + stable `ai_system_id`
per scenario (`ai_circus_shared.governance`, never auto-classified); content-free CloudEvents
(`governance_events`, log or HMAC webhook via a Valkey outbox, `prediction.recorded` decision records for
non-LLM models with enforced retention in `decision_records`); a hash-chained admin audit trail
(platform-registry is the single writer; data-platform-manager reports budget/policy changes to it);
llm-gateway's pluggable **policy decision point** (`policy_hook.py`: noop/static/OPA/HTTP, explicit
`POLICY_FAIL_MODE`, HMAC `POST /governance/policy/invalidate` on data-platform-manager); `gen_ai.*` +
`aicf.*` span attributes (`llm_gateway/tracing.py`, semconv pinned to v1.37.0); a CycloneDX 1.6 AI-BOM at
`GET /governance/inventory`. **All model calls go through llm-gateway** (a libs/shared test enforces it;
embeddings included). Rules that bite: `llm-gateway` cannot import `ai_circus_shared`, so shared contracts
are JSON Schemas under `docs/governance/schemas/` (regenerate with `scripts/export_governance_schemas.py`;
tests fail on drift); a `GOVERNANCE_*`/`POLICY_*` flag must **never** get a profile default in a
`settings.yaml` (config is `EnvConfig(**profile)`, so init values beat the environment); governance
events/audit/records carry ids, metrics and decisions — never prompts or personal data. Optional OPA:
`make k3s-governance-opa-up|down|test`.

## Maps and dashboard charts

`GeoBubbleMap.tsx` draws the region and watchlist maps in SVG: marks are grouped into one path per style, a pure
nearest-point search does the hit-testing, and pan and zoom only move a transform. `geoMapFrame.ts` holds the framing,
shared with `DataStoreMap.tsx`. `dashboardCharts.tsx` draws a data product's line and bar panels at the real pixel width,
and `dashboardChartLogic.ts` is their pure part (scales, series, and leaving out a day, week or month still in progress).
A panel declared `chart: pie` or `hbar` is a `BarList`. Measurements are in ADR 0014.
