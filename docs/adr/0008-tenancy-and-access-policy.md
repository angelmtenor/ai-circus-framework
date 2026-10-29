# 0008 — Tenancy by `org_id`, plus scenario-declared row filters and column masks

**Context.** The proposal had no tenancy at all; this repo requires every data path to be
scoped by `org_id` and entitlement-checked in the backend.

**Decision.** `org_id` is a column of every bronze, silver, gold and hot table and the first
predicate of every query `data-api` builds; it is never a client parameter — it comes from
`resolve_caller_identity`. On top of tenancy, a data product declares `access.roles` (Keycloak
realm roles) mapping to a row filter (e.g. `store_id in claims.stores`) and column masks
(`hidden`, `pseudonymised`). `ai_circus_shared.data_policy` turns roles + claims into SQL
predicates and masks; callers with no data role get the scenario's `default_role`. Raw
identifiers are pseudonymised in silver with HMAC-SHA256 (key from a k8s Secret). A tenant with no
data of its own reads the fallback org's data product (`DATA_FALLBACK_ORG_ID`, same rule as
shared models), and the simulator writes into the caller's org.

**Consequences.** API, assistant and MCP share one policy implementation. SQL users for BI get
the same filters via Superset row-level security (bi tier).
