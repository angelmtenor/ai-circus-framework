# 0011 — Simulators are scenario-shipped Python plugins

**Context.** A data product needs realistic synthetic sources (history backfill, live mode,
anomalies on demand). That logic is domain-specific; the services must stay generic.

**Decision.** `scenarios/<slug>/simulator/` is a Python package implementing a small protocol
(`ai_circus_shared.data_simulator.Simulator`: `backfill`, `live_tick`, named `actions`). The
generic services load it by path from the read-only scenarios mount. It produces through the
platform's own interfaces only (Kafka producer, landing bucket, operational-DB connection),
stamped with the caller's `org_id`; it never writes bronze or marts directly. Simulators are
deterministic given a seed and use only the services' existing dependencies (stdlib + numpy).

**Consequences.** Scenario code runs inside platform pods — acceptable because scenarios are
repository content with the same review as service code (never user uploads). The UI's
"Simulate" buttons come from the scenario's declared `actions`.
