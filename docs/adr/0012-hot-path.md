# 0012 — Hot path: ingest-side minute aggregates, in every tier

**Context.** Live tiles need seconds-level latency; the curated path takes minutes. The proposal
suggested ClickHouse's Kafka engine + materialized views for the full profile.

**Decision.** A data product declares `hot_path` aggregates (sources, dimensions, measures).
`data-ingest` maintains them as per-minute upserts in Postgres (`<slug>.hot_<name>`) for the
*first* sighting of each business key (a re-send or a correction never double-counts; for a
versioned source only version 1 counts), at-least-once and labelled approximate. This is the hot
path in every tier — the analytics tier moves the *marts* to ClickHouse, not the hot path.

**Why not the ClickHouse Kafka engine.** Topics are per tenant (`tenant-<org>.<topic>`, ADR
0003) and the Kafka engine takes a fixed topic list, not a regex subscription: every new tenant
would need new DDL, and a Kafka engine consumer per tenant and source. The ingest-side
aggregate already sees every record once, validated, with its tenant.

**Consequences.** The live tile works with only the core tier and costs one extra upsert per
batch. Hot numbers converge with gold once the transform runs; the UI labels them "live,
approximate".
