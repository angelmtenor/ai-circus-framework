# Governance events and admin audit trail

Both are **off by default**; with them off the framework behaves exactly as before. The
framework *supports* an AI management system — these are evidence feeds, not a compliance claim.
Design rules: [ADR 0011](../adr/0011-governance-ready-by-standards.md).

## Governance events (CloudEvents 1.0)

Structured-JSON CloudEvents with a reverse-DNS, versioned type
`<namespace>.governance.<name>.v1` (default namespace `dev.aicircus`, set with
`GOVERNANCE_EVENT_NAMESPACE`). Extension attributes `tenantid` and `aisystemid` correlate an event
with a tenant and an AI system (`ai_system_id`, see the scenario `governance:` block).

| `<name>` | When | `data` schema |
|---|---|---|
| `entitlement.changed` | a tenant is granted/revoked a scenario | [event-entitlement-changed.v1.json](schemas/event-entitlement-changed.v1.json) |
| `llm.active_model.changed` | the active LLM model is switched | [event-llm-active_model-changed.v1.json](schemas/event-llm-active_model-changed.v1.json) |
| `voice.active_settings.changed` | the STT/TTS provider is switched | [event-voice-active_settings-changed.v1.json](schemas/event-voice-active_settings-changed.v1.json) |
| `budget.changed` | a tenant's monthly gateway budget is set or removed | [event-budget-changed.v1.json](schemas/event-budget-changed.v1.json) |
| `model.trained` | a tabular model finished training and was stored | [event-model-trained.v1.json](schemas/event-model-trained.v1.json) |
| `policy.decision` | the gateway denied or degraded a call, or let it through because the decision point was down | [event-policy-decision.v1.json](schemas/event-policy-decision.v1.json) |
| `budget.exhausted` | a monthly budget (tenant, or one AI system) blocked a call | [event-budget-exhausted.v1.json](schemas/event-budget-exhausted.v1.json) |
| `ai_system.status_applied` | a suspend/resume pushed by the decision point took effect | [event-ai_system-status_applied.v1.json](schemas/event-ai_system-status_applied.v1.json) |
| `policy.changed` | the policy cache was invalidated or overwritten | [event-policy-changed.v1.json](schemas/event-policy-changed.v1.json) |

Envelope: [cloudevent-envelope.json](schemas/cloudevent-envelope.json). Events are **content-free**:
IDs, settings values, metrics and decisions — never a prompt, completion, feature value or personal
data. Payload models forbid unknown fields, and keys such as `prompt`, `content`, `email`, `token` are
rejected anywhere in a payload.

Only committed changes are announced, and an unchanged repeat (an idempotent `PUT`) emits nothing.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GOVERNANCE_EVENTS_ENABLED` | `false` | master switch (platform-registry, training, llm-gateway, data-platform-manager) |
| `GOVERNANCE_EVENT_SINK` | `log` | `log`: one JSON line per event on the `ai_circus.governance.events` logger. `http`: signed webhook |
| `GOVERNANCE_EVENT_NAMESPACE` | `dev.aicircus` | reverse-DNS prefix of the event `type` |
| `GOVERNANCE_WEBHOOK_URL` | – | receiver, when the sink is `http` |
| `GOVERNANCE_WEBHOOK_SECRET` | – | HMAC secret; environment only, never committed |

A misconfigured `http` sink (missing URL, secret or Valkey) falls back to `log` with a warning
(fail-open, logged).

On k3s, Valkey is behind a NetworkPolicy (`k8s/base/valkey.yaml`) that admits only named pods. The
`http` sink works from platform-registry, llm-gateway and data-platform-manager; the training Job is
not on that list, so its `model.trained` event stays on the `log` sink unless you extend the policy to
the Job's pods.

### The webhook (`http` sink)

The request path only appends to a Valkey stream (`governance:events:outbox`, capped at 10,000
entries); platform-registry's background deliverer POSTs each event and keeps retrying (every 30 s)
until the receiver answers 2xx, so a receiver outage only delays delivery — delivery is
**at-least-once**, so receivers should de-duplicate on the event `id`.

```
POST <GOVERNANCE_WEBHOOK_URL>
Content-Type: application/cloudevents+json
X-Governance-Timestamp: 1793355000
X-Governance-Signature: sha256=<hex>
```

`<hex>` is `HMAC-SHA256(secret, "<X-Governance-Timestamp>." + <raw request body>)`. A receiver
recomputes it over the raw bytes, compares in constant time, and rejects stale timestamps.

## Admin audit trail

`GOVERNANCE_AUDIT_ENABLED=true` (platform-registry and data-platform-manager) appends every admin
mutation — entitlements, active LLM model, voice settings, gateway budgets — to the `audit_log`
table, in the same transaction as the change. Each row stores the SHA-256 of the previous row, so
editing, inserting or deleting a row anywhere but the very end breaks every hash after it.

- `GET /governance/audit?limit=&before_id=&org_id=` — newest first ([audit-record.json](schemas/audit-record.json))
- `GET /governance/audit/verify` — recomputes the whole chain ([audit-verification.json](schemas/audit-verification.json))
- Admin bearer token required; both answer `404` while the flag is off.

Gateway budgets are applied by data-platform-manager and reported to platform-registry (the single
writer). If the registry is unreachable the budget change **still stands** and the miss is logged at
WARNING (fail-open).

Limits to know about: the admin API is one shared bearer key, so the recorded `actor` is the
credential class (`admin-api-key`), not a person; and someone with full database write access can
rewrite the whole chain — keep the `audit_hash` carried in the matching events outside the system to
anchor it.

## Regenerating the schemas

The JSON Schemas are generated from the pydantic models in `libs/shared`; a test fails if a file drifts.

```bash
cd libs/shared && uv run python ../../scripts/export_governance_schemas.py
```
