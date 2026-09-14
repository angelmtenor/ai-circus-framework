import { useCallback, useEffect, useMemo, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type ScenarioSummary, type RegionMapExtra, type RegionMapLevel, type MapRegion } from "./apiClient";
import { config } from "./config";
import { useTheme } from "./useTheme";
import { PlotlyChart } from "./PlotlyChart";
import { BarList } from "./charts";
import { initialRecord, featureLabel, mapContributions, FeatureInput, type Record_ } from "./predictUtils";
import type { PlotlyDatum, PlotlyLayout } from "./plotly";

type RegionResult = { region: MapRegion; prediction: number; contributions: Record<string, number> };

/**
 * Generic 5th workspace tab for any `tabular_ml` scenario that sets
 * `ui_extras: {kind: region_map, ...}` (see libs/shared/scenario_schema.py) — not
 * scenario-specific: `luznova_regional_demand` is the first user, but any future
 * region-map scenario gets this same renderer for free.
 *
 * `levels` lets the scenario offer more than one aggregation granularity (e.g.
 * region vs. province) against the SAME trained model — a pill selector switches
 * which level's `group_by`/`regions` drives the map, all still against real batched
 * predictions and real per-bubble SHAP. One shared "what-if" form (every feature
 * except any level's group_by column) is applied to every bubble at once, plus each
 * bubble's own pinned real values (`feature_overrides`) — a single batched /predict
 * call scores the active level's bubbles together, then renders as a Spain bubble
 * map (grouped, spatially) or a flat ranked table (non-grouped) of the same result.
 * Plotly's built-in `scattergeo` needs no GeoJSON file and no mapbox token — see
 * PlotlyChart.tsx.
 */
export function RegionMapView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as RegionMapExtra;
  const { theme } = useTheme();
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = scenario.feature_schema ?? {};

  const [levelKey, setLevelKey] = useState(extras.levels[0].key);
  const activeLevel: RegionMapLevel = extras.levels.find((l) => l.key === levelKey) ?? extras.levels[0];

  // Every level's group_by column is hidden from the shared what-if form — each
  // bubble at ANY level always pins its own group_by value via the request builder
  // below, so a stray "region"/"province" slider would be misleading (its value is
  // never actually used).
  const groupByColumns = useMemo(() => new Set(extras.levels.map((l) => l.group_by).filter((g): g is string => g !== null)), [extras.levels]);
  const sharedFeatureColumns = useMemo(() => featureColumns.filter((f) => !groupByColumns.has(f)), [featureColumns, groupByColumns]);

  const [shared, setShared] = useState<Record_>(() => initialRecord(sharedFeatureColumns, featureSchema));

  // A level with no group_by (e.g. a coarser level reusing a finer level's real
  // trained feature — see luznova_regional_demand's "region" level, which relies
  // entirely on feature_overrides rather than injecting its own key into a feature
  // that isn't actually trained) leaves every bubble's request built from
  // feature_overrides alone.
  const buildRecord = useCallback(
    (region: MapRegion) => (activeLevel.group_by ? { ...shared, ...region.feature_overrides, [activeLevel.group_by]: region.key } : { ...shared, ...region.feature_overrides }),
    [shared, activeLevel.group_by],
  );
  const [results, setResults] = useState<RegionResult[] | null>(null);
  const [selected, setSelected] = useState<RegionResult | null>(null);
  const [view, setView] = useState<"map" | "table">("map");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Switching aggregation level invalidates the last batch — its bubbles no longer
  // match the level now on screen.
  useEffect(() => {
    setResults(null);
    setSelected(null);
  }, [levelKey]);

  function update(feature: string, value: number | string) {
    setShared((r) => ({ ...r, [feature]: value }));
  }

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const records = activeLevel.regions.map(buildRecord);
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
      const rows: RegionResult[] = activeLevel.regions.map((region, i) => ({
        region,
        prediction: response.predictions[i].prediction,
        contributions: response.predictions[i].contributions,
      }));
      setResults(rows);
      setSelected(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  // Live "what's on the map right now" context for the chat agent — same pattern as
  // MlPredictionsView/ExploreModelView's useCopilotReadable calls.
  useCopilotReadable({
    description: `Batched ${scenario.title} predictions across all ${activeLevel.regions.length} ${activeLevel.label.toLowerCase()} areas, currently shown on the map/table. Use this if asked which area is highest/lowest, or to compare areas.`,
    value: results ? results.map((r) => ({ area: r.region.label, prediction: r.prediction })) : null,
  });

  const isRegression = scenario.task_type === "regression";
  const formatValue = useCallback(
    (v: number) => (isRegression ? `${v.toFixed(1)} ${scenario.target_units ?? ""}`.trim() : `${(v * 100).toFixed(1)}%`),
    [isRegression, scenario.target_units],
  );

  const mapData: PlotlyDatum[] = useMemo(() => {
    if (!results) {
      return [
        {
          type: "scattergeo",
          lat: activeLevel.regions.map((r) => r.lat),
          lon: activeLevel.regions.map((r) => r.lon),
          text: activeLevel.regions.map((r) => r.label),
          mode: "markers",
          marker: { size: 13, color: theme.categoryPalette[0], opacity: 0.75 },
          hovertemplate: "%{text}<extra></extra>",
        },
      ];
    }
    const values = results.map((r) => r.prediction);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const span = hi - lo || 1;
    return [
      {
        type: "scattergeo",
        lat: results.map((r) => r.region.lat),
        lon: results.map((r) => r.region.lon),
        text: results.map((r) => `${r.region.label}<br>${extras.value_label}: ${formatValue(r.prediction)}`),
        mode: "markers",
        marker: {
          size: results.map((r) => 9 + ((r.prediction - lo) / span) * 26),
          color: values,
          colorscale: "YlOrRd",
          showscale: true,
          colorbar: { title: { text: extras.value_label }, thickness: 14 },
          line: { color: theme.cssVars["--border-strong"] ?? "#2c5a8a", width: 1 },
        },
        hovertemplate: "%{text}<extra></extra>",
      },
    ];
  }, [results, activeLevel, extras.value_label, theme.categoryPalette, theme.cssVars, formatValue]);

  // Computed from the ACTIVE level's own regions (padded), not hardcoded to Iberia
  // — so this same renderer frames correctly for any future region_map scenario's
  // geography, at any of its levels. `scope: "world"` (not "europe") is required
  // for this to actually work: Plotly's continent-scoped base maps clip to a fixed
  // template extent regardless of lataxis/lonaxis, which silently drops any region
  // outside that template's frame (confirmed empirically: Spain's Canary Islands,
  // well outside mainland Europe's usual frame, disappeared entirely under
  // scope: "europe").
  const geoBounds = useMemo(() => {
    const lats = activeLevel.regions.map((r) => r.lat);
    const lons = activeLevel.regions.map((r) => r.lon);
    const pad = 2.5;
    return {
      lat: [Math.min(...lats) - pad, Math.max(...lats) + pad] as [number, number],
      lon: [Math.min(...lons) - pad, Math.max(...lons) + pad] as [number, number],
    };
  }, [activeLevel]);

  const geoLayout: PlotlyLayout = {
    paper_bgcolor: "rgba(0,0,0,0)",
    geo: {
      scope: "world",
      resolution: 50,
      lataxis: { range: geoBounds.lat },
      lonaxis: { range: geoBounds.lon },
      showland: true,
      landcolor: theme.cssVars["--panel-2"] ?? "#101b36",
      showocean: true,
      oceancolor: theme.cssVars["--bg"] ?? "#05070f",
      showlakes: false,
      showcountries: true,
      countrycolor: theme.cssVars["--border-strong"] ?? "#2c5a8a",
      showcoastlines: true,
      coastlinecolor: theme.cssVars["--border-strong"] ?? "#2c5a8a",
      bgcolor: "rgba(0,0,0,0)",
      framecolor: theme.cssVars["--border"] ?? "#1c3a5e",
    },
    margin: { t: 10, r: 10, b: 10, l: 10 },
  };

  const contributionItems = selected
    ? mapContributions(buildRecord(selected.region), selected.contributions).map((item) => ({
        ...item,
        label: featureLabel(scenario, item.label),
      }))
    : [];

  const rankedForTable = results ? [...results].sort((a, b) => b.prediction - a.prediction) : null;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="region-map-header">
          <h3>Scenario applied to every area</h3>
          {extras.levels.length > 1 && (
            <div className="sub-tabs">
              {extras.levels.map((level) => (
                <button key={level.key} className={level.key === levelKey ? "active" : ""} onClick={() => setLevelKey(level.key)}>
                  {level.label} ({level.regions.length})
                </button>
              ))}
            </div>
          )}
        </div>
        <p className="panel-hint">
          Set a shared day/context below — it's applied to all {activeLevel.regions.length} {activeLevel.label.toLowerCase()} areas at once. Each area keeps
          its own real profile (population/industrial index, etc.), so this isolates the effect of what you change here.
        </p>
        <div className="feature-grid">
          {sharedFeatureColumns.map((feature) => (
            <FeatureInput key={feature} feature={feature} spec={featureSchema[feature]} value={shared[feature]} onChange={(v) => update(feature, v)} />
          ))}
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? "Predicting…" : `Predict all ${activeLevel.regions.length} ${activeLevel.label.toLowerCase()} areas`}
        </button>
        {error && <p className="error">{error}</p>}
      </div>

      {results && (
        <div className="panel-card region-map-panel">
          <div className="region-map-header">
            <h3>{extras.value_label}</h3>
            <div className="sub-tabs">
              <button className={view === "map" ? "active" : ""} onClick={() => setView("map")}>
                Map (grouped)
              </button>
              <button className={view === "table" ? "active" : ""} onClick={() => setView("table")}>
                Table (ranked)
              </button>
            </div>
          </div>

          {view === "map" ? (
            <div className="region-map-body">
              <PlotlyChart
                data={mapData}
                layout={geoLayout}
                height={460}
                onPointClick={(pointIndex) => {
                  const row = results[pointIndex];
                  if (row) setSelected(row);
                }}
              />
              <div className="region-map-detail">
                {selected ? (
                  <>
                    <h4>{selected.region.label}</h4>
                    <p className="panel-hint">
                      {extras.value_label}: <strong>{formatValue(selected.prediction)}</strong>
                    </p>
                    <BarList items={contributionItems} valueFormatter={(v) => v.toFixed(3)} />
                  </>
                ) : (
                  <p className="panel-hint">Click a bubble to see that area's SHAP explanation.</p>
                )}
              </div>
            </div>
          ) : (
            <>
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>{activeLevel.label}</th>
                      <th>{extras.value_label}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rankedForTable!.map((row, i) => (
                      <tr key={row.region.key} onClick={() => setSelected(row)} className={selected?.region.key === row.region.key ? "row-selected" : ""}>
                        <td>{i + 1}</td>
                        <td>{row.region.label}</td>
                        <td>{formatValue(row.prediction)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="region-map-detail region-map-detail--below-table">
                {selected ? (
                  <>
                    <h4>{selected.region.label}</h4>
                    <p className="panel-hint">
                      {extras.value_label}: <strong>{formatValue(selected.prediction)}</strong>
                    </p>
                    <BarList items={contributionItems} valueFormatter={(v) => v.toFixed(3)} />
                  </>
                ) : (
                  <p className="panel-hint">Click a row to see that area's SHAP explanation.</p>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
