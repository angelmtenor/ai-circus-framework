# ai-circus-framework

A scalable, multi-tenant microservices platform for building and demoing data-science and
GenAI **scenarios** (tabular ML dashboards, agentic RAG chatbots, ...) behind a real login —
combining the microservices/`docker compose up` shape of `mlops_templates`, the pluggable
scenario pattern and GenAI ambitions of `smart-data-science`, and the `ai-circus-template`
scaffold (uv/ruff/pyrefly/CI) used for every backend service.

Runs today via `docker compose up`; designed — not yet built — to migrate to
minikube/Kubernetes later (see "Kubernetes path" below).

---

## Architecture

```
                              ┌─────────────┐
                        ┌────▶│   Traefik   │◀──── single ingress point (*.localhost)
                        │     └─────────────┘
        ┌───────────────┼───────────────────────────────┬─────────────────────┐
        │               │                               │                     │
 ┌──────▼──────┐ ┌──────▼──────┐ ┌───────────────┐ ┌─────▼─────┐       ┌───────▼──────┐
 │ ui-streamlit│ │  ui-react   │ │platform-registry│ │llm-gateway│       │ prediction / │
 │ (Logto OIDC)│ │(CopilotKit) │ │ (tenants/roles) │ │ (LiteLLM) │       │ assistant /  │
 └─────────────┘ └─────────────┘ └───────┬─────────┘ └─────┬─────┘       │ rag-agent    │
                                          │                 │            └──────┬───────┘
                                    ┌─────▼─────┐     ┌─────▼─────┐             │
                                    │  Postgres  │     │  Qdrant   │◀────────────┘
                                    │(logto+     │     │(per-tenant│
                                    │ platform)  │     │collections│
                                    └─────┬──────┘     └───────────┘
                                          │
                                    ┌─────▼─────┐            ┌──────────────┐
                                    │   Logto    │            │    MinIO     │◀── datasets, models,
                                    │(IDP/tenants)│            │(object store)│    documents
                                    └────────────┘            └──────┬───────┘
                                                                     │
                                                        ┌────────────┴────────────┐
                                                        │ etl-tabular / training / │
                                                        │      etl-vectorize      │
                                                        └──────────────────────────┘
```

### Scenarios (= apps a tenant is entitled to)

A **scenario** is a self-contained demo, declared once in `scenarios/<slug>/scenario.yaml`
and seeded into `platform-registry`'s Postgres schema at bootstrap (see "Scenario registry"
below). Two kinds exist today:

| Kind | Example | Services involved |
|---|---|---|
| `tabular_ml` | `churn` — customer churn prediction + SHAP explainability + chat | `etl-tabular` → `training` → `prediction`, plus `assistant` for chat-over-data |
| `conversational_rag` | `docs_rag` — agentic chatbot over vectorized documents | `etl-vectorize` → `rag-agent` |

A tenant (Logto **Organization**) only sees the scenarios its members have been granted the
matching `scenario:<slug>` role for — enforced both in the UI (what's shown) and at each
backend service's API (what's allowed).

### Foundations chosen for future SaaS scale

These are in place from day one — not deferred — because they're cheap to build correctly
now and expensive to retrofit once single-tenant assumptions are baked in. See the project
plan (`/home/amartinez3/.claude/plans/swirling-nibbling-cocoa.md`) for the full reasoning.

- **Tenancy**: Logto **Organizations** model tenants; roles are assigned per-organization.
- **Object storage**: all datasets/models/documents live in **MinIO** (S3-compatible), never
  on a service's local disk — keeps services stateless and horizontally scalable.
- **Scenario/entitlement registry**: `platform-registry` owns a Postgres schema
  (`tenants`/`scenarios`/`entitlements`); `scenarios/*.yaml` is only the human-editable seed
  format, not read directly by any other service.
- **Ingress**: **Traefik** is the only container with a published port; every other service
  is reached through it by hostname — a 1:1 mapping onto a Kubernetes Ingress later.

### Shared code

Every backend service is generated via **real `cookiecutter` generation** against
[`ai-circus-template`](../ai-circus-template) (see `scripts/new_service.sh`), so each stays an
independent `uv` project with its own `pyproject.toml`/`uv.lock`/Dockerfile — no monorepo-wide
uv workspace. Common code (Logto token validation, MinIO client, entitlement-check client,
scenario schema) lives in `libs/shared` (`ai-circus-shared`), added to each service as a local
**non-editable** `uv` path dependency, so `uv sync` builds it into a self-contained wheel and
the runtime image never needs `libs/shared` copied in separately.

---

## Quick start

```bash
make bootstrap   # copy .env.example -> .env; then fill in Logto/LLM/MinIO secrets
make up-infra    # start postgres, logto, qdrant, minio, traefik
```

Then, once you've configured Logto (see below):

```bash
make up          # start every backend service + both UIs
make pipeline     # (re)run the churn scenario's etl -> training -> prediction pipeline
```

Local (non-Docker) development: each generated service under `services/*/` has its own
`make run` (from `ai-circus-template`) — run it directly with `uv run` from inside that
service's directory while the infra containers stay up via `make up-infra`.

### First-time Logto setup

`make up-infra` brings up Logto at `http://logto.localhost` (sign-in) and
`http://admin.logto.localhost` (Admin Console). One-time, via the Admin Console:

1. Register an API resource for the framework's backend; note its identifier for
   `LOGTO_API_RESOURCE_INDICATOR` in `.env`.
2. Enable **Organizations**; each customer/team you want isolated is one Organization (tenant).
3. Create organization roles named `scenario:churn` / `scenario:docs_rag` (one per
   `scenarios/*/scenario.yaml`'s `role_required`).
4. Under **Sign-in Experience**, upload your logo/colors — end users get this branded, hosted
   page; no custom login screen is built in this repo (see `AGENTS.md`'s reasoning: managed
   auth over custom auth).
5. Add users to an Organization and assign them the relevant `scenario:*` role(s) — that
   assignment *is* what grants access to a scenario.

---

## Adding a new scenario or service

- **New backend service**: `make new-service NAME=my-service` — wraps real cookiecutter
  generation from `ai-circus-template`, wires in `libs/shared`, and adapts the Dockerfile for
  this repo's build-context conventions. Then add it to `docker-compose.yml`.
- **New scenario**: add `scenarios/<slug>/scenario.yaml` (see `churn`/`docs_rag` for the two
  kinds), re-run `platform-registry`'s seed step, and create the matching Logto role.

## Kubernetes path (documented, not yet built)

Every service already reads all config from env vars and is stateless (artifacts live in
MinIO/Qdrant/Postgres, never only on local disk) — the only piece that needs re-expressing
for a minikube/Kubernetes move is Traefik's Docker-label routing, which becomes Ingress
resources (or a Traefik Kubernetes CRD). No Helm chart exists yet; this is next once the
platform is feature-complete on `docker compose`.

## Reserved for later (documented, not built)

Kubernetes/Helm manifests, a custom in-app admin screen (Logto's console covers v1), a task
queue for on-demand tenant-triggered jobs, distributed tracing/OpenTelemetry, evaluation
tooling (Opik/Giskard), voice/multimodal agents (Pipecat), per-tenant billing/metering.

## Contributing

- [AGENTS.md](AGENTS.md) — mandates for AI-assisted and human contributions alike.
- [styleguide.md](styleguide.md) — commit message conventions (Conventional Commits).
