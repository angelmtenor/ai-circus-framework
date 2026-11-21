# Architecture details - Governance

Source: docs/architecture-details.md (this repository)

**Governance-ready by standards (`docs/governance/`, ADR 0011).** The framework is governable by an
external tool through open contracts only — never a product name in code, config or docs; it *supports* an
AI management system, it is not one ("supports"/"provides evidence for", never "compliant"). **Everything
is off by default** and changes nothing when off: declared `governance:` metadata + stable `ai_system_id`
per scenario (`ai_circus_shared.governance`, never auto-classified); content-free CloudEvents
(`governance_events`, log or HMAC webhook via a Valkey outbox, `prediction.recorded` decision records for
non-LLM models with enforced retention in `decision_records`); a hash-chained admin audit trail
(platform-registry is the single writer; data-platform-manager reports budget/policy changes to it);
llm-gateway's pluggable **policy decision point** (`policy_hook.py`: noop/static/OPA/HTTP, explicit
`POLICY_FAIL_MODE`, HMAC `POST /governance/policy/invalidate` on data-platform-manager); `gen_ai.*` +
`aicf.*` span attributes (`llm_gateway/tracing.py`, semconv pinned to v1.37.0); a CycloneDX 1.6 AI-BOM at
`GET /governance/inventory`. **All model calls go through llm-gateway** (a libs/shared test enforces it;
embeddings included). Rules that bite: `llm-gateway` cannot import `ai_circus_shared`, so shared contracts
are JSON Schemas under `docs/governance/schemas/` (regenerate with `scripts/export_governance_schemas.py`;
tests fail on drift); a `GOVERNANCE_*`/`POLICY_*` flag must **never** get a profile default in a
`settings.yaml` (config is `EnvConfig(**profile)`, so init values beat the environment); governance
events/audit/records carry ids, metrics and decisions — never prompts or personal data. Optional OPA:
`make k3s-governance-opa-up|down|test`.
