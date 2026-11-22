# Architecture details - Networks, out-of-fold scores, money trail

Source: docs/architecture-details.md (this repository)

A tabular scenario may ship a network next to its rows (`dataset.graph`, contract
`ai_circus_shared.network_graph.NetworkGraph`: `row`/`entity`/`context` nodes, typed edges with
per-period `series`): etl-tabular validates it and restricts it to the cleaned rows
(`processed/graph.json`), prediction serves it at `GET /graph/{slug}` (same entitlement,
fallback-org and TTL cache as the dataset sample) and the `network_explorer` tab draws it
(canvas + d3-force, `networkScene.ts`; `NetworkTab.tsx` adds a person-centred *Investigate* workspace —
ego stage, routes to targets, live-scored hypothetical individuals via `ui_extras.tie_features`). Graph position reaches the model only as ordinary
numeric features computed offline. `model.out_of_fold_scores` (classification only)
makes training cross-fit every row's probability + SHAP with the selected model — served at
`GET /model/{slug}/out-of-fold` (per-row SHAP up to 5,000 rows, probabilities only above) — because
the deployed model is refit on every row and its own scores of them are optimistic; `network_explorer`,
`shipment_globe` and `money_trail` require it (they reveal real outcomes). `money_trail`
(`aml_money_trail`, IBM AMLworld) reads a graph whose entities are banks, holders are
rows/context and flow edges carry an *hourly* `series` (`YYYY-MM-DDTHH` periods): a world map
replaying the payments hour by hour, with a per-typology reveal — plus a second, *Network graph* view
that feeds the same network to `network_explorer`'s renderer (`networkExplorerExtras()`).
