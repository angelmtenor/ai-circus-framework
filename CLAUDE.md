# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mandatory reading

**Read [`AGENTS.md`](AGENTS.md) before making any change** — it is the root-level policy (security/tenancy
rules, git-flow branching, human-in-the-loop/no-`git push` protocol, verification requirements) and is
STRICTLY MANDATORY. If a task touches a specific service under `services/<name>/` or `ui-react/`, read
that directory's **own** `AGENTS.md` too — each is an independently cookiecutter-generated project with
its own conventions layered on top of these root ones.

The two rules from `AGENTS.md` most likely to matter mid-task, worth restating here:
- Any code path reading scenario data, model artifacts, or vector search results **must** be scoped by
  `org_id` (the Logto Organization / tenant) — see `libs/shared/src/ai_circus_shared/storage.py` and
  `entitlements.py` for the enforced pattern. Entitlement checks belong in the backend service, not just
  the UI.
- Never hand-write a new service's `pyproject.toml`/`Dockerfile`/`settings.yaml` — always scaffold via
  `./scripts/new_service.sh <name>` (real cookiecutter generation from `ai-circus-template`).

## Commands

```bash
make bootstrap              # copy .env.example -> .env (edit it: at least one LLM key, or `make ollama-up`)
make all                     # one-shot: infra + services + ETL/training pipelines + end-to-end verify
make reset-all               # nuke containers+volumes (postgres/logto/qdrant/minio data too), then `make all`
make up-infra                # postgres, logto, qdrant, minio, traefik only
make up                      # every backend service + ui-react
make pipeline                # (re)run churn's etl-tabular -> training -> prediction
make verify                  # curl-check the exact requests the login screen makes (diagnoses "Failed to fetch")
make logs                    # tail all container logs
make down                    # stop everything

make check-all               # `make check` inside every services/* project, in sequence
make sync-shared              # after editing libs/shared: `uv sync --reinstall-package ai-circus-shared` everywhere
make new-service NAME=foo    # scaffold a new backend service from ai-circus-template

make k3s-cluster              # create the local k3d cluster (see k8s/README.md for the full k3s-* workflow)
```

Per-service (`cd services/<name>/`, or `cd ui-react/`):
```bash
make check                   # pre-commit (ruff, pyrefly, gitleaks, checkmake) + settings.yaml/data_model.py drift + pytest
make run                     # run the service locally with uv (infra containers must already be up: make up-infra)
uv run pytest path/to/test_file.py::test_name   # a single test
uv run platform-registry-provision-owner        # (platform-registry only) create/find the real Logto owner user — see README "First-time Logto setup"
```
```bash
cd ui-react/
npm run dev                  # Vite dev server
npm run build                # tsc -b (typecheck) then vite build — this is ui-react's "check"
npm run lint                 # oxlint
```

There is no root-level test command — each `services/*/` directory and `ui-react/` is an independent
project. CI (`.github/workflows/ci.yml`) runs `make qa`/`make test` per service as a matrix job, builds
`ui-react`, and validates `docker-compose.yml`.

**Debugging "Failed to fetch" in the browser**: this is a network-level failure (request never reached a
server), not an application bug. Run `make verify` first — it reproduces the login screen's exact calls
with logs. Common causes: testing right after `make up` before containers are actually healthy (use
`make all`/`make wait-services` instead of raw `docker compose up -d`), a stale `postgres-data` volume
from an earlier partial run (`make reset-all`), a port already bound on the host (80/8010/6333/4000), or
opening the app via a different origin than `http://aiopen.localhost` (CORS allow-lists are keyed to it
exactly).

## Architecture

Microservices behind **Traefik** (the only container reachable from outside the host — a few services
additionally publish a **loopback-only** port for non-Docker local dev, never `traefik.enable=true`).
Runs via `docker compose up` day-to-day; every service is stateless/env-configured by design, which
also runs unchanged on a local single-node k3s (k3d) cluster — see [`k8s/README.md`](k8s/README.md)
and the `make k3s-*` targets (dev-parity only, not a production/multi-node setup).

### Scenario-driven, not per-feature code

A **scenario** (`scenarios/<slug>/scenario.yaml`) is the unit of product content — three `kind`s exist
(`tabular_ml`, `conversational_rag`, `assisted_form`; schema in
`libs/shared/src/ai_circus_shared/scenario_schema.py`). Adding a new scenario is a YAML file plus
restarting `platform-registry` (which seeds it) — never new UI or container code. **One consolidated
service instance serves every scenario of a given kind**: `prediction` and `assistant` load every
`tabular_ml` scenario from the same running container, routed by a `{scenario_slug}` path segment;
`rag-agent` does the same for `conversational_rag`, `form-agent` for `assisted_form`. `ui-react` mirrors
this on the frontend — `ScenarioPicker` renders whatever the entitlements API returns, and
`TabularView`/`RagView`/`AssistedFormView` are generic renderers driven entirely by each scenario's
`ScenarioSummary` (feature schema, form config, chat context) — there is no per-scenario UI code.
`scenario.yaml` is read directly by `etl-tabular`/`training`/`prediction` too (dataset schema, model
candidates) as build-time config; it is otherwise never read by services other than `platform-registry`.

### Tenancy & entitlements

Logto **Organizations** model tenants; a tenant only sees scenarios its members hold the matching
`scenario:<slug>` role for. `platform-registry` owns the source of truth in Postgres
(`tenants`/`scenarios`/`entitlements`) and every other backend service calls its entitlement check before
serving a request, regardless of what the UI already filtered client-side. Three ways to authenticate,
all resolving through the *same* entitlement path (`ai_circus_shared.auth.resolve_caller_identity`) — none
is a bypass of the real check:
- Real Logto sign-in (OIDC/PKCE from `ui-react`, organization-scoped access token).
- `ADMIN_API_KEY` bearer token → fixed `admin` org id, entitled to every seeded scenario.
- `ENGINEERING_DEMO_API_KEY` bearer token → fixed `engineering-demo` org id, entitled only to the
  scenarios in `services/platform-registry/src/platform_registry/core/seed.py`'s
  `ENGINEERING_DEMO_SCENARIOS` — the template to follow for adding further scoped demo tenants.

### Shared code

`libs/shared` (`ai-circus-shared`) holds cross-service code — Logto token validation (`auth.py`),
entitlement-check client (`entitlements.py`), MinIO object storage client (`storage.py`), the
`scenario.yaml` Pydantic schema (`scenario_schema.py`), tabular ML helpers, form validation — added to
each service as a local **non-editable** `uv` path dependency (no monorepo-wide workspace; each service
under `services/*/` is its own independent `uv` project, generated via cookiecutter from
`ai-circus-template`). After editing `libs/shared`, run `make sync-shared` from the root.

### LLM routing

`llm-gateway` execs the real **LiteLLM** proxy; `assistant`/`rag-agent`/`ui-react` all call it by
`model_name` (never a provider SDK directly) — see `services/llm-gateway/litellm_config.yaml` for the
routing table. Runtime provider *key* rotation isn't possible from the browser (no DB-backed LiteLLM proxy
mode here) — a new key means editing `.env` then `docker compose up -d llm-gateway`. Switching which
already-configured model is *active*, though, is instant from `ui-react`'s Settings page.

### Object storage & ingress

All datasets/models/documents live in **MinIO** (S3-compatible), never on a service's local disk, keeping
services stateless. `infra/{postgres,logto,qdrant,minio,traefik}/` holds per-service config directories —
today only `infra/postgres/` has real content (a multi-database init script); the rest is inline in
`docker-compose.yml`.

### ui-react specifics

The SPA reaches every backend only through Traefik's `*.localhost` hostnames (browser-side, never
docker-internal service names) — base URLs are baked in at Vite **build** time (`ui-react/src/config.ts`),
not injected at container start. The assistant chat (`ChatPanel.tsx`) talks AG-UI wire protocol straight
to each service's `/agui/{scenario_slug}` endpoint via `@ag-ui/client`'s `HttpAgent`, not CopilotKit's
GraphQL runtime — CopilotKit is still used for `useCopilotAction`/`useCopilotReadable` (generative UI:
the chat can render live charts/tables via the real `prediction` API, and in `assisted_form` scenarios can
write directly into the form's state).

## Branching & commits

Git-flow (`main`/`develop` permanent; `feature/*`, `release/*`, `hotfix/*` ephemeral) — see `AGENTS.md`
§5 for the exact finish commands. Commit messages follow Conventional Commits — see
[`styleguide.md`](styleguide.md).
