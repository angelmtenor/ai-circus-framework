# 0014 — Charts: Plotly stays for what only it does; Superset for BI, Power BI by contract

**Status.** Accepted (2026-11-04). The first draft proposed replacing Plotly with Vega-Lite; measurements
and the wish to keep 3-D charts decided against it.

**Context.** ui-react draws charts four ways: hand-rolled SVG primitives (`charts.tsx`: SHAP bars,
partial dependence, evaluation), Plotly (`PlotlyChart*.tsx` + `chartBuilder.ts` — `plotly.js-dist-min` is
by far the heaviest lazy chunk), d3 modules (`d3-force`, `d3-geo`, `d3-contour`, `d3-zoom`… inside the
canvas scenes) and Superset (the data platform's bi tier, [0009](0009-bi-superset.md)). Customers ask for
Power BI-style reports, and some already use Deneb (Vega / Vega-Lite inside Power BI).

**Decision.**
- **Plotly stays** for what nothing else here does: the configurable chart builder (the Data tab and the
  Tutorial), the chat's `render_chart`, large scatter plots through WebGL, and 3-D. It stays lazy-loaded.
- **No Vega-Lite for now.** Swapping would replace one general chart library with another, drop 3-D from
  five scenarios and the chat, and bring no gain in frame rate (see *Measured*).
- **Each other job has one owner, so Plotly should not spread:** a new small fixed-purpose chart uses the
  SVG primitives in `charts.tsx`, and a new map uses `d3-geo`, not `scattergeo`. d3 modules are layout and
  geometry maths, not a charting library, and canvas scenes run on `sceneLoop.ts`.
- **Superset stays the BI tool** of the data platform (bi tier, off by default). The governed
  metrics in `scenario.yaml` (`data_product.metrics`) remain the one definition: `bi_sync`
  compiles `bi/dashboards.yaml` to Superset, and the native workspace views read the same
  metrics through data-api.
- **Power BI is reached by contract, never run in the cluster:** DirectQuery/import on the gold
  marts (Postgres or ClickHouse views, with the data product's row filters as roles).

**Consequences.** The overlap that remains is known and small: `RegionMapView` and `RiskWatchlistView` draw
`scattergeo` maps, and `DataDashboardView` draws fixed charts through Plotly. Moving them stops those views
loading Plotly but does not shrink the bundle, since the builder still needs it. Superset and Power BI keep
their own chart engines; portability between them is at the level of metrics and marts, not pixels.

**Reopen this if** customers need the same chart specs in the app and in Power BI through Deneb, the
assistant has to write full chart specs (which must be validated, not executed: Vega compiles expressions
with `new Function` unless `vega-embed` runs with `ast: true` and `vega-interpreter`), or 3-D stops being
wanted.

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

**Neither library has an idle cost**, so this was never a frame-rate question. Those problems came from
endless canvas loops, which `sceneLoop.ts` addresses. Vega-Lite's gain is the first chart a user opens:
about 1.2 MB less to download and about a second less to parse.
