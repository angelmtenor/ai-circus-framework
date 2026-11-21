# 🏄 Angel's Surf Co. — a governed data platform (`data_product`)

Source: docs/scenarios/surf_retail.md (this repository)

> Domain: **Retail** · Kind: `data_product` (the fifth scenario kind) · Scenario: [`scenarios/surf_retail/scenario.yaml`](../../scenarios/surf_retail/scenario.yaml) · ML scenario: [`surf_ml`](../../scenarios/surf_ml/scenario.yaml)

<p align="center">
  <img src="../screenshots/surf_retail/guided-tour.webp" alt="The Guided tour: a live map of the platform — sources, capture, quality, store, transform, serve — lighting up as a sale travels through it" width="900">
</p>

**Why.** Most AI demos start from a CSV. A real company starts from tills, a web shop, an ERP and
daily files from a franchise whose old systems nobody dares to touch — and the hard part is
making all of that *trustworthy* before any model or dashboard sees it. This scenario runs a whole
data platform end to end, with open-source parts only, so the path from "a sale is rung up" to
"the assistant quotes a number" can be watched, broken on purpose and audited.

**The company is fictional.** Angel's Surf Co., its shops, brands and every sale were invented by
the framework's author, a surf enthusiast. All data comes from the scenario's own simulator;
nothing is for sale and no payment is taken.

| | |
|---|---|
| **Business** | 24 shops on the coasts of Spain, France, Italy, Greece, Cyprus and Malta, a web shop, an ERP, and five years of history |
| **Sources** | Till receipts and web orders **streamed through Kafka** (a topic per tenant); the Greek franchise's legacy tills send **daily files** reconciled against control totals; five years of history arrive as **migration files**; the ERP is captured by **change data capture** |
| **Pipeline** | contract check + business rules → quarantine · idempotent bronze (JSONB by key) → Iceberg lake → dbt silver (clean, pseudonymised) → gold marts, with tests → one governed API |
| **Serving** | `data-api` (metrics, records with their processing trail, governed SQL, lineage, live SSE), the assistant's data tools, native dashboards, Superset, OpenMetadata |
| **Access** | Tenant = Keycloak Organization on every door, then a role's row filters and column masks — *view as* a store manager and the API, SQL, dashboards and the assistant all show only that store |
