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

**Consequences.** The overlap this decision found is gone: `RegionMapView` and `RiskWatchlistView`'s maps moved
to `GeoBubbleMap`, and `DataDashboardView`'s fixed charts to `dashboardCharts.tsx` and `BarList` (see the
measurements below). Neither shrinks the bundle, since the builder still needs Plotly; those views just stop
loading it. The watchlist's Risk landscape keeps Plotly's WebGL scatter. A dashboard panel declared `pie` is
drawn as ranked bars with each share written beside it. Superset and Power BI keep their own chart engines;
portability between them is at the level of metrics and marts, not pixels.

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

**Measured: the maps (2026-11-04).** `scattergeo` against `GeoBubbleMap` (SVG, marks grouped into one path per
style, no frame loop), on the k3s cluster in headless Chromium:

| | Bank watchlist, 8,196 banks: Plotly | SVG | Regional map, 17 areas: Plotly | SVG |
|---|---|---|---|---|
| Browser CPU to open | 3.01 s | 0.45 s | 1.43 s | 0.22 s |
| Downloaded on opening the tab | 8.05 MB | 6.70 MB | 2.02 MB | 0.26 MB |
| JS heap | 54 MB | 26 MB | 64 MB | 26 MB |
| DOM nodes in the map | 8,299 | 20 | 114 | 26 |
| CPU to toggle the biggest tier | 0.38 s | 0.09 s | n/a | n/a |
| CPU at idle | 0.03% | 0.01% | 0.01% | 0.01% |
| Frames per second at idle | 0 | 0 | 0 | 0 |
| Outside hosts contacted | `cdn.plot.ly` | none | `cdn.plot.ly` | none |
| Page overflow on a phone | 0 px | 0 px | 508 px | 0 px |

Again **idle was never the problem**: the gain is opening and interacting, and that these maps no longer
need an outside CDN for their map data. The rest of the watchlist's download is its own data.
`geoAlbersUsa` cannot place points outside the 50 states, so 39 banks are off the map; the map says so,
and they stay in the ranked list.

**Measured: the dashboards (2026-11-04).** `surf_retail`'s Dashboards tab, its map and 9 chart panels, Plotly
against `dashboardCharts.tsx` and `BarList`, measured the same way:

| | Plotly | SVG |
|---|---|---|
| Browser CPU to open | 1.47 s (0.62 s in Plotly's own code) | 0.71 s |
| Downloaded on opening the tab | 1.71 MB | 0.34 MB |
| JS heap | 43 MB | 24 MB |
| DOM nodes on the page | 3,068 | 786 |
| CPU to change the period | 0.51 s | 0.30 s |

The page's idle cost, under 1% with 0 frames per second, is its 10-second poll of `/overview`, not its charts.
