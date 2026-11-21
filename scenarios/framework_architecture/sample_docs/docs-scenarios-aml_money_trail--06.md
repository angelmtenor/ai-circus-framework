# 💸 Follow the Money — transaction monitoring for a bank consortium - How it is built

Source: docs/scenarios/aml_money_trail.md (this repository)

- **Data**: `scripts/prepare_aml_money_trail_dataset.py` (sources pinned by SHA-256) writes the
  CSV and the network `aml_money_trail_network.json` (`ai_circus_shared.network_graph`, now with
  hourly periods): banks as `entity` nodes, holders as `row`/`context` nodes, one flow edge per
  payer, payee and payment format carrying USD per hour.
- **Schema**: `MoneyTrailExtra` in `libs/shared/.../scenario_schema.py` — flows, countries, tiers,
  pillars, holder-type feature, typology and case columns — validated (network, out-of-fold
  scores, real columns) like the other tabular showpieces.
- **Serving**: unchanged — etl-tabular restricts the graph to the cleaned rows, prediction serves
  `GET /graph/{slug}` and `GET /model/{slug}/out-of-fold` (probabilities only above 5,000 rows;
  the dossier's SHAP comes from a live `/predict`).
- **UI**: `ui-react/src/MoneyTrailView.tsx` (the single generic renderer; the scenario supplies
  only wording; its **Network graph** view is `NetworkExplorerView` fed with a config derived by
  `networkExplorerExtras()` — payment formats merged per pair by `mergeFlowKinds()`, hourly period labels
  and ticks, and only the rows the graph draws), `moneyTrailScene.ts` (canvas map, particles, hit-testing; the pixel ratio drops
  to 1 on slow software canvases) and `moneyTrailModel.ts` (pure functions).
