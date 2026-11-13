# CLAUDE.md

Guidance for Claude Code working in this repository.

## Mandatory reading

**Read [`AGENTS.md`](AGENTS.md) before any change** — security/tenancy rules, git-flow, the
human-in-the-loop / no-`git push` protocol, and verification requirements. It is STRICTLY
MANDATORY. Each `services/<name>/` and `ui-react/` is its own cookiecutter-generated project
with its own `AGENTS.md`/`SKILLS.md` layered on top — read those when you touch that directory
(they are template text: `make check` + a unit test for every bug fix).

Rules most likely to matter mid-task:
- Every code path reading scenario data, model artifacts or vector search results **must** be
  scoped by `org_id` (Keycloak Organization = tenant), and the entitlement check happens in the
  backend service, never only in the UI — see `libs/shared/src/ai_circus_shared/{storage,entitlements,auth}.py`.
- Never hand-write a new service's `pyproject.toml`/`Dockerfile`/`settings.yaml` — scaffold with
  `make new-service NAME=<name>` (the `new-service-scaffold` skill).
- Never read or print `.env`; use presence checks (`grep -q '^KEY=' .env`).

## Where it runs

**k3s (local k3d) is the default target** — verify every change there, not with docker compose
(the `k3s-deploy-verify` skill is the runbook; `service-check` is the Definition of Done).
`docker-compose.yml` is still maintained as an equivalent path and must stay in sync with
`k8s/` by hand (CI only validates it statically).

## Commands

```bash
# k3s — the day-to-day loop (see k8s/README.md)
make k3s-all                 # cluster -> build -> import -> secrets -> up -> wait (+ port-forward to platform-registry)
make k3s-all-lite            # same, without mlflow/agui-voice (~1.2 GB less RAM); `make k3s-full` restores
make k3s-pipeline            # REQUIRED on a fresh cluster: etl-tabular -> training Jobs, then restarts prediction
make k3s-vectorize           # REQUIRED on a fresh cluster for RAG/forms/the case desk's reading room: etl-vectorize Job (~20 min on CPU, run detached)
make k3s-verify              # curl checks (admin + engineering-demo tenants); then a real browser check
make k3s-idle-check IDLE_SCENARIOS="Title A,Title B"  # every tab of those scenarios, headless: fails if one still draws >2 fps or >15% CPU idle (a new tab must pass)
make k3s-embed-onnx          # OPTIONAL, off by default: local-embed on ONNX Runtime int8 (pod RSS -25%, ~4x faster, but vectors differ: cosine >= 0.98 vs torch, so re-vectorize); EMBED_VARIANT=off reverts
make k3s-pause / k3s-resume-lite
make k3s-demo / k3s-demo-off  # live demo on a 16 GB laptop: scale observability + admin Platform to 0 (~2.5 GB); verify with VERIFY_SKIP="langfuse mlflow"
make k3s-data-demo / k3s-data-demo-off  # multi-scenario demo incl. data core+analytics+bi; governance, MLflow, voice off (~8.8 of 11.7 GiB)
make k3s-mcp-portforward              # OPTIONAL: MCP doors on localhost for clients on this machine: rag-agent :8031, data-api :8032, the MCP gateway in front of both :8033 (ADR 0019, 0020)
make k3s-mcp-examples-up / -down       # OPTIONAL crm-mcp (libs/mcp-kit) in front of crm-api: per-API token exchange, gateway door /mcp/crm (ADR 0022)
make k3s-chat-ui-up / k3s-chat-ui-down # OPTIONAL chat UI: Open WebUI at chat.localhost, Keycloak sign-in (lite-demo users), MCP doors via the gateway with each user's token (ADR 0021)
make k3s-librechat-up / k3s-librechat-down # OPTIONAL second chat UI: LibreChat (MIT) + MongoDB at librechat.localhost, same sign-in and MCP path (ADR 0026); one chat UI at a time
make mcp-catalog / mcp-catalog-check   # mcp/catalog.yaml (ADR 0024) -> gateway routes, chat UI connections, kit policy.yaml, Keycloak exchange clients; CI fails on drift
make mcp-catalog-portal                # OPTIONAL local page over the catalog (127.0.0.1:8040): tick tools per group, review diffs, publish; then make k3s-mcp-catalog-apply
make k3s-mcp-tls                       # Level 3 (ADR 0025): mutual-TLS certificates per level-3 kit server/API pair (infra/mcp-tls/, gitignored) + per-workload Secrets; renew every 90 days
make k3s-obs-up / k3s-obs-down         # OPTIONAL observability lab bundle (~1 GiB): collector, VictoriaMetrics, Loki, Tempo, Grafana at grafana.localhost
# after a code change: rebuild + reimport + restart what changed (a same-tag import never restarts pods)
make k3s-build k3s-import && kubectl -n ai-circus rollout restart deployment/<svc>

# deep learning (optional, never part of k3s-all)
make k3s-all-dl | dl-train-nlp | dl-train-cv | dl-train-anomaly | k3s-dl-train SCENARIO=<slug>
make k3s-text-embeddings     # host-GPU sentence embeddings for a tabular text scenario's challenger (toxic_leadership), then retrain

# quality
make check-all               # libs/shared lint+tests, then `make check` in every service
make sync-shared             # after editing libs/shared: reinstall it into every service venv
make new-service NAME=foo    # scaffold a service from ai-circus-template

# docker compose (equivalent path)
make all | up | up-infra | pipeline | verify | logs | down | reset-all
```

Per service (`cd services/<name>/`): `make check` (pre-commit: ruff, pyrefly, gitleaks,
checkmake, settings.yaml/data_model.py drift, `uv audit`; then pytest with a coverage floor),
`make run`, `uv run pytest tests/test_x.py::test_y`. `ui-react/`: `npm run build` (tsc + vite —
its "check"), `npm run lint` (oxlint), `npm run dev`.

Run long steps (`k3s-build`, `k3s-pipeline`) detached — `nohup setsid bash -c '…' > ~/.cache/<log> 2>&1 &` —
and log under `~/.cache`: sessions end and WSL wipes `/tmp` on restart.

## Architecture

**How each feature works is in [`docs/architecture-details.md`](docs/architecture-details.md): read its section
before touching that feature.** Sections: scenarios and renderers, paper forms, forecasts, fleet health/row
triage/case tracker/source library, free-text features, networks and out-of-fold scores, business rules and case desk,
document-search tools, the MCP door, knowledge graphs, Data Platform, observability, governance, maps and dashboard charts.

Microservices behind **Traefik** (the only ingress; a few services also publish a
loopback-only port for non-container dev — never `traefik.enable=true` for platform-registry,
llm-gateway, qdrant or valkey). Every service is stateless and env-configured.

**Scenario-driven, not per-feature code.** A scenario (`scenarios/<slug>/scenario.yaml`,
schema in `ai_circus_shared/scenario_schema.py`) is the unit of product content, of one `kind`:
`tabular_ml`, `conversational_rag`, `assisted_form`, `deep_learning`, `data_product`. Adding one is a YAML file
plus a `platform-registry` restart (it seeds scenarios) — never new UI or service code. **One
service instance serves every scenario of its kind**, routed by a `{scenario_slug}` path segment:
`prediction`/`assistant` (tabular_ml), `rag-agent` (conversational_rag), `form-agent`
(assisted_form), optional `dl-inference` (deep_learning; onnxruntime only). `ui-react` mirrors this: generic
renderers driven by each scenario's `ScenarioSummary`, plus an optional extra tab from `ui_extras`.

**Rules for features, whatever you touch** (the details doc explains each):
- **A new tab must leave the page quiet**: prefer DOM/SVG over canvas, cap what it draws, and measure it idle.
- A challenger model is never auto-promoted. A `rubric_check` reads a *description of behaviour* and never judges named people.
- Graph position reaches a model only as ordinary numeric features computed offline. A tab that reveals real outcomes
  (`network_explorer`, `shipment_globe`, `money_trail`) requires `model.out_of_fold_scores`.
- Audit-only columns stay out of the model. Business rules run server-side only, and training never fits on rows they block.
- A knowledge graph is extracted once, offline, and committed: never in the pipeline, and a tenant's own graph is never
  overwritten. Keep `graph_search`'s caps (`max_triples`, `max_passages`, `MAX_GRAPH_SEARCHES_PER_RUN`), since they are the
  per-question token bill.
- The Data Platform's four tiers (`k8s/data-platform/`) are never part of `k3s-all`. Its simulators produce only through Kafka,
  the landing bucket or the `data_sources` database; `data-api` is the governed door (org first, then the role's filter and masks).

**Tenancy & entitlements.** `platform-registry` owns tenants/scenarios/entitlements in Postgres;
every other service calls its entitlement check (`ai_circus_shared.auth.resolve_caller_identity`,
30s in-process cache) before serving a request. Three ways to authenticate, all through that same
check: a real Keycloak token (OIDC/PKCE, organization-scoped); `ADMIN_API_KEY` → `admin` org,
entitled to every seeded scenario; `ENGINEERING_DEMO_API_KEY` → `engineering-demo` org, entitled
only to `platform_registry/core/seed.py`'s `ENGINEERING_DEMO_SCENARIOS` (the template for further
scoped demo tenants). Tenants without their own trained model share the fallback org's model
(`SHARED_MODEL_ORG_ID`) — caches are keyed by the *source* org so that model is loaded once.

**Shared code** — `libs/shared` (`ai-circus-shared`), a non-editable local `uv` path dependency
of every service (no workspace; each service is its own `uv` project). Use it rather than
re-implementing:
- `auth` — `AuthSettingsAdapter.from_config(get_env_config())`, `resolve_caller_identity` / `resolve_org_identity`
- `entitlements` — `PlatformRegistryClient` (pooled HTTP, bounded TTL caches, rejects unsafe path segments)
- `startup.wait_for` — wait for a dependency at boot instead of crash-looping (already used by the next two)
- `db.connect_engine` (+ `conversations`, `document_store`) — Postgres engine, URL-escaped password, waits for the DB
- `storage.ObjectStore` — tenant-prefixed SeaweedFS keys; one boto3 client per endpoint
- `observability` — `configure_metrics`, `langfuse_request_metadata`, `redact_token_query_params`
- `telemetry` — `configure_tracing` (OTLP, off without an endpoint), `install_json_sink`, `redact_attributes`
- `drift` — model-drift reference profiles and PSI (pure Python; `training` builds, `prediction` observes)
- `embeddings`, `cache` (Valkey), `events` (Kafka), `cdc`, `form_validation`, `tabular_ml`, `deployment_guard`

`libs/mcp-kit` (`mcp_kit`, ADR 0022) is separate on purpose: an MCP server in front of an existing API gets token
validation, the access policy, the per-API token exchange and audit lines from it, and its own code is its tools only.
`mcp/catalog.yaml` (ADR 0024) decides every MCP server's gateway route, both chat UIs' connections, `policy.yaml` and Keycloak
clients: edit the catalog and run `make mcp-catalog`, never the generated `BEGIN`/`END` blocks or those files by hand.
`level: 3` there (ADR 0025) makes the kit refuse to start without live token checks, a DPoP-bound per-API token and
mutual TLS to the API; a Keycloak client flag is always written "true" or "false" (Keycloak merges attribute updates).

**LLM routing.** `llm-gateway` runs the real LiteLLM proxy; `assistant`/`rag-agent`/`form-agent`/
`ui-react` call it by `model_name` (routing table: `services/llm-gateway/litellm_config.yaml`),
never a provider SDK. Local options sit behind it too: Ollama (CPU fallback, compose only) and vLLM (`make
k3s-vllm-up`, holds the GPU, ADR 0017). It also serves the `local-embed` model (sentence-transformers, in-process
torch — the reason it is the biggest app pod). A new provider key = edit `.env`, then
`make k3s-secrets` + restart; switching the active model is live from the Settings page.

**Storage & ingress.** Datasets/models/documents live in SeaweedFS (S3), never on local disk.
`infra/` holds per-component config and images (Postgres init script, Keycloak image + realm,
SeaweedFS credentials, ClickHouse low-memory config, MLflow image, k3s GPU node).

**Observability rules.**
- `data-platform-manager`'s `core/platform_status.py` is a hand-synced list: add a row for any new container (a test pins the
  names). Start/Stop is only for `core/workloads.py`'s `OPTIONAL_SERVICES`, whose workloads must equal the Role's `resourceNames`.
- Langfuse is fed only by llm-gateway's `langfuse_otel` callback: never add a Langfuse SDK to an agent; pass request `metadata`
  via `langfuse_request_metadata`. MLflow tracking must never fail a training job.
- The metrics/logs/traces *stack* is a platform prerequisite, never shipped in `k8s/base` (ADR 0015). Each service honours the
  contract: `/metrics`, `telemetry.configure_tracing(app, service)` right after `configure_metrics`, and `LOG_FORMAT=json` logs.
- `RedactingSpanExporter` must stay the only exporter: never add an OpenTelemetry distro or auto-instrumentation, and keep
  `configure_tracing(app, …)` on every FastAPI app. A new content-carrying attribute goes into `DROPPED_ATTRIBUTE_PREFIXES`.
- Log messages never carry prompts or personal data, and metric labels never carry tenants or values. A new FastAPI
  deployment gets the `prometheus.io/*` pod annotations.
- An MCP server built with `libs/mcp-kit` calls `configure_audit_output()` at start-up: its audit lines are the record of
  every tool call and refusal (ADR 0023).

**Governance rules (`docs/governance/`, ADR 0011).** The framework is governable by an external tool through open contracts
only: never a product name in code, config or docs, and it *supports* an AI management system ("supports"/"provides evidence
for", never "compliant"). **Everything is off by default** and changes nothing when off.
- **All model calls go through llm-gateway** (a libs/shared test enforces it; embeddings included).
- `llm-gateway` cannot import `ai_circus_shared`, so shared contracts are JSON Schemas under `docs/governance/schemas/`
  (regenerate with `scripts/export_governance_schemas.py`; tests fail on drift).
- A `GOVERNANCE_*`/`POLICY_*` flag must **never** get a profile default in a `settings.yaml` (config is `EnvConfig(**profile)`,
  so init values beat the environment).
- Governance events, audit and records carry ids, metrics and decisions — never prompts or personal data. Classification is
  declared, never automatic. platform-registry is the audit trail's single writer.

**ui-react.** Reaches every backend through Traefik `*.localhost` hostnames, baked in at Vite
*build* time (`src/config.ts`). Pages and Plotly are code-split (`React.lazy`) — keep heavy
dependencies out of the entry chunk. Every animated canvas runs on `sceneLoop.ts` (frames say
`moving`/`ambient`/`idle`; ambient motion rests 15 s after the last input, nothing draws while
hidden) — a new scene must not run its own endless `requestAnimationFrame`. `ScenarioPicker` grays
out a scenario whose backend is down (`serviceAvailability.ts`: each service's `/healthz` through
Traefik, one shared 30 s poll), and one whose model is not trained for the caller's tenant (`prediction`'s org-scoped
`GET /models/status`; "run `make k3s-pipeline`"). Charts: ADR 0014 (Plotly only for the chart builder, chat charts, large scatters and 3-D; a
new small chart uses `charts.tsx` or `dashboardCharts.tsx`, a new map `GeoBubbleMap`/`d3-geo`). The chat (`ChatPanel.tsx`) speaks AG-UI to each service's
`/agui/{scenario_slug}` via `@ag-ui/client`'s `HttpAgent`; CopilotKit is used only for
`useCopilotAction`/`useCopilotReadable` generative UI. **Settings** = preferences (appearance, LLM
provider, voice engine); **Platform** (admin) = operations/monitoring (Health, Monitor, Capabilities,
Deep Learning). Voice
mode talks to `agui-voice` over a plain WebSocket.

## Conventions for new or changed code

- **Async routes do no blocking I/O on the event loop.** An `async def` route (needed for
  `StreamingResponse`/WebSockets) must wrap DB, S3, sync-HTTP and CPU-heavy calls in
  `run_in_threadpool`; plain `def` routes and dependencies are threadpooled by FastAPI already.
- **Bound everything a caller controls**: request bodies (`Field(max_length=…)`), uploads (read
  at most cap+1 bytes), and every in-memory cache (size and/or TTL).
- **Startup dependencies go through `wait_for`**, never a bare first call that crashes the pod.
- **Models load at start-up, never on a request** (a demo's first question must be instant):
  anything a service holds in memory — llm-gateway's `local-embed` (`LOCAL_EMBED_PRELOAD`),
  prediction/dl-inference's `ModelCache.preload`, agui-voice's STT/TTS — is loaded and warmed
  before the pod reports Ready (readiness gated on it), best-effort so a missing model never
  blocks boot. Downloaded weights live on a PVC (`embedding-model-cache`, `voice-model-cache`).
- **Every container** (compose and k8s) has a memory limit; every k8s pod also has requests, a
  `startupProbe` + readiness + liveness probes, `runAsNonRoot` with a *numeric* `runAsUser`,
  `allowPrivilegeEscalation: false` and dropped capabilities. Service Dockerfiles follow the
  shared shape: uv cache mount + `UV_COMPILE_BYTECODE=1`, root-owned code, `USER app` (UID 1000),
  `PYTHONUNBUFFERED=1`, `MALLOC_ARENA_MAX=2`.
- **A new service/container** also needs: a `k8s/base` manifest (+ `kustomization.yaml`), a
  `docker-compose.yml` entry reusing the `x-*-env` anchors, `K3S_IMAGES` in the Makefile, a row in
  `platform_status.py`, and a `scripts/k3s_generate_secrets.sh` secret spec.

## Non-obvious symptoms (details: the `k3s-deploy-verify` skill)

- **"Failed to fetch" in the browser** is a network-level failure, not an app bug: run
  `make k3s-verify` (compose: `make verify`); common causes are the platform-registry
  port-forward not running (`ss -tlnp | grep 8010` → `make k3s-portforward`; it is a systemd user
  service `ai-circus-portforward-<cluster>` that survives reboots where user systemd exists), a stale pod after a
  same-tag image import (`rollout restart`), or opening the app from an origin other than
  `http://aiopen.localhost` (CORS allow-lists are exact).
- A `tabular_ml` scenario returning 503 "No trained model artifacts" on a healthy cluster →
  `make k3s-pipeline` never ran (always true on a fresh/recreated cluster).
- Pod exit code **135** with no logs → a truncated `.so` from a build a WSL restart killed —
  check with `verify_records.py`, don't rebuild blindly.
- Disk usage jumping ~70 GB → SeaweedFS preallocation on a volume created before the fix.
- `kubectl` flapping right after a Docker/WSL restart → k3d node-IP swap. On WSL read
  `docs/windows-wsl.md` before touching `.wslconfig`, Docker or the virtual disk.

## Branching & commits

Git-flow (`main`/`develop` permanent; `feature/*`, `release/*`, `hotfix/*`) — `AGENTS.md` §5 and
the `git-flow-finish` skill. Conventional Commits — [`styleguide.md`](styleguide.md). Never
commit before the human has inspected the diff; never `git push`.
