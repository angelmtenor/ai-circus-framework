# Changelog

Notable changes per release, newest first. Versions follow SemVer and every release is a git-flow
tag (`git tag`); commits follow [Conventional Commits](styleguide.md). Releases before v0.31.0 are
only recorded as tags and in `git log`.

## v0.47.0 — 2026-11-05

### Added
- **`make k3s-vectorize`**: runs the `etl-vectorize` Job, which a fresh cluster needs for RAG, forms and the case desk's reading
  room. About 20 min on CPU, so run it detached.
- **`make dl-artifacts-export` / `dl-artifacts-import`**: move trained deep-learning models between stacks as one tar file, so one
  machine can train on a GPU and another can serve on CPU (`scripts/dl_artifacts.py`). `raw/` stays behind, and the manifest goes last.

### Changed
- **`symptom_triage` is served unquantized**: 600 MB fp32 ONNX, 96.2% on test with the CPU budget. `dl-inference`'s memory limit goes
  from 1536Mi to 3Gi in both deploy paths (measured peak 2.4 GiB).

### Fixed
- **int8 models could fail depending on the CPU**: on an AVX2 CPU without VNNI, the quantized `symptom_triage` scored 4% on test
  against 95% for the same model in torch and fp32 ONNX, consistent with onnxruntime's documented saturation on such CPUs. The export
  now uses `reduce_range` (91.5% on the same model).
- **The case desk's reading room was blank when no documents were indexed**: it now says so and names the command to run.

## v0.46.0 — 2026-11-05

### Added
- **Several models in one scenario** (`extra_models:`, ADR 0018): each model is a validated view of the scenario with its own
  dataset and bucket, addressed as `slug~id` (the bare slug is the main model, so every existing URL is unchanged). Entitlement is
  to the scenario. The assistant gets every model's tools; an extra model's end in `_<id>`.
- **Recommenders** (`model.task_type: recommendation`): association rules and trained item factors, served side by side by
  `GET /recommend/{slug}`, scored on items hidden from held-out baskets next to a popularity baseline. First user `surf_ml~recommender`,
  from a committed sample of anonymous baskets (`scripts/prepare_surf_ml_samples.py`), so it runs without the data tier.

### Changed
- **`energy_building` has its regression back** next to its 24-hour forecast, which replaced it in v0.40.0.
- **`surf_demand_forecast` and `surf_customer_propensity` are now one scenario, `surf_ml`**; `luznova_regional_forecast` is now a model of
  `luznova_regional_demand`. The seed moves a tenant's entitlements to the successor (`RETIRED_SCENARIOS`).
- **ML Predictions and ML Insights are one tab per model**, with a Predictions / Insights switch, in every tabular scenario.
- `make k3s-idle-check` also visits a model tab's Insights, fails a tab on a backend 5xx or 404, and saves screenshots with `SHOT_DIR`.

## v0.45.0 — 2026-11-04

### Added
- **"Model not trained" on scenario cards**: a tabular scenario whose model does not exist yet for the caller's tenant (every one
  on a fresh cluster) is grayed out with "run `make k3s-pipeline`" instead of failing on open. One org-scoped, entitlement-checked
  request, `GET /models/status` on `prediction`, reads only whether the artifacts exist.
- **`make k3s-idle-check`**: opens every tab of the scenarios in `IDLE_SCENARIOS` in headless Chromium and fails if one still draws
  more than 2 frames/s or uses more than 15% CPU while nobody touches it.
- **`make k3s-embed-onnx`** (optional, off by default): runs `local-embed` on ONNX Runtime instead of torch. Measured on the
  cluster after one request: llm-gateway 975 MB against 1,300 MB (-25%), 0.18 s against 0.67 s for five texts. The int8 export's
  vectors differ from torch's (cosine 0.984 to 0.993), so documents already in Qdrant should be re-vectorized after switching;
  `EMBED_VARIANT=off` goes back. The fp32 export is exact but needs 1.4 GB, more than the pod's limit. torch stays in the image
  as the fallback.

### Fixed
- **Data & BI held a CPU core while idle** (found by the new idle check): `DataView` passed fresh arrays to the filter panel on
  every render, which re-ran its effect and re-rendered the view without end (about 290,000 DOM changes in 8 s, 100% CPU, measured
  on churn; the view is shared by every tabular scenario). Idle is now 0%. The same pattern in ML Predictions' batch mode is fixed too.

### Known
- `local-embed` has always served a plain 1024-d Qwen3 backbone (causal attention, no linear head), because the gateway loads
  `voyageai/voyage-4-nano` without `trust_remote_code`; the model card's 2048-d bidirectional model is not what is deployed.
  The ONNX export reproduces what is deployed, so existing vectors stay comparable. Switching to the real model would mean
  re-vectorizing everything and retraining the text challengers; it was not done.

## v0.44.0 — 2026-11-04

### Changed
- **The region and watchlist maps are plain SVG** (`GeoBubbleMap`, `d3-geo`, no frame loop): opening them takes about 85% less
  browser CPU, the 8,196-bank map is 20 DOM nodes instead of 8,299, and they no longer fetch map data from `cdn.plot.ly`.
  Idle was already near zero. The regional map uses a one-hue colour scale, and pan and zoom have a reset button.
  The watchlist's Risk landscape keeps Plotly's WebGL scatter.
- **The data product dashboards no longer load Plotly** (`dashboardCharts.tsx`, `BarList`): opening the tab takes about half the
  browser CPU and a fifth of the download. A panel declared `chart: pie` is drawn as ranked bars with each share beside them.
- ADR 0014 records both sets of measurements. Plotly now serves only the chart builder, the chat's charts, large scatter
  plots and 3-D.

### Fixed
- A dashboard's day, week or month still in progress is left out, with a note, instead of drawn as a sudden collapse.
- The regional map no longer overflows the page on a phone. 39 banks the US map cannot place are now stated under it, and stay
  in the ranked list.

## v0.43.0 — 2026-11-04

### Added
- **vLLM as an optional GPU backend beside Ollama** (ADR 0017): `make k3s-vllm-up` or the `vllm` compose profile, reached
  only through llm-gateway as the `vllm-qwen` model, listed in Settings and the health page, off by default. Its first start
  needs about 33 GB of disk for the image and 3.1 GB for the weights, and it holds the whole GPU while it runs.

### Changed
- ADR 0014 is decided: Plotly stays for the chart builder, large scatter plots and 3-D, with measurements against Vega-Lite.
  New small charts use the SVG primitives and new maps use `d3-geo`.

## v0.42.0 — 2026-11-04

### Added
- **`luznova_regional_forecast`**: the first forecast with many series, 50 Spanish provinces 30 days ahead, from the same
  synthetic data as `luznova_regional_demand`, which stays a regression because its map scores any area under any
  conditions. The backtest selects exponential smoothing, so its temperature lever is off and says why.
- **`row_triage` tab on `steel_defects`**: plates routed to an inspector or automatically by a threshold you move, with the
  real workload and misses. It uses out-of-fold scores, so a tick calls no model.
- **`case_tracker` tab on `service_request` and `sede_electronica`**: a user's own filed cases, with receipt, values and
  filed PDF, through form-agent's new `GET /submissions/{slug}`, scoped by tenant and by user.
- **`source_library` tab on `ai_circus_reference`**: the documents the assistant really searches, through rag-agent's new
  `GET /documents/{slug}` and `/passages`, read from the tenant's own collection.

### Changed
- etl-vectorize stores each chunk's position. Re-run it (`etl-vectorize` Job) so Sources shows passages in order.
- `steel_defects` trains out-of-fold scores: retrain it (`./scripts/k3s_ml_train.sh steel_defects`).

### Fixed
- A forecast scenario can no longer take `region_map`, which the schema wrongly allowed: the map scores through `/predict`,
  which answers 409 for a forecast.
- A numeric static feature of a forecast could arrive as a numpy number, be treated as a category and fail the artifact.

## v0.41.0 — 2026-11-04

### Added
- **`fleet_health` tab**: a fleet of units replaying real dataset rows, scored live by the deployed model. Health is the
  model's score, or actual minus predicted when the target is not itself a health measure. DOM and CSS only, with no
  frame loop. On `turbofan_rul`, `electric_motor`, `luznova_gas_anomaly` and `luznova_ev_charging`.
- **Watchlists on `churn` and `surf_customer_propensity`**, showing customers by id only. `risk_watchlist` gains an
  optional `name_column`, `positive_is_adverse` (a blue ramp when the outcome is good news) and its own wording.

### Changed
- `turbofan_rul` shows `unit_number`, which stays out of the features: rerun its ETL
  (`./scripts/k3s_ml_train.sh turbofan_rul`).

### Fixed
- Pages no longer scroll sideways at phone width: the top bar wraps, the tab strip scrolls inside itself, and Data & BI,
  data tables, sub-tabs and the watchlist fit. Desktop is unchanged.
- Lint errors in the forecasting files v0.40.0 shipped with (`make check` failed for `prediction` and `training`). The
  pre-commit hook skips untracked files, so they went unchecked. No behaviour change.

## v0.40.0 — 2026-11-04

### Added
- **Time-series forecasting** as a third tabular task (`model.task_type: forecast` + `time_series:`, ADR 0016), with no
  new service. Training backtests seasonal naive, exponential smoothing and LightGBM on lags on past cut-offs and
  selects by MASE under the Green Code rule, with conformal prediction bands.
- `prediction` serves `GET /forecast/{slug}`, `POST /forecast/{slug}/what-if` and `GET /model/{slug}/backtest`.
- The assistant gets `get_forecast` and `get_forecast_backtest` on a forecast scenario.
- ui-react: forecast variants of ML Predictions and ML Insights, and the opt-in `forecast_board` what-if tab.
- The gallery's *Time Series* filter now finds real scenarios; it used to test for a scenario kind nothing produced.

### Changed
- **`energy_building` is now a forecast**: a home's appliance energy, hourly, 24 hours ahead. It is re-seeded from the
  10-minute sample under a new raw object key and needs retraining (`make k3s-pipeline`, or
  `./scripts/k3s_ml_train.sh energy_building`). Its `/predict`, dataset evaluation and explainability now answer 409.
- `training` depends on statsmodels. mlforecast and statsforecast were the first choice but pin `pandas<3`.

## v0.39.0 — 2026-11-03

### Added
- **Data drift in the app**: every tabular scenario's ML Insights tab opens with a *Data drift* card — Stable /
  Moderate / Significant shift, the output's PSI, the most shifted input and a bar per profiled input — over the
  tenant's own recent scoring traffic, or why there is none yet (a model trained before v0.38.0; too few scored
  rows). Served by `prediction`'s new `GET /model/{slug}/drift`.
- The admin Platform page lists the observability bundle's `grafana` (console link) and `otel-collector` as
  optional Observability rows.

### Security
- Drift windows are kept per tenant: tenants without a model of their own share the fallback one, and a shared
  window would have shown one tenant statistics of another's traffic. The `/metrics` gauges still sum every
  tenant of a model, with no tenant label.

### Fixed
- The Platform page no longer offers console links for tools that are stopped or not deployed.
- `prediction`'s decision-record test failed at random (its `f1` leak marker also occurs in the event's UUID).

## v0.38.0 — 2026-11-03

### Added
- The AI-specific observability pieces ([`observability/`](observability/README.md), ADR 0015): an OpenTelemetry
  collector config (OTLP in, pod JSON logs tailed, redaction again), Grafana dashboards — **LLM gateway** (calls,
  tokens, cost, latency, time to first token), **GPU**, **Model drift** — generated by
  `scripts/generate_observability_dashboards.py`, and Prometheus rule groups (slow/expensive/silent LLM calls, 5xx
  rate, service down, model drift, GPU heat and memory).
- An optional observability lab bundle for clusters with no platform stack: `make k3s-obs-up` / `k3s-obs-down`
  (collector, VictoriaMetrics + vmalert, Loki, Tempo, Grafana at http://grafana.localhost; off by default, ~700 MiB
  measured, 24 h retention). Services are restarted one at a time to keep a laptop's memory in check.
- Model drift: `training` writes a `drift_reference` (top 8 features by SHAP and the served score) into the model
  metadata; `prediction` exports the Population Stability Index of live traffic against it on /metrics. Models
  trained before this release report none until retrained (`make k3s-pipeline`).
- llm-gateway exports OpenTelemetry GenAI metrics (dimensions: operation, provider, model only).
- `data-platform-manager` publishes its NVML GPU reading under NVIDIA DCGM exporter names (`source="nvml"`) where
  DCGM cannot run (WSL2, consumer cards).
- FastAPI pods carry `prometheus.io/*` scrape annotations.

### Security
- llm-gateway's platform spans drop LiteLLM's request metadata (`metadata.*`, `hidden_params`: requester IP and
  headers, user e-mail, API-key hash) and carry the `gen_ai.*`/`aicf.*` attributes instead.

### Fixed
- llm-gateway tests share one event loop and reset LiteLLM's global callback registry, so tracing test modules no
  longer interfere with each other.

## v0.37.0 — 2026-11-03

### Added
- Observability as a platform contract ([ADR 0015](docs/adr/0015-observability-platform-contract.md)): the
  metrics/logs/traces stack (collector, Prometheus-compatible store, Loki, Tempo, Grafana) is a prerequisite the
  cluster provides, never shipped in `k8s/base`. Each service keeps `/metrics` and now:
  - exports **OTLP traces** when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (`ai_circus_shared.telemetry.configure_tracing`:
    FastAPI server and httpx client spans, `traceparent` carried to llm-gateway) — off by default, and the framework
    imports nothing OpenTelemetry while off;
  - writes **JSON logs** (`LOG_FORMAT=json`, set in k8s `shared-config`) with `trace_id`/`span_id`.
- llm-gateway joins the same traces through LiteLLM's `otel` callback, always without message content
  (`message_logging: false`); Langfuse keeps the prompts, linked by the same trace id.
- The layers diagram reads left to right, over a platform observability band.

### Security
- Exported spans are content-free: `RedactingSpanExporter` drops prompt, completion, message, SQL-text, body and
  `enduser.*` attributes and strips URL query strings before anything leaves a pod.
- FastAPI's own `OTEL_*` auto-export is switched off on every app — verified on k3s, it otherwise attached a second,
  unredacted exporter (each span went out twice, once with its query string) and a log exporter carrying exception
  messages and rejected input.

### Changed
- The OpenTelemetry SDK is a dependency of every service image (~10 MB). redis-py imports it whenever installed:
  ~4 MiB of RSS per Python process even with tracing off (measured on `prediction`).

## v0.36.0 — 2026-11-03

### Added
- Scenario gallery: a scenario whose backend is not running (scaled to 0 by `k3s-demo`/`k3s-all-lite`, or an
  overlay never deployed) is grayed out with "<service> is not started" and the command to start it, instead of
  failing with "Failed to fetch" once opened. The check is each service's `/healthz` through Traefik (one shared
  30 s poll, paused while the tab is hidden); admins get a Start button for `dl-inference`. A down assistant only
  adds a "Chat unavailable" chip.
- ADR 0014 (proposed): Vega-Lite as the app's one chart grammar (Plotly to be phased out), Superset kept for BI,
  Power BI reached through the gold marts and Deneb-ready specs.

### Changed
- Every animated canvas outside the network scenes (shipment globe, Dispatch Tower map and landscape, case-desk
  circuit) runs on one loop, `ui-react/src/sceneLoop.ts`: decorative motion rests 15 s after the last input (the
  globe's spin 30 s), nothing draws while the page or canvas is hidden, and expensive frames lower the frame rate.
  Idle animation frames drop from 60/s to 0 on these tabs; the case-desk circuit no longer redraws forever.

## v0.35.1 — 2026-11-03

### Fixed
- `RTVI_DEBUG` (the v0.35.0 opt-in for agui-voice's RTVI message logging) now reaches the pod: it is passed by
  `docker-compose.yml` and the k3s `agui-voice-secrets`, and documented in `.env.example`.

## v0.35.0 — 2026-11-03

### Security
- Governed SQL (`data-api`): a role's hidden columns are now absent from the tenant-filtered subquery itself, so
  no spelling (`EMAIL`, `to_jsonb(t)`, `t::text`, `SELECT *`) can reach them; the name check is case-insensitive.
- Auth: a token belonging to more than one Keycloak organization is rejected (the tenant is no longer "the first
  claim"); malformed organization claims and non-ASCII bearer values no longer cause a 500.
- Tenant ids and path segments are validated with `\Z` (a trailing newline no longer passes); Qdrant collection
  names are built only from a validated org id.
- Prompt injection: retrieved documents, catalog entries and knowledge-graph lines are wrapped by
  `wrap_untrusted`, which defangs any delimiter inside the text and keeps `source` inside its attribute.
- Optional model-artifact signing: with `MODEL_SIGNING_KEY` set (training + prediction only), `training` signs each
  model's checksums with HMAC-SHA256 and `prediction` refuses a model whose signature is missing or wrong. Off by
  default; set it before `make k3s-pipeline`.
- Public deployments (`DEPLOYMENT_TARGET=public`) also refuse the other shipped `change-me…` secrets; agui-voice's
  RTVI message logging is opt-in (`RTVI_DEBUG=true`); Keycloak brute-force protection in the realm export.
- Request bodies are bounded (conversation titles, dl-inference text/image/ids, registry model and voice names).
- `ObjectStore.exists()` only answers "absent" for a real not-found; a 403/5xx now raises instead of reading as
  missing, and concurrent bucket creation no longer fails the loser.

### Added
- `SECURITY.md` and `CONTRIBUTING.md`; Dependabot covers data-api, data-ingest, data-transform, GitHub Actions and
  base images; CI lints the UI and reports `npm audit` (informational).

### Fixed
- Bumped `pyasn1` and `pypdf` to clear `uv audit` CVEs.
- The entitlement TTL cache evicts the least recently written entry, not a freshly refreshed one.

## v0.34.3 — 2026-10-30

### Added
- README hero: a 2:54 narrated Tron-style pitch video (`docs/media/ai-open-framework-tron-pitch.mp4`, 720p, with an
  animated WebP preview) recorded on the real app — the Angel's Surf Co. data platform, the delivery globe, the
  money-laundering map, pneumonia and wildfire-smoke detection, an assisted tax form, the social-aid desk, and the
  platform's health, gateway and audit trail.

## v0.34.2 — 2026-10-30

### Fixed
- CI: every `service-check` job failed since the runner's `uv` started installing Python 3.15, for which
  `confluent-kafka` has no wheel. The workflow now pins `UV_PYTHON: "3.14"`, the version the services and their
  Dockerfiles target.

## v0.34.1 — 2026-10-30

### Changed
- README: a three-layer architecture overview (`docs/screenshots/architecture-layers.svg`, generated by
  `scripts/generate_layers_diagram.py`) — consumption, data platform, AI gateway & governance — a full Angel's Surf Co.
  section in the platform tour, and the Governance tab in the Platform tour.

## v0.34.0 — 2026-10-30

### Added
- **Platform → Governance** tab in the admin Platform page (`PlatformGovernance.tsx`) backed by
  `GET /governance/overview` on platform-registry: switches on/off, the AI systems with their declared EU AI Act risk
  and human oversight, the audit trail with a chain-verified badge, and an AI-BOM download. Read-only, content-free,
  and it answers while every governance feature is off.

### Changed
- New `k3s-data-demo` / `k3s-data-demo-off` targets: every scenario kind plus the core, analytics and bi data tiers,
  with governance, MLflow and voice off (about 8.8 of 11.7 GiB measured).
- The simulator ADR is renumbered 0013 (0011 stays the governance ADR).
- Docs: README governance section, tier table with demos and memory, `docs/scenarios/surf_retail.md`, a regenerated
  data-products diagram.

## v0.33.1 — 2026-10-30

### Fixed
- `prediction`: the decision-record metadata is typed as a mapping of any, so `make check` passes.
- `libs/shared`: three lint errors in `data_product` cleared so `make check-all` passes.

## v0.33.0 — 2026-10-30

### Added
- **AI-BOM** — the AI inventory exported as CycloneDX 1.6 at `GET /governance/inventory`
  (`GOVERNANCE_INVENTORY_ENABLED`, off by default).
- Governance documentation: [docs/governance](docs/governance/README.md) with the standards
  crosswalk, plus the roadmap status in the admin Platform → Capabilities tab.

## v0.32.0 — 2026-10-29

### Added — governance-ready by standards ([ADR 0011](docs/adr/0011-governance-ready-by-standards.md))
Everything is off by default and changes nothing when off.
- Declared AI-system metadata per scenario with a stable `ai_system_id` (never auto-classified).
- Content-free **CloudEvents** (log or HMAC-signed webhook with an outbox) and a **hash-chained
  admin audit trail** (`/governance/audit`, `/audit/verify`).
- A pluggable **policy decision point** enforced at `llm-gateway` (noop / static / OPA / HTTP, with
  an explicit `POLICY_FAIL_MODE` and an HMAC invalidation webhook).
- `gen_ai.*` + `aicf.*` trace attributes (semconv v1.37.0) and content-free **decision records**
  with enforced retention for non-LLM predictions.

## v0.31.0 — 2026-10-29

### Added — Data Platform and the `data_product` scenario kind ([docs/data-platform](docs/data-platform/README.md))
- A fifth scenario kind, `data_product`, and the generic services `data-ingest`, `data-transform`
  and `data-api` on four switchable k8s tiers (core, analytics, bi, governance; never part of
  `make k3s-all`).
- The `assistant` answers data products through governed data, catalogue and ML tools.
- **Angel's Surf Co.** (`surf_retail`, fictional): a web shop, the Surf HQ control room, a guided
  tour, and two ML scenarios trained on its gold marts (`surf_demand_forecast`,
  `surf_customer_propensity`). See [docs/scenarios/surf_retail.md](docs/scenarios/surf_retail.md).

### Fixed
- `etl-tabular` and `training` jobs honour `SCENARIOS`.
- Dependency advisories cleared (langgraph, filelock, dnspython).
