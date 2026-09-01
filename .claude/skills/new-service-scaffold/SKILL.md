---
name: new-service-scaffold
description: Scaffold a new backend service in ai-circus-framework via the real cookiecutter template — never hand-write a service's pyproject.toml/Dockerfile/settings.yaml.
version: 1.0.0
---

# New Service Scaffold

## Overview

Every service under `services/*/` is generated from the sibling `ai-circus-template` repo via
real `cookiecutter`, wrapped by `./scripts/new_service.sh <name>` (equivalently
`make new-service NAME=<name>` from the repo root). This is the only supported way to create a
new service — `AGENTS.md` §4 marks hand-writing a service's `pyproject.toml`, `Dockerfile`, or
`settings.yaml` from scratch as STRICTLY PROHIBITED, since it would drift from every other
service's cookiecutter-generated conventions (uv, ruff, pyrefly, gitleaks, checkmake, pytest,
`settings.yaml`/`data_model.py` drift-check pipeline).

## When to use

- The user asks to add a new microservice, backend, or API to this repo.
- A task implies a new `services/<name>/` directory that doesn't exist yet.

## When NOT to use

- Adding a new **scenario** (`scenarios/<slug>/scenario.yaml`) does NOT need this — scenarios are
  YAML content served by the *existing* `platform-registry`/`prediction`/`assistant`/`rag-agent`/
  `form-agent` services, never a new service per scenario. See the "Scenario-driven, not
  per-feature code" section of root `CLAUDE.md` before reaching for this skill.
- Modifying an existing service — just edit it directly.

## Prerequisites — check before running

The script needs the **sibling** `ai-circus-template` repo checked out separately; it is **not
vendored inside ai-circus-framework**. It reads `$AI_CIRCUS_TEMPLATE`, defaulting to
`$HOME/PROJECTS/ai-circus-template`. Verify it exists first:

```bash
ls "${AI_CIRCUS_TEMPLATE:-$HOME/PROJECTS/ai-circus-template}"
```

If it's missing, the script will fail — tell the human operator to clone it rather than trying to
improvise a template.

## Workflow

1. Run the scaffold:
   ```bash
   ./scripts/new_service.sh <name>
   ```
   This invokes cookiecutter with `--no-input` (project_name, description, author, org,
   `python_version=3.14`), writes into `services/<name>/`, strips the nested `.git` the template's
   post-gen hook creates, and runs `uv add ../../libs/shared` inside the new service (adding
   `ai-circus-shared` as a non-editable local path dependency) if `uv` is available.

2. **The script does not update `docker-compose.yml`.** Add the new service to the root
   `docker-compose.yml` by hand, following the pattern of an existing service of the same kind —
   internal-only unless it needs Traefik ingress (only services actually reached from the browser
   get `traefik.enable=true`; most get a loopback-only port instead, per root `CLAUDE.md`).

3. From the repo root, run `make sync-shared` to make sure every service (including the new one)
   is pinned to the current `libs/shared` build.

4. If the new service will read scenario data, model artifacts, or vector search results, wire in
   tenant scoping from the start — every such code path **must** be scoped by `org_id` (the Keycloak
   Organization). Follow the enforced pattern in `libs/shared/src/ai_circus_shared/storage.py` and
   `entitlements.py`, and add the entitlement check in the new service itself, not just the UI —
   see root `AGENTS.md` §1.

   **Known gap:** the `ai-circus-template` repo this skill scaffolds from has not been migrated
   off Logto — the generated `services/<name>/settings.yaml` will still declare `LOGTO_ISSUER`/
   `LOGTO_JWKS_URL`/`LOGTO_API_RESOURCE_INDICATOR`, not the `KEYCLOAK_*` names this repo now uses.
   Rename those three fields by hand to match the pattern in an existing service's `settings.yaml`
   (e.g. `services/prediction/`) and regenerate `data_model.py` before considering auth wiring
   done — don't assume a freshly scaffolded service gets Keycloak auth for free.

5. Read the newly generated `services/<name>/AGENTS.md` and `SKILLS.md` — they layer
   service-specific conventions on top of these root ones.

6. Verify with the [service-check](../service-check/SKILL.md) skill before considering the new
   service done.

## Key rules

- Never bypass this script to write `pyproject.toml`/`Dockerfile`/`settings.yaml` by hand.
- Never invent a new service for something that should be a `scenario.yaml` instead.
- Don't commit or push without the human operator inspecting the diff first (root `AGENTS.md` §3).

## References

- `./scripts/new_service.sh` — the actual scaffolding script.
- Root `AGENTS.md` §1 (tenancy) and §4 (scaffolding mandate).
- Root `CLAUDE.md` — "Scenario-driven, not per-feature code" and "Tenancy & entitlements" sections.
