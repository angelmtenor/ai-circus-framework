# Data Platform — an open-source, scenario-driven data platform tier - Runbook

Source: docs/data-platform/README.md (this repository)

```bash
make k3s-data-build                 # images: data-ingest, data-api, data-transform
make k3s-data-core-up               # Kafka + data-ingest + data-api + the dbt CronJob
make k3s-data-seed                  # the scenario's history (five years for surf_retail) through the real pipeline
make k3s-data-seed-lite             # one year, for laptops short of memory or disk
make k3s-data-ml                    # train the tabular_ml scenarios the data product exports
make k3s-data-transform             # a dbt build now (the CronJob runs every 3 minutes)
make k3s-data-analytics-up          # Dagster + ClickHouse marts (data-api reads ClickHouse)
make k3s-data-bi-up                 # Superset + dashboards as code
make k3s-data-governance-up         # OpenMetadata + catalogue sync
make k3s-data-showcase              # all of the above, AI-only pods scaled to 0
make k3s-data-call P=/v1/surf_retail/overview   # any data-api GET as the admin
make k3s-data-reset                 # drop every byte of one data product, then re-seed
```

Each `*-up` has a `*-down`. Open http://aiopen.localhost → *Angel's Surf Co. — Data
Platform*. Its first tab is the **Guided tour** (`ui-react/src/DataTourView.tsx`, content in
`dataTour.ts`): a live map of the whole platform that lights up with real pipeline events, and
eleven steps — sources, capture, contract gate, bronze, live tile, lake, transform, gold,
serving doors, governance, the capability map — each at three depths (*Simple* for anyone,
*Detailed* with the components, *Engineer* with alternatives, contracts, SQL and live JSON),
with live numbers from the cluster and a *try it* button that does the real thing (ring up a
sale, send a broken receipt, re-send a duplicate, view as a store manager, ask the assistant).
Press play for a hands-free demo. The other tabs: Data product · Live flow (simulate sales, bad records, legacy files, price changes)
· Dashboards · Explore (records, datasets, governed SQL, quarantine) · Quality & lineage. The
admin can *view as* any role (analyst, store manager S012, data steward) and the assistant
answers within that role. Tool UIs: http://dagster.localhost (admin basic auth),
http://superset.localhost (`admin` / `SUPERSET_ADMIN_PASSWORD`),
http://openmetadata.localhost (`admin@open-metadata.org`, OpenMetadata's default password).
