# 0004 — Bronze: Postgres JSONB record store + Iceberg archive

**Context.** Raw data must be kept once, as received, replayable, idempotent by business key, and
readable without the platform. The repo already runs Postgres (with logical replication) and a
PyIceberg SQL catalog on SeaweedFS.

**Decision.** `data-ingest` upserts every valid record into Postgres database `data_platform`,
schema `<slug>`, table `bronze_<source>` (`org_id`, business key, `payload jsonb`,
`payload_hash`, `received_at`, `source_kind`, `contract_version`), primary key
`(org_id, business key[, version])`. Same key + same hash is a no-op; same key + new hash is a
counted correction. Rejected records go to `<slug>.quarantine`. Valid records are also appended
in micro-batches to an Iceberg table `<slug>_bronze.<source>` (catalog in Postgres, data in
bucket `ai-circus-data`), which is the reprocessing and openness archive.

**Consequences.** Replays never change counts. The lake is readable from DuckDB on a laptop.
Iceberg writes are best-effort (an S3 hiccup never drops a bronze row; the archiver catches up
from Postgres). Postgres gets a bigger memory limit in the core tier.
