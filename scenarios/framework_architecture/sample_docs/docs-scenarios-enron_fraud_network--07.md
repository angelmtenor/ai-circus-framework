# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation - How it is built

Source: docs/scenarios/enron_fraud_network.md (this repository)

- `scripts/prepare_enron_fraud_network_dataset.py` — pinned, checksum-verified sources; the pay
  pickle is opcode-checked before unpickling; networkx network features; the graph file; every
  entity relation and title quote-checked against the Powers Report text, which also gives the
  page for its citation. Two runs produce byte-identical files.
- **`dataset.graph`** — a scenario can ship a network next to its rows
  (`ai_circus_shared.network_graph.NetworkGraph`: `row` / `entity` / `context` nodes, typed
  edges with per-month series). etl-tabular validates it and restricts it to the cleaned rows;
  prediction serves it, tenant-scoped, at `GET /graph/{slug}`.
- **`network_explorer`** — the generic tab (`NetworkExplorerView.tsx`, canvas renderer
  `networkScene.ts` on d3-force + d3-zoom in a lazy chunk, pure model `networkModel.ts`); all its
  wording comes from the YAML, so an AML or procurement network needs no UI code. It requires
  `model.out_of_fold_scores`, because it reveals real outcomes next to the scores.
- **Palettes** validated with the dataviz validator: the shared ordinal red ramp for tiers
  (`riskPalette.ts`, also used by the Watchlist), violet entities, three community hues.
- **Performance**: dirty-flag rendering, sprites for glows and particles, a cached background and
  pattern grid, frame pacing and a one-time drop to 1× pixel density on machines that cannot
  keep up (no GPU).
