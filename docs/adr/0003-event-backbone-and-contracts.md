# 0003 — Kafka backbone, JSON Schema contracts, `org_id` in every message

**Context.** Kafka 3.9 (KRaft, single broker, 512 MiB heap) already ships as the optional
data-platform profile. Options were Kafka, Redpanda (lighter, source-available) or NATS (not
Kafka-API compatible). Contracts: JSON Schema + Pydantic, or Avro/Protobuf with a registry.

**Decision.** Keep Kafka; it moves into the core tier. Topics follow
`<domain>.<source>.<entity>.v<N>` and are declared by the scenario; on the wire each tenant
has its own topic, `tenant-<org_id>.<topic>` — the convention `ai_circus_shared.events` already
enforces — and consumers subscribe by regex (`^tenant-[A-Za-z0-9_-]+\.<topic>$`), so a record's
tenant comes from where it was published, never from its payload. Contracts are JSON Schema
files under `scenarios/<slug>/contracts/`, validated by `ai_circus_shared.contracts`
(`jsonschema`, Draft 2020-12) plus scenario-declared record rules. No schema registry. Messages
carry `contract_version` and `trace_id` headers.

**Consequences.** No new broker, no extra registry pod. Breaking contract changes create a new
topic version. Contracts double as documentation and as OpenMetadata topic schemas.
