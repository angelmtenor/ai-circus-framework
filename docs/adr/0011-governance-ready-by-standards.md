# 0011 — Governance-ready through open standards, not a product

**Context.** Organisations running this framework will want to govern its AI systems with
their own tooling (inventory, policy, evidence, audit). Coupling the framework to one
governance product would tie every adopter to it; building an in-house governance suite is out
of scope. The framework only needs to be *governable*: expose what an external tool needs, and
accept its decisions.

**Decision.** Integrate only through open contracts, each published as a JSON Schema under
`docs/governance/schemas/`: declared AI-system metadata (this step), a CycloneDX AI-BOM
export, OpenTelemetry GenAI trace attributes plus `aicf.*` correlation attributes, a
pluggable policy decision point enforced at `llm-gateway`, CloudEvents governance events, and
a hash-chained admin audit trail. Rules that bind every step:

- **Product-neutral**: no governance-product names in new env vars, schemas, event types or docs
  (existing infrastructure — Langfuse, MLflow, Keycloak, OpenMetadata — keeps its names).
- **Off by default**: with governance disabled, behaviour and performance are unchanged;
  a scenario without a `governance:` block keeps working and only gains its slug as `ai_system_id`.
- **Supports, never claims compliance** (EU AI Act, ISO/IEC 42001, NIST AI RMF): wording is
  "supports" / "provides evidence for". Risk categories are *declared by the owner*, never
  inferred.
- **No content, no personal data** in governance events and audit records: IDs, metrics, decisions.
- **Explicit failure semantics**: every enforcement point declares `open`/`closed` and logs every fail-open.
- **Single chokepoint**: every model call goes through `llm-gateway`.
- `llm-gateway` cannot import `ai_circus_shared` (litellm pins `fastapi`), so contracts shared
  with it are JSON Schemas with contract tests, not a shared Python import.

**Consequences.** `ScenarioSummary.governance` (and the `scenarios.governance` column) carries
the declared block with a stable `ai_system_id` that later steps key policies, traces and
events on. Delivered in steps (metadata → events + audit → policy → telemetry → AI-BOM → docs);
each is independently releasable and flagged off.
