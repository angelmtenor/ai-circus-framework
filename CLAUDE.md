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
make k3s-verify              # curl checks (admin + engineering-demo tenants); then a real browser check
make k3s-pause / k3s-resume-lite
make k3s-demo / k3s-demo-off  # live demo on a 16 GB laptop: scale observability + admin Platform to 0 (~2.5 GB); verify with VERIFY_SKIP="langfuse mlflow"
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

Microservices behind **Traefik** (the only ingress; a few services also publish a
loopback-only port for non-container dev — never `traefik.enable=true` for platform-registry,
llm-gateway, qdrant or valkey). Every service is stateless and env-configured.

**Scenario-driven, not per-feature code.** A scenario (`scenarios/<slug>/scenario.yaml`,
schema in `ai_circus_shared/scenario_schema.py`) is the unit of product content, of one `kind`:
`tabular_ml`, `conversational_rag`, `assisted_form`, `deep_learning`. Adding one is a YAML file
plus a `platform-registry` restart (it seeds scenarios) — never new UI or service code. **One
service instance serves every scenario of its kind**, routed by a `{scenario_slug}` path segment:
`prediction`/`assistant` (tabular_ml), `rag-agent` (conversational_rag), `form-agent`
(assisted_form), optional `dl-inference` (deep_learning; onnxruntime only, models from the
one-shot `dl-training`, artifact contract in `ai_circus_shared/deep_learning.py`). A
`deep_learning` scenario is `task: classification` (fine-tune) or `task: anomaly_detection`
(frozen DINOv2 + patch memory bank; same ONNX interface plus an `anomaly_map` output). `ui-react`
mirrors this: `ScenarioPicker` renders whatever the entitlements API returns; `TabularView`/
`RagView`/`AssistedFormView`/`DeepLearningView` are generic renderers driven by each scenario's
`ScenarioSummary`; an optional 5th tab comes from `ui_extras` (`region_map`, `live_plant`,
`process_optimizer`, `risk_watchlist`, `network_explorer`, `dispatch_tower`, `shipment_globe`, `money_trail`, …). A `tutorial:` block (tabular_ml) adds a guided Tutorial tab; `industry` is the
scenario's *domain* (industries plus `tutorial`/`society_ethics`), shown as the picker's Domain filter. `scenario.yaml` is otherwise read only by `platform-registry` and, as
build-time config, `etl-tabular`/`training`/`prediction`.
An `assisted_form` whose `form` has `sections` renders as an official paper sheet
(`OfficialFormSheet.tsx`: numbered boxes/`casilla`, `variants` = general + specific models chosen by
the classification field, `locale` for messages); form-agent prints the same YAML as PDF
(`core/pdf.py`, reportlab: draft + filed copy with receipt) and serves `form.sample_uploads`.
A tabular feature can be `type: text` (free text): TF-IDF step `text_<col>` inside the pipeline,
SHAP terms rolled up server-side (`tabular_ml.original_feature`), per-word spans via `/predict`
`explain_text`; `explain: false` is the fast bulk path (TreeExplainer SHAP ≈5 ms/row). Such a
scenario may add a `model.text_challenger` (sentence embeddings from llm-gateway's `local-embed`,
cached from the host GPU — the cluster's CPU embedder is ~0.6 s/text; served as `/predict`
`model: "challenger"`, never auto-promoted) and a `rubric_check` (assistant `POST /rubric-check`: the
active LLM reads a *description of behaviour* against a YAML rubric — never judges named people).
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
A `tabular_ml` scenario may also give its assistant a **document search as one more tool**
(`documents` + `documents.tool` {name, label, description, sample_questions} + `vector_store`, e.g.
`prestaciones_sociales`' `consultar_normativa` over real BOE/EUR-Lex articles + fictional local rules,
fetched by `scripts/prepare_prestaciones_normativa.py`): etl-vectorize indexes it like a RAG scenario,
assistant builds the tool per request (`ai_circus_shared.retrieval` — the one `retrieve`/`format_retrieved`
rag-agent and form-agent share), serves `GET /documents/{slug}`, and honours AG-UI `forwardedProps.scope:
"documents"` by building **that tool alone**. ChatPanel labels every reply with the sources its tool
calls really used (`chatProvenance.ts`); case_desk adds a reading room (`NormativaDesk.tsx`, «📖 Ver norma»
on a fired rule's `legal_basis` — a libs/shared test checks each one resolves to a shipped document).
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
- `embeddings`, `cache` (Valkey), `events` (Kafka), `cdc`, `form_validation`, `tabular_ml`, `deployment_guard`

**LLM routing.** `llm-gateway` runs the real LiteLLM proxy; `assistant`/`rag-agent`/`form-agent`/
`ui-react` call it by `model_name` (routing table: `services/llm-gateway/litellm_config.yaml`),
never a provider SDK. It also serves the `local-embed` model (sentence-transformers, in-process
torch — the reason it is the biggest app pod). A new provider key = edit `.env`, then
`make k3s-secrets` + restart; switching the active model is live from the Settings page.

**Storage & ingress.** Datasets/models/documents live in SeaweedFS (S3), never on local disk.
`infra/` holds per-component config and images (Postgres init script, Keycloak image + realm,
SeaweedFS credentials, ClickHouse low-memory config, MLflow image, k3s GPU node).

**Observability (admin-only)**, all reachable from ui-react's admin **Platform** view: the health
dashboard (`data-platform-manager` `GET /platform/status`, `core/platform_status.py` — a
hand-synced list of every component; add a row for any new container, `tests/test_platform_status.py`
pins the names; Start/Stop only for `core/workloads.py`'s `OPTIONAL_SERVICES`, whose workloads
must equal the Role's `resourceNames` — `tests/test_workloads.py`); the resource monitor
(`GET /platform/resources`, `core/resources.py`: metrics-server + NVML via `k8s/gpu/`'s patch); **Langfuse v4** for GenAI (fed only by llm-gateway's `langfuse_otel` callback —
never add a Langfuse SDK to an agent; pass request `metadata` via `langfuse_request_metadata`);
**MLflow** for ML (`training`'s `core/mlflow_tracking.py`, must never fail the job).

**ui-react.** Reaches every backend through Traefik `*.localhost` hostnames, baked in at Vite
*build* time (`src/config.ts`). Pages and Plotly are code-split (`React.lazy`) — keep heavy
dependencies out of the entry chunk. The chat (`ChatPanel.tsx`) speaks AG-UI to each service's
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
