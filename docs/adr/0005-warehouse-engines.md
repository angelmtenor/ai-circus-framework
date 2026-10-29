# 0005 — Marts on Postgres (core) and ClickHouse (analytics)

**Context.** The proposal used PostgreSQL for the lite profile and ClickHouse for the full one,
one dbt project for both. ClickHouse 26.4 already runs as Langfuse's backend.

**Decision.** One dbt project per data product with adapter-dispatched macros for the few
engine-specific expressions (JSON extraction, date truncation, array unnesting). Targets:
`postgres` (core tier, the default), `clickhouse` (analytics tier), `duckdb` (tests). ClickHouse
is the *existing* instance with its own database `data_platform` and user, and a raised memory
limit when the analytics tier is applied (Langfuse's settings are left untouched). When the
analytics tier is up, `data-api` reads marts from ClickHouse; otherwise from Postgres.

**Consequences.** The same metrics come from either engine; the engine is a deployment choice.
Sharing ClickHouse saves a ~300 MiB baseline but couples the data tier to Langfuse's instance;
`k3s-data-showcase` therefore keeps ClickHouse running while it scales Langfuse web/worker down.
