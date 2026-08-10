# AGENTS.md — AI Agent Development & Verification Guide (monorepo root)

Foundational mandates for AI coding agents (Claude Code, GitHub Copilot, Gemini CLI, Codex,
Cursor, or any other agent) operating on **ai-circus-framework**. This is the root-level
policy; every service under `services/*/` and `ui-react/` is itself a
cookiecutter-generated project with its **own** `AGENTS.md` — read that one too when working
inside a specific service. Adherence is STRICTLY MANDATORY.

## 🚨 1. Security & System Integrity (Priority Zero)
- **Credential Protection:** NEVER log, print, or commit secrets, API keys, or Logto/MinIO/LLM
  credentials. Every `.env` (root and per-service) is gitignored except `.env.example`.
- **Multi-tenancy is not optional:** any code path that reads scenario data, model artifacts,
  or vector search results MUST be scoped by `org_id` (the Logto Organization / tenant). Never
  add a query, MinIO path, or Qdrant collection lookup that isn't tenant-scoped — see
  `libs/shared/src/ai_circus_shared/storage.py` and `entitlements.py` for the enforced pattern.
- **Entitlement checks happen at the API, not just the UI:** every backend service must call
  `platform-registry`'s entitlement check before serving a scenario request, regardless of what
  the calling UI already filtered.
- **AI Context Exclusions:** never read/analyze `.env`, `.env.*` (except `.env.example`),
  `*.pem`, `*.key`, `**/credentials*`, `**/secrets*`, or `.cache/` contents, even with
  filesystem access that bypasses `.gitignore`.

## 📚 2. Context Discovery Requirement
- Read `/home/amartinez3/.claude/plans/swirling-nibbling-cocoa.md` (the approved project plan)
  before making architectural changes — it records the reasoning behind the tenancy/storage/
  ingress/scenario-registry decisions, not just the "what".
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
