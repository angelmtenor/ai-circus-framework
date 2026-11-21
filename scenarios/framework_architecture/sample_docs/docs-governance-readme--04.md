# Governance-ready by standards - The switches

Source: docs/governance/README.md (this repository)

| Variable | Default | Effect |
|---|---|---|
| `POLICY_DECIDER` | `noop` | `static`, `opa` or `http` enables enforcement at llm-gateway |
| `POLICY_FAIL_MODE` | `open` | `closed`, or `by_risk` (closed only for high-risk systems) when the decision point is down |
| `GOVERNANCE_EVENTS_ENABLED` | `false` | emit events (`GOVERNANCE_EVENT_SINK=log\|http`, `GOVERNANCE_WEBHOOK_URL`, `GOVERNANCE_WEBHOOK_SECRET`) |
| `GOVERNANCE_AUDIT_ENABLED` | `false` | hash-chained admin audit trail |
| `GOVERNANCE_POLICY_HMAC_SECRET` | – | enables the invalidation webhook |
| `GOVERNANCE_CATALOG_ENABLED` | `false` | publish declared risk to Valkey for `POLICY_FAIL_MODE=by_risk` |
| `GOVERNANCE_INVENTORY_ENABLED` | `false` | serve the AI-BOM |
| `GOVERNANCE_RECORD_STORE` / `GOVERNANCE_RETENTION_DAYS` | off / `183` | keep non-LLM decision records, and for how long |
| `TRACE_CAPTURE_CONTENT` | `true` | `false` redacts prompts and completions from traces |
