# Telemetry: trace attributes, decision records, retention

What the framework records about each AI call, in open formats an external governance tool can read.
Everything here is **content-free** (ids, names, counts, decisions) except where noted. The framework
*supports* record-keeping obligations (EU AI Act Art. 12, 19, 26(6); ISO/IEC 42001 clause 9.1) — it does not
by itself make a deployment compliant. Design rules: [ADR 0011](../adr/0011-governance-ready-by-standards.md).

## 1. LLM calls: trace spans

Every model call passes llm-gateway, which exports one OpenTelemetry span per call through LiteLLM's
`langfuse_otel` callback (when Langfuse is configured). llm-gateway adds two families of attributes to
that same span (`llm_gateway/tracing.py`):

### `gen_ai.*` — OpenTelemetry GenAI semantic conventions, pinned to **v1.37.0**

| Attribute | Value |
|---|---|
| `gen_ai.operation.name` | `chat`, `text_completion` or `embeddings` |
| `gen_ai.provider.name` (and legacy `gen_ai.system`) | the routed provider, e.g. `groq`, `openai`, `gemini` |
| `gen_ai.request.model` | the model the call was sent to (after a policy `degrade`, the substituted one) |
| `gen_ai.response.model`, `gen_ai.response.id` | as returned by the provider |
| `gen_ai.response.finish_reasons` | e.g. `["stop"]` |
| `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | token counts |

These conventions are still marked *Development* upstream, so the version is pinned here and in
`tracing.SEMCONV_VERSION` instead of tracked; a bump is a deliberate change with a test update.
`gen_ai.system` is kept next to `gen_ai.provider.name` for consumers that predate v1.37.

### `aicf.*` — the framework's correlation namespace

| Attribute | Meaning | Present |
|---|---|---|
| `aicf.tenant.id` | the tenant (Keycloak Organization) | always for agent calls |
| `aicf.ai_system.id` | the scenario's stable `ai_system_id` (its declared id, else its slug) | always for agent calls |
| `aicf.scenario.slug` | the scenario | always for agent calls |
| `aicf.policy.decision` | `allow`, `degrade` (or `allow` after a fail-open) | when a policy decision point is active |
| `aicf.policy.version` | the decision point's `policy_version` | when it reports one |

They come from the request `metadata` the agent services send via `langfuse_request_metadata()` and from
the policy hook; with the policy off the two `aicf.policy.*` attributes are simply absent. A denied call
makes no span.

The existing Langfuse attributes (`langfuse.*`, `llm.*`, `user.id`) are untouched. A test
(`services/llm-gateway/tests/test_tracing.py`) drives LiteLLM's real logging pipeline with a mock model and
asserts all of the above.

### Prompt and completion content

**Prompts and completions are part of the Langfuse span today** (`langfuse.observation.input/output`) —
that is what the GenAI monitor shows, and it only exists when Langfuse is deployed. The default is
unchanged. To keep content out of traces and logs, set `TRACE_CAPTURE_CONTENT=false` on llm-gateway:
LiteLLM then replaces every message with `redacted-by-litellm` while tenant, model, token counts and the
attributes above stay. Governance events and audit records never carry content either way.

## 2. Non-LLM predictions: decision records

Record-keeping applies to ML models too. Every scoring request served by `prediction` (tabular) and
`dl-inference` (deep learning) leaves one **decision record** — the CloudEvent `prediction.recorded`
([schema](schemas/event-prediction-recorded.v1.json)):

| Field | Meaning |
|---|---|
| `scenario_slug`, `ai_system_id`, `org_id` | what was asked, of which AI system, for which tenant |
| `model_name`, `model_version`, `model_variant` | the model, a content-derived id of its stored artifacts (changes exactly when the model does), `primary` or `challenger` |
| `prediction_count`, `outcomes` | the unit is the **request**: a bulk request is one record with a histogram (decision-policy decisions, gate results, or `scored`) |
| `score_min`, `score_max` | the range of the scores |
| `explanation_available` | whether per-feature explanations were produced |
| `trace_id` | the request's W3C `traceparent` trace id when the caller sent one, else a generated id |

**Never** the input features, free text, images, explanations or any individual's identity. Records are
written after the response, off the request path, and a failure is logged without ever failing the
prediction.

Where they go (both off by default):

- `GOVERNANCE_EVENTS_ENABLED=true` — as a governance event to the configured sink
  ([events-and-audit.md](events-and-audit.md)).
- `GOVERNANCE_RECORD_STORE=objectstore` — also kept in the tenant's own bucket as one small JSON object per
  request at `governance/decision-records/<YYYY-MM-DD>/<trace_id>-<n>.json`.

## 3. Retention

| Data | Retention |
|---|---|
| Stored decision records | `GOVERNANCE_RETENTION_DAYS` (default **183**, about six months — the floor Art. 19 / 26(6) set for logs a provider or deployer keeps). Enforced by the framework: day folders older than the window are deleted, at most once a day per tenant and scenario per process |
| Admin audit trail | never pruned (deleting rows would break the hash chain) |
| Governance events | the sink's — `log` lives as long as your log pipeline keeps it; the `http` webhook receiver owns what it stores. Keep them at least as long as you keep the records they describe |
| Traces | **Langfuse's, not the framework's.** Set the Langfuse project's data retention to at least the same window; this repo does not configure it |

Lowering `GOVERNANCE_RETENTION_DAYS` deletes records on the next daily pass — it is destructive, so change
it deliberately. A value below 1 or a non-number falls back to the default.

## 4. Checking it works

```bash
# with GOVERNANCE_EVENTS_ENABLED=true on prediction:
kubectl -n ai-circus logs deploy/prediction | grep prediction.recorded
```
