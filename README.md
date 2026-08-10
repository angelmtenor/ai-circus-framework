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
      ┌───────────┼───────────────────────┬─────────────────────┐
      │           │                       │                     │
┌─────▼─────┐ ┌───▼─────────────┐ ┌───────▼───┐       ┌─────────▼────┐
│ ui-react  │ │platform-registry│ │llm-gateway│       │ prediction /  │
│(Logto OIDC)│ │ (tenants/roles) │ │ (LiteLLM) │       │ assistant /   │
└───────────┘ └───────┬─────────┘ └─────┬─────┘       │ rag-agent     │
                       │                 │             └──────┬───────┘
                 ┌─────▼─────┐     ┌─────▼─────┐              │
                 │  Postgres  │     │  Qdrant   │◀─────────────┘
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

| Kind | Examples | Services involved |
|---|---|---|
| `tabular_ml` | `churn` (customer churn), `mpm` (machine predictive maintenance) — dashboard + SHAP explainability + chat | `etl-tabular` → `training` → `prediction`, plus `assistant` for chat-over-data |
| `conversational_rag` | `ai_circus_reference` — agentic chatbot over this repo's own dev/ML/GenAI reference notes, fetched straight from GitHub and vectorized | `etl-vectorize` → `rag-agent` |

A tenant (Logto **Organization**, or the shared admin credential — see below) only sees the
scenarios its members have been granted the matching `scenario:<slug>` role for — enforced
both in the UI (what's shown) and at each backend service's API (what's allowed).

**One consolidated instance per kind serves every scenario of that kind** —
`prediction`/`assistant` both load every `tabular_ml` scenario (`churn` *and* `mpm`, from the
*same* running container), and `rag-agent` every `conversational_rag` scenario, routed by a
`{scenario_slug}` path segment (`POST /predict/{slug}`, `POST /chat/{slug}`). This is
controlled by each service's `SCENARIOS` env var (comma-separated slugs; empty/unset = every
scenario of that kind) — adding a new scenario is a new `scenarios/<slug>/scenario.yaml` file,
never a new container. Both UIs render every `tabular_ml` scenario's form purely from its
`feature_columns`/`feature_schema` (see `libs/shared/scenario_schema.py`) — there is no
scenario-specific form code anywhere.

`rag-agent` is a real **LangChain tool-calling agent** (see
`services/rag-agent/src/rag_agent/core/agent.py`), not a fixed "always retrieve" pipeline: the
model decides whether a question needs its `retrieve_docs` tool at all, grounded in the
scenario's `chat.context` — chitchat/off-topic questions get answered directly, with an empty
`sources` list signaling "no retrieval happened" rather than always populating it.

### Foundations chosen for future SaaS scale

These are in place from day one — not deferred — because they're cheap to build correctly
now and expensive to retrofit once single-tenant assumptions are baked in.

- **Tenancy**: Logto **Organizations** model tenants; roles are assigned per-organization.
- **Object storage**: all datasets/models/documents live in **MinIO** (S3-compatible), never
  on a service's local disk — keeps services stateless and horizontally scalable.
- **Scenario/entitlement registry**: `platform-registry` owns a Postgres schema
  (`tenants`/`scenarios`/`entitlements`); `scenarios/*.yaml` is only the human-editable seed
  format, not read directly by any other service.
- **Ingress**: **Traefik** is the only container with a published port; every other service
  is reached through it by hostname — a 1:1 mapping onto a Kubernetes Ingress later.
- **Admin credential**: `ADMIN_API_KEY` (default `ai-circus-2026` — rotate before any real
  deployment) is a shared bearer token resolving to a fixed `admin` tenant, which
  `platform-registry` auto-grants access to *every* scenario it seeds — a real, auditable
  entitlement row, not a bypass of the entitlement check. Useful for demos/ops without
  configuring Logto at all; both `ui-react`'s login screen and this key end up enforced
  through the exact same `ai_circus_shared.auth.resolve_caller_identity` path.

### LLM providers

`llm-gateway` execs the real **LiteLLM** proxy (`litellm[proxy]`) — every consumer
(`assistant`, `rag-agent`, `ui-react`) calls its OpenAI-compatible API by `model_name`, never a
provider SDK directly (see `services/llm-gateway/litellm_config.yaml` for the routing table).
Supported providers today:

| `model_name` | Provider | Key needed | Notes |
|---|---|---|---|
| `gpt-4o-mini` | OpenAI | `OPENAI_API_KEY` | |
| `gemini-flash` | Google Gemini | `GOOGLE_API_KEY` | **Default free-tier pick** — routes to `gemini-3.1-flash-lite` (Google retires 2.x-line models early for new API keys — see `litellm_config.yaml`'s comment) |
| `deepseek-chat` | DeepSeek | `DEEPSEEK_API_KEY` | |
| `groq-llama` | GroqCloud | `GROQ_API_KEY` | Free tier, very low latency |
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | One key, many vendors; routes through OpenRouter's own `openrouter/free` auto-router by default (individual `:free` model slugs rotate too often to pin one) |
| `azure-gpt4o` | Azure OpenAI | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_API_BASE` | Also edit the `azure/<deployment>` line in `litellm_config.yaml` |
| `llama3` | Ollama (local) | none | **Optional** — off by default, see below |

**At least one of these must actually work** for `assistant`/`rag-agent` chat to answer: either
set one provider's API key(s) above in `.env` and point `LLM_MODEL` at its `model_name`, or run
`make ollama-up` to start the bundled Ollama container as a free, no-API-key fallback (leave
`LLM_MODEL=llama3`). Runtime key updates from a browser aren't possible — this deployment
doesn't run litellm's DB-backed proxy mode (see `litellm_config.yaml`'s comments) — so a new key
always means edit `.env` then `docker compose up -d llm-gateway`.

`ollama` is **not started by `make up`** — it's a real container with real RAM/disk cost sitting
idle if you already have a cloud provider key, so it's gated behind the `ollama` compose profile.
`make ollama-up` starts it and pulls **`llama3.2:3b`** (~2GB) on first run. Stick to a 3B/4B-class
model or larger if you swap it — Ollama's 1B tier answers too unreliably to be usable for real
chat.

ui-react's admin **Settings** page (`http://react.localhost` → Settings) shows every provider's
live routing status, a per-provider **Test** button (a real round-trip completion call), and a
**Test All** button that fires every provider's test concurrently — the fastest way to see which
of your configured keys are actually working.

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

Then, once you've configured Logto (see below) — or skip that entirely for a quick look, see below:

```bash
make up          # start every backend service + both UIs
make pipeline     # (re)run etl-tabular -> training for every tabular_ml scenario (SCENARIOS=all)
```

**You need a working LLM before `assistant`/`rag-agent` chat will answer anything** — `make up`
does *not* start a local model by default (see "LLM providers" below), so do one of:

- Set at least one provider API key in `.env` (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`,
  `GROQ_API_KEY`, `OPENROUTER_API_KEY`, or `AZURE_OPENAI_API_KEY`+`AZURE_OPENAI_API_BASE`) and point
  `LLM_MODEL` at it, **or**
- Run `make ollama-up` to start the bundled, free, no-API-key Ollama fallback instead (leave
  `LLM_MODEL` at its default `llama3`).

Now open **`http://react.localhost`**.

For a quick look without configuring Logto at all, log in with the **admin key**
(`ai-circus-2026` by default, see `ADMIN_API_KEY` in `.env`) on the login screen — it's
granted every scenario automatically. Otherwise sign in with a Logto-managed user (see
"First-time Logto setup" below).

Local (non-Docker) development: each generated service under `services/*/` has its own
`make run` (from `ai-circus-template`) — run it directly with `uv run` from inside that
service's directory while the infra containers stay up via `make up-infra`.

### First-time Logto setup

`make up-infra` brings up Logto at `http://logto.localhost` (sign-in) and
`http://admin.logto.localhost` (Admin Console). One-time, via the Admin Console:

1. Register an API resource for the framework's backend; note its identifier for
   `LOGTO_API_RESOURCE_INDICATOR` in `.env`.
2. Enable **Organizations**; each customer/team you want isolated is one Organization (tenant).
3. Create organization roles named `scenario:churn` / `scenario:mpm` /
   `scenario:ai_circus_reference` (one per `scenarios/*/scenario.yaml`'s `role_required`).
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
- **New scenario**: add `scenarios/<slug>/scenario.yaml` (see `churn`/`mpm` for `tabular_ml`,
  `ai_circus_reference` for `conversational_rag`) with a `chat:` block (`context` +
  `sample_questions`), re-run
  `platform-registry`'s seed step (restart it — it seeds on startup), and create the matching
  Logto role. **No new container, no UI code** — the existing `prediction`/`assistant` or
  `rag-agent` instance picks it up automatically (their `SCENARIOS` env var defaults to "every
  scenario of this kind"), and `ui-react` renders its form/chat generically.

## Testing & CI

Every backend service is an independent `ai-circus-template` project with its own QA stack —
from inside `services/<name>/`:

```bash
make check   # pre-commit (ruff, pyrefly, gitleaks, checkmake) + settings.yaml/data_model.py drift check + pytest
```

`make check-all` (from the repo root) runs this for every service in sequence. `ui-react` has
its own `npm run build` (type-checks via `tsc -b` then builds via Vite).

`.github/workflows/ci.yml` runs the same checks per service as a matrix job, builds `ui-react`,
and validates `docker-compose.yml`, on every push/PR to `main`/`develop`.

> Because every service shares this one git repository (rather than one repo per service, as
> `ai-circus-template` assumes standalone), each service's `.pre-commit-config.yaml` is scoped
> to its own `services/<name>/` subtree via a top-level `files:` filter — pre-commit always
> executes hooks with the git root as `cwd`, so without this scoping `make qa` in any one
> service would lint/reformat the entire monorepo instead of just itself.

## Kubernetes path (documented, not yet built)

Every service already reads all config from env vars and is stateless (artifacts live in
MinIO/Qdrant/Postgres, never only on local disk) — the only piece that needs re-expressing
for a minikube/Kubernetes move is Traefik's Docker-label routing, which becomes Ingress
resources (or a Traefik Kubernetes CRD). No Helm chart exists yet; this is next once the
platform is feature-complete on `docker compose`.

## Reserved for later (documented, not built)

Kubernetes/Helm manifests, a custom in-app admin screen (Logto's console covers v1), a task
queue for on-demand tenant-triggered jobs, distributed tracing/OpenTelemetry, evaluation
tooling (Opik/Giskard), voice/multimodal agents (Pipecat), per-tenant billing/metering, a
shared cache (e.g. Redis) for `prediction`/`assistant`'s per-`(org, scenario)` model/prompt
caches once running multiple replicas of either makes the current in-process `dict` cache
insufficient, and a real AG-UI runtime bridge for `ui-react`'s `rag-agent` chat (CopilotKit's
`<CopilotChat>`/Copilot Runtime protocol — `ChatPanel` calls `rag-agent` directly for now;
CopilotKit's packages aren't installed until this integration is actually built).

Every service now runs on Python 3.14 (`requires-python` in each `pyproject.toml`,
`python:3.14-slim` in each Dockerfile) — the ML-heavy dependencies (SHAP, LightGBM,
numba, pandas, scikit-learn) all ship 3.14 wheels, and every service's test suite passes
unchanged.

## Contributing

- [AGENTS.md](AGENTS.md) — mandates for AI-assisted and human contributions alike.
- [styleguide.md](styleguide.md) — commit message conventions (Conventional Commits).
