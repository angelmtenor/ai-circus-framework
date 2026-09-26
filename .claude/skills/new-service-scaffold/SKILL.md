---
name: new-service-scaffold
description: Scaffold a new backend service in ai-circus-framework via the real cookiecutter template (never hand-write a service's pyproject.toml/Dockerfile/settings.yaml), then wire it into k8s, docker-compose, the Makefile, secrets and the admin health dashboard. Use when adding a new microservice/API — not for a new scenario (that's just a scenario.yaml).
version: 2.0.0
---

# New Service Scaffold

Every `services/*/` project is generated from the sibling `ai-circus-template` repo by
`./scripts/new_service.sh <name>` (`make new-service NAME=<name>`). Hand-writing a service's
`pyproject.toml`/`Dockerfile`/`settings.yaml` is prohibited (`AGENTS.md` §4) — it drifts from
the uv/ruff/pyrefly/gitleaks/checkmake/pytest and settings.yaml→`data_model.py` conventions.

## When NOT to use

- A new **scenario** is a `scenarios/<slug>/scenario.yaml` served by the existing service for
  its `kind` — never a new service (root `CLAUDE.md`, "Scenario-driven").
- Changing an existing service — just edit it.

## Prerequisite

The template is not vendored: `$AI_CIRCUS_TEMPLATE` if set, else a local checkout at
`~/PROJECTS/ai-circus-template`, else the published
[angelmtenor/ai-circus-template](https://github.com/angelmtenor/ai-circus-template) (cookiecutter
clones it). `cookiecutter` must be on `PATH` (`uv tool install cookiecutter`) — don't improvise a
template.

## 1. Generate

```bash
./scripts/new_service.sh <name>
```

Runs cookiecutter (`python_version=3.14`), strips the nested `.git`, `uv add ../../libs/shared`,
and writes the monorepo-aware Dockerfile (repo-root build context, uv cache mount, precompiled
bytecode, non-root UID 1000 — the same shape as every other service). Then read the generated
`services/<name>/AGENTS.md` and `SKILLS.md`.

**Verify the auth settings.** The template historically shipped Logto settings
(`LOGTO_ISSUER`/`LOGTO_JWKS_URL`/`LOGTO_API_RESOURCE_INDICATOR`); if `settings.yaml` still has
them, rename to the `KEYCLOAK_*`/`ADMIN_API_KEY`/`ENGINEERING_DEMO_API_KEY`/`AUTH_DISABLED`/
`DEV_ORG_ID`/`PLATFORM_REGISTRY_URL` fields of an existing service (e.g. `services/prediction/`)
and `make generate-data-model`.

## 2. Code it the platform way

- **Identity**: copy `services/prediction/src/prediction/core/identity.py` — it is
  `AuthSettingsAdapter.from_config(get_env_config())` + `resolve_caller_identity`, mapping
  `TokenValidationError`→401 and `EntitlementDeniedError`→403. Every scenario route depends on it.
- **Tenancy**: anything reading scenario data, models or vectors is scoped by `org_id` — use
  `ai_circus_shared.storage.ObjectStore` (tenant-prefixed keys), never raw bucket paths.
- **Start-up**: connect dependencies through the shared helpers that wait for them
  (`ObjectStore.connect`, `ai_circus_shared.db.connect_engine`, or `startup.wait_for` for anything
  else) — never a bare call that crash-loops the pod while its dependency boots.
- **Async routes** wrap blocking work in `run_in_threadpool`; bound request bodies, uploads and
  caches; call `configure_metrics(app)` and `enforce_safe_for_public_deployment(...)` like the
  other `app.py`s.

## 3. Wire it into the platform (the script does none of this)

1. **`k8s/base/<name>.yaml`** + add it to `k8s/base/kustomization.yaml` — copy
   `k8s/base/prediction.yaml`: `runAsNonRoot` + `runAsUser: 1000`, dropped capabilities,
   `automountServiceAccountToken: false`, memory requests/limits (measure with `kubectl top pods`),
   `startupProbe` (2s × 90) + readiness + liveness on `/healthz`. Add an `IngressRoute` only if
   the browser calls it.
2. **`scripts/k3s_generate_secrets.sh`** — a `<name>-secrets|KEY,KEY,...` entry in `SECRET_SPECS`
   with only the keys this service needs.
3. **`Makefile`** — add `<name>` to `K3S_IMAGES` (and to `k3s-wait`'s list if long-running).
4. **`docker-compose.yml`** — follow a same-kind service; merge the shared `x-*-env` anchors
   (`<<: [*service-auth-env, *object-store-env, …]`) instead of repeating env vars; set
   `mem_limit`; Traefik labels only if browser-facing, otherwise a loopback-only port at most.
5. **`services/data-platform-manager/src/data_platform_manager/core/platform_status.py`** — a
   row for the new component (its test pins the names).
6. `make sync-shared`.

## 4. Verify

The `service-check` skill: `make check` in the new service, `make check-all`, then build/import/
deploy on k3s and `make k3s-verify` (+ a browser check if the UI talks to it). CI picks the new
service up automatically (`discover-services` matrix: check, image build, SBOM, Trivy).

## Key rules

- Never bypass the script for `pyproject.toml`/`Dockerfile`/`settings.yaml`.
- Never invent a service for what should be a `scenario.yaml`.
- No commit or push without the human inspecting the diff (`AGENTS.md` §3).
