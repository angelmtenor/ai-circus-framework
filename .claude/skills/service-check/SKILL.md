---
name: service-check
description: Definition-of-Done for a change in ai-circus-framework — make check, make check-all, and an actual docker compose smoke test, since CI's compose-validate never boots containers.
version: 1.0.0
---

# Service Check (Definition of Done)

## Overview

Root `AGENTS.md` §4 (Verification is Mandatory) defines Definition of Done for any new or changed
service. This skill is the checklist to run before calling a change complete — whether or not
you're about to commit (which still requires human inspection first, per `AGENTS.md` §3).

## When to use

- Before reporting any code change in `services/*/` or `libs/shared` as finished.
- Before proposing a commit.

## Workflow

1. **Per-service check**, from inside the touched service directory:
   ```bash
   cd services/<name>
   make check
   ```
   This runs `make qa` (pre-commit: ruff-check, ruff-format, pyrefly-check, gitleaks, checkmake,
   plus the service's `settings.yaml`/`data_model.py` drift check) followed by `make test`
   (pytest). All of it must pass — don't hand-wave a failing step.

2. **Cross-service changes** (anything touching `libs/shared`, or a change that's supposed to
   apply the same way to multiple services): run `make check-all` from the repo root, which loops
   `make check` across every `services/*/` directory in sequence.

3. **After editing `libs/shared`**: run `make sync-shared` from the repo root
   (`uv sync --reinstall-package ai-circus-shared` per service) before step 1/2, otherwise services
   are still testing against the old shared build.

4. **Actual integration smoke test — do not skip this.** CI's `compose-validate` job only runs
   `docker compose config --quiet` (static YAML validation) — it never boots a single container.
   `make check`/`make check-all` are unit-test-level only. The real Definition of Done requires
   bringing the affected service(s) up for real:
   ```bash
   make up-infra      # if infra isn't already running
   make up             # or docker compose up -d <service> for a narrower smoke test
   make verify          # curl-checks the exact requests the login screen makes
   ```
   For a UI-facing change, also exercise the actual feature in a browser at the relevant
   `*.localhost` Traefik hostname — golden path and at least one edge case — per root `CLAUDE.md`'s
   guidance on UI changes.

## Key rules

- `make check` passing is necessary but not sufficient — it does not replace an actual running
  smoke test.
- Never claim a feature "works" from type-checking/unit tests alone if it wasn't exercised live.
- If `make check` surfaces a real finding (a gitleaks hit, a pyrefly type error, a coverage
  regression against a service's `--cov-fail-under` floor), fix the root cause — don't suppress it
  with an ignore rule or lower the threshold to make it pass.

## References

- Root `AGENTS.md` §4 (Verification is Mandatory).
- Root `CLAUDE.md` — "Debugging 'Failed to fetch'" section, and the per-service `make check`
  command table.
