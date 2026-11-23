# Architecture details - Maps and dashboard charts

Source: docs/architecture-details.md (this repository)

`GeoBubbleMap.tsx` draws the region and watchlist maps in SVG: marks are grouped into one path per style, a pure
nearest-point search does the hit-testing, and pan and zoom only move a transform. `geoMapFrame.ts` holds the framing,
shared with `DataStoreMap.tsx`. `dashboardCharts.tsx` draws a data product's line and bar panels at the real pixel width,
and `dashboardChartLogic.ts` is their pure part (scales, series, and leaving out a day, week or month still in progress).
A panel declared `chart: pie` or `hbar` is a `BarList`. Measurements are in ADR 0014.
