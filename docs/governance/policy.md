# Policy decision point and enforcement

llm-gateway can ask an external **policy decision point** whether a tenant's AI system may make a
model call, and enforce the answer before the call is sent. It is **off by default** (`POLICY_DECIDER`
unset or `noop`): the gateway then behaves exactly as it did before. The framework *supports* an AI
management system — enforcing a policy someone else decides is a control, not a compliance claim.
Design rules: [ADR 0011](../adr/0011-governance-ready-by-standards.md).

## The contract

Schemas: [policy-input.json](schemas/policy-input.json), [policy-decision.json](schemas/policy-decision.json).

```jsonc
// input — no request content, ever
{ "tenant_id": "org-1", "ai_system_id": "prestaciones_sociales", "model": "gpt-5.6-terra", "call_type": "completion" }

// output
{
  "decision": "allow" | "deny" | "degrade",
  "reason_code": "cost_control",            // ^[a-z0-9_]{1,96}$
  "policy_version": "2026-10-29",           // optional, recorded in events
  "obligations": {                          // all optional
    "allowed_models": ["gpt-5.6-luna"],
    "max_tokens": 400,
    "rpm": 30,                              // per tenant + AI system
    "monthly_budget": 20.0,                 // USD per month, per tenant + AI system
    "status": "active" | "suspended"
  }
}
```

How the gateway enforces it:

| Answer | Effect |
|---|---|
| `deny`, or `obligations.status: suspended` | `403`, `reason_code` in the message (`ai_system_suspended` for a suspension) |
| `degrade` | a model outside `allowed_models` is replaced by the first listed one; `max_tokens` caps completions |
| `allow` | passes through, but an unlisted model is still refused (`model_not_allowed`) |
| `rpm` exceeded | `429 rate_limited` |
| `monthly_budget` reached | `429 ai_system_budget_exhausted` (spend is tracked per AI system from the gateway's own cost figures) |

`max_tokens` never touches embedding calls. It composes with the per-tenant budget in `budget_hook`
(the policy runs first, so a refused call is never billed). **Not governed:** calls with no tenant
(`user`) or no AI system — internal admin tests, for instance. The AI system id comes from the request
`metadata` the agent services already send (`aicf_ai_system_id`, else the scenario slug), which equals
the scenario's `ai_system_id` unless its `governance:` block declares another.

## Decision points (`POLICY_DECIDER`)

| Value | Source | Settings |
|---|---|---|
| `noop` (default) | allow everything | – |
| `static` | YAML rules, first match wins, reloaded when the file changes | `POLICY_STATIC_PATH` |
| `opa` | Open Policy Agent: `POST {POLICY_OPA_URL}/v1/data/{POLICY_OPA_PACKAGE}/decision` with `{"input": …}` | `POLICY_OPA_URL`, `POLICY_OPA_PACKAGE` (default `aicircus/governance`) |
| `http` | any service speaking the contract above, verbatim | `POLICY_HTTP_URL`, `POLICY_HTTP_TOKEN` |

All use `POLICY_TIMEOUT_MS` (default 500). A misconfigured decider stops the gateway at start-up — it
never silently downgrades to allow-all. OPA answering `{}` (rule undefined) counts as an error, so a
policy gap goes through the fail mode instead of allowing.

Static rules (`infra/policy/static-policy.example.yaml`):

```yaml
version: "2026-10-29"
rules:
  - { tenant: org-1, ai_system: churn, decision: degrade, reason_code: cost_control,
      obligations: { allowed_models: [gpt-5.6-luna], max_tokens: 400, rpm: 30 } }
  - { tenant: "*", ai_system: titanic, obligations: { status: suspended } }
```

Mount it with `kubectl -n ai-circus create configmap gateway-policy --from-file=policy.yaml=…` and set
`POLICY_DECIDER=static`, `POLICY_STATIC_PATH=/policy/policy.yaml`.

### The optional OPA service

`make k3s-governance-opa-up` deploys OPA from `infra/opa/` (reference policy `aicircus.rego`, data
`data.json`, tests `aicircus_test.rego` — `make k3s-governance-opa-test`); compose:
`docker compose --profile governance up opa`. Then set `POLICY_DECIDER=opa` and
`POLICY_OPA_URL=http://opa:8181` on llm-gateway. It is not part of `k3s-all`.

## Caching and change notification

Decisions are cached in Valkey as a hash at `tenant-{org}:policy:{ai_system}` (field = model, `*` = any
model) for `POLICY_CACHE_TTL_SECONDS` (default 30). A cache hit costs one Valkey round trip.

An external tool should not be polled. It calls **`POST /governance/policy/invalidate`** on
data-platform-manager, signed with a shared secret (`GOVERNANCE_POLICY_HMAC_SECRET`; the endpoint answers
`404` until it is set):

```
POST /governance/policy/invalidate
X-Governance-Timestamp: 1793355000
X-Governance-Signature: sha256=<hex>      # HMAC-SHA256(secret, "<timestamp>." + raw body); ±300 s window

{ "tenant_id": "org-1", "ai_system_id": "churn" }                       # forget the cached decisions
{ "tenant_id": "org-1", "ai_system_id": "churn", "ttl_seconds": 600,    # …or replace them for every model
  "decision": { "decision": "deny", "reason_code": "suspended_by_governance",
                "obligations": { "status": "suspended" } } }
```

A pushed suspension takes effect on the next call. Both forms are audited (`policy.invalidated` /
`policy.overwritten`) and announce `policy.changed`; a pushed decision also raises
`ai_system.status_applied`.

## Failure semantics (`POLICY_FAIL_MODE`)

When the decision point is unreachable, times out or answers off-contract:

| Mode | Behaviour |
|---|---|
| `open` (default) | the call proceeds; logged at WARNING and raised as `policy.decision` with `fail_open: true` |
| `closed` | `503 policy_unavailable` |
| `by_risk` | `closed` for AI systems declared `high` / `prohibited`, `open` otherwise |

`by_risk` reads the declared risk from a catalogue platform-registry publishes to Valkey at
`governance:ai-system:{id}` when `GOVERNANCE_CATALOG_ENABLED=true`; an AI system missing from it counts as
`unassessed` (open). A cached decision is still honoured while the decision point is down.

## What the chokepoint covers

Every LLM and embedding call goes through llm-gateway; `libs/shared/tests/test_single_chokepoint.py` fails
if a service imports a provider SDK, names a provider host, or builds an OpenAI-style client without the
gateway's `base_url`. `EMBEDDING_PROVIDER=gemini|voyage` is routed through the gateway too
(`gemini-embedding-001`, `voyage-3.5-lite`; the keys live on the gateway). **Out of scope:** the speech
vendors agui-voice can use (Deepgram, ElevenLabs, Cartesia) — they are STT/TTS services, not LLM providers.

## Events

`policy.decision` (deny / degrade / fail-open, at most one per minute per tenant, AI system and reason),
`budget.exhausted`, `ai_system.status_applied`, `policy.changed` — see
[events-and-audit.md](events-and-audit.md). They carry IDs, decisions and numbers, never a prompt.
