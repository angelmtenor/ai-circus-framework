# 0014 — Charts: Plotly stays for what only it does; Superset for BI, Power BI by contract

Source: docs/adr/0014-charting-and-bi-strategy.md (this repository)

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
