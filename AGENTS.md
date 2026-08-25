# AGENTS.md — AI Agent Development & Verification Guide (monorepo root)

Foundational mandates for AI coding agents (Claude Code, GitHub Copilot, Gemini CLI, Codex,
Cursor, or any other agent) operating on **ai-circus-framework**. This is the root-level
policy; every service under `services/*/` and `ui-react/` is itself a
cookiecutter-generated project with its **own** `AGENTS.md` — read that one too when working
inside a specific service. Adherence is STRICTLY MANDATORY.

## 🚨 1. Security & System Integrity (Priority Zero)
- **Credential Protection:** NEVER log, print, or commit secrets, API keys, or Logto/SeaweedFS/LLM
  credentials. Every `.env` (root and per-service) is gitignored except `.env.example`.
- **Multi-tenancy is not optional:** any code path that reads scenario data, model artifacts,
  or vector search results MUST be scoped by `org_id` (the Logto Organization / tenant). Never
  add a query, SeaweedFS path, or Qdrant collection lookup that isn't tenant-scoped — see
  `libs/shared/src/ai_circus_shared/storage.py` and `entitlements.py` for the enforced pattern.
- **Entitlement checks happen at the API, not just the UI:** every backend service must call
  `platform-registry`'s entitlement check before serving a scenario request, regardless of what
  the calling UI already filtered.
- **AI Context Exclusions:** never read/analyze `.env`, `.env.*` (except `.env.example`),
  `*.pem`, `*.key`, `**/credentials*`, `**/secrets*`, or `.cache/` contents, even with
  filesystem access that bypasses `.gitignore`.

## 📚 2. Context Discovery Requirement
- If your Claude Code plans directory has an approved project plan for this repo, read it before
  making architectural changes — it records the reasoning behind the tenancy/storage/ingress/
  scenario-registry decisions, not just the "what". (This plan lives in a per-contributor local
  path, e.g. `~/.claude/plans/`, not in the repo — it won't exist for every clone/contributor.)
- If the task touches a specific service, read that service's own `AGENTS.md`/`SKILLS.md` first.

## 👥 3. Human-in-the-Loop Protocol
- **Inspection Required:** never commit until the human operator has inspected changes.
- **No Pushing:** STRICTLY PROHIBITED from running `git push`.
- **Confirmation Loop:** for destructive or significant operations (dropping a volume, `git
  reset`, rewriting a generated service), explain intent first and wait for approval.

## ✅ 4. Verification is Mandatory
- **Definition of Done** for a new/changed service: `make check` (inside that service) passes,
  plus an actual `docker compose up` smoke test of the affected service(s) — not just unit tests.
- **Cross-service changes:** run `make check-all` from the repo root.
- **New service scaffolding:** always go through `./scripts/new_service.sh <name>` (real
  cookiecutter generation from `ai-circus-template`) — never hand-write a service's
  `pyproject.toml`/`Dockerfile`/`settings.yaml` from scratch.

## 🌳 5. Branching Strategy — git-flow
- **Model:** `main` (production-ready, tagged releases) and `develop` (integration branch) are
  permanent; `feature/*`, `release/*`, and `hotfix/*` are ephemeral. Use the `git flow` CLI (AVH
  edition) rather than hand-rolled merges — run `git flow init -d` once per clone if it hasn't
  been initialized yet.
- **Feature work:** branch from `develop` as `feature/<name>`; finish with
  `git flow feature finish <name>` (merges into `develop`, deletes the branch). Never merge a
  feature branch straight into `main`.
- **Releases:** cut `release/<version>` from `develop` with `git flow release start vX.Y.Z`;
  `git flow release finish vX.Y.Z` merges it into both `main` and `develop` and tags it.
  Versioning follows SemVer.
- **Hotfixes:** for a production-only fix, branch `hotfix/<name>` from `main`; finish with
  `git flow hotfix finish <name>` (merges into both `main` and `develop`, tags it).
- **Agents:** the "No Pushing" rule in section 3 applies here too — prepare and surface the exact
  `git flow`/`git push` commands (including the version/tag to use) for the human operator to run
  or explicitly approve; never run `git push`, `git flow feature finish`, `git flow release
  finish`, or `git flow hotfix finish` unprompted.

### Branch protection (recommended — apply manually, this is a GitHub setting, not a file)
For both `main` and `develop`, under GitHub → Settings → Branches:
- **Required status checks:** `service-check`, `ui-react`, `compose-validate`, `gitleaks`,
  `container-scan`, `sbom`, `commitlint` (from `.github/workflows/ci.yml`). Leave
  `integration-smoke` out of the required list for now — it runs with `continue-on-error: true`
  until it's proven non-flaky; promote it once it is.
- **Require a pull request before merging**, with at least one approving review.
- **Do not allow direct pushes** to `main` or `develop` — every change lands via PR, matching the
  git-flow model above.
