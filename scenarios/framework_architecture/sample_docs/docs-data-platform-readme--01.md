# Data Platform — an open-source, scenario-driven data platform tier

Source: docs/data-platform/README.md (this repository)

The Data Platform turns the framework from "AI on top of datasets" into "AI on top of a
governed data platform": events and files are captured, landed raw (bronze), transformed as
code into conformed tables (silver) and marts (gold), served through one governed API, the
assistant, BI dashboards and a metadata catalogue — all open source, all on the same k3s
cluster, all driven by a scenario of the new kind **`data_product`**.

It is a *platform capability*, not a retail feature: the services under `services/data-*`
contain no domain words. Everything specific to a data product — its contracts, simulator,
dbt project, metrics, access rules, dashboards — lives under `scenarios/<slug>/`.
`surf_retail` (Angel's Surf Co., a fictional chain of surf, SUP and kite shops invented by the author, a surf enthusiast) is the first data product: 24 shops, a web shop, an ERP and five years of history, with a storefront app, a control room (Surf HQ) and two models trained on its gold feature marts.

Design source: the "Retail Omnichannel Data Platform" proposal (v0.1, October 2026). The
decisions below adapt it to this repository; each one is an ADR under [`docs/adr/`](../adr/).
