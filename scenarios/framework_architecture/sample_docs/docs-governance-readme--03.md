# Governance-ready by standards - Plugging in an external tool

Source: docs/governance/README.md (this repository)

Three contracts carry decisions and evidence in each direction, plus one export.

| Direction | Contract | Doc | Schema |
|---|---|---|---|
| tool → gateway (decide) | **Policy decision**: the gateway asks `{tenant_id, ai_system_id, model, call_type}`, enforces `{decision, reason_code, obligations}` | [policy.md](policy.md) | [policy-input](schemas/policy-input.json), [policy-decision](schemas/policy-decision.json) |
| tool → gateway (change) | **Invalidation webhook**: `POST /governance/policy/invalidate`, HMAC-signed — clears or replaces a cached decision (e.g. suspend an AI system) | [policy.md](policy.md#caching-and-change-notification) | – |
| platform → tool | **Events**: CloudEvents 1.0 over a log line or an HMAC-signed webhook with a retrying outbox | [events-and-audit.md](events-and-audit.md) | [envelope](schemas/cloudevent-envelope.json) + one per event |
| platform → tool | **Inventory**: CycloneDX 1.6 AI-BOM, `GET /governance/inventory` | [inventory.md](inventory.md) | the official CycloneDX schema |

Also available to a tool: the hash-chained admin audit trail (`GET /governance/audit`, `/audit/verify`) and
the trace attributes on every model call ([telemetry.md](telemetry.md)).

A typical wiring: set `POLICY_DECIDER=http` and `POLICY_HTTP_URL` to the tool's decision endpoint, give it the
invalidation secret, point `GOVERNANCE_WEBHOOK_URL` at its event receiver, and let it read the AI-BOM.
