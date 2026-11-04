# 0014 — One chart grammar (Vega-Lite) in the app, Superset for BI, Power BI by contract

**Status.** Proposed — the migration below has not started. It waits on one product decision: dropping
3-D charts (see *Measured*).

**Context.** ui-react draws charts four ways: hand-rolled SVG primitives (`charts.tsx`: SHAP bars,
partial dependence, evaluation), Plotly (`PlotlyChart*.tsx` + `chartBuilder.ts`, the configurable
Data dashboard — `plotly.js-dist-min` is by far the heaviest lazy chunk), d3 modules (`d3-force`,
`d3-geo`, `d3-contour`, `d3-zoom`… inside the canvas scenes) and Superset (the data platform's bi
tier, [0009](0009-bi-superset.md)). Customers ask for Power BI-style reports, and some already use
Deneb (Vega / Vega-Lite inside Power BI). The assistant's generative UI will also need to emit
charts, and an LLM-written chart must be validated, not executed.

**Decision.**
- **Declarative charts in the app use Vega-Lite** (`vega-embed`, lazy-loaded like Plotly today). A
  chart is a JSON spec: the same spec renders in ui-react, in Power BI through Deneb, and in
  notebooks through Altair; the assistant's chart tool returns a spec validated against the
  Vega-Lite JSON Schema. Plotly is phased out view by view; until it is gone no new view adopts it.
- **Hand-rolled SVG primitives stay** for small fixed-purpose charts (no dependency, theme tokens
  for free). **d3 modules stay** inside canvas scenes — they are layout/geometry maths, not a
  charting library — and those scenes run on `sceneLoop.ts`.
- **Superset stays the BI tool** of the data platform (bi tier, off by default). The governed
  metrics in `scenario.yaml` (`data_product.metrics`) remain the one definition: `bi_sync`
  compiles `bi/dashboards.yaml` to Superset, and the native workspace views read the same
  metrics through data-api.
- **Power BI is reached by contract, never run in the cluster:** DirectQuery/import on the gold
  marts (Postgres or ClickHouse views, with the data product's row filters as roles), and
  Vega-Lite specs exportable for Deneb visuals.

**Consequences.** One grammar to learn, theme and test; a smaller bundle once Plotly is gone (3-D
charts are dropped rather than ported). Vega-Lite has no 3-D and weaker very-large-scatter
performance than Plotly's WebGL traces — large point clouds aggregate (binning/density) instead.
Superset and Power BI keep their own chart engines; portability between them is at the level of
metrics and marts, not pixels.

**Measured (2026-11-04).** Plotly 3.7.0 against vega 6.4 + vega-lite 6.5 + vega-embed 7.3, in headless
Chromium with software GL, one chart on a page, no hover or zoom exercised:

| | Plotly | Vega-Lite stack |
|---|---|---|
| Download (gzip) | 1.48 MB | 0.28 MB |
| Library load and parse | 1.0–2.5 s | about 0.1 s |
| JS heap after load | 8.9 MB | 3.7 MB |
| First draw: 500-row bars | 49 ms | 78 ms |
| First draw: 5,000-row histogram | 408 ms | 70 ms |
| First draw: 5,000-point scatter | 140 ms | 115 ms |
| First draw: 20,000-point scatter | 233 ms | 264 ms |
| Heap added by a 20,000-point scatter | 3.2 MB | 11.8 MB |
| CPU at idle, chart on screen | 0.01–0.03% | 0.02–0.03% |

What this shows:
- **Neither library has an idle cost**, so this migration is not a frame-rate fix. Those problems came
  from endless canvas loops, which `sceneLoop.ts` addresses. The gain is the first chart a user opens:
  about 1.2 MB less to download and about a second less to parse.
- **Migrating means giving things up.** Five scenarios ship `scatter3d` charts (`electric_motor`,
  `steel_defects`, `cnc_surface_finish`, `mpm`, `supply_chain`) and the chat's `render_chart` offers 3-D
  too. `RegionMapView` and `RiskWatchlistView` draw `scattergeo` maps, and those would need porting.
- **Starting without finishing is the worst outcome:** while both libraries ship, a view with a chart
  can load both. So a migration should begin only once dropping 3-D is agreed.
- **LLM-written specs need the CSP-safe path:** Vega compiles expressions with `new Function` by default;
  `vega-embed`'s `ast: true` with `vega-interpreter` avoids that, and it is not in the sizes above.
