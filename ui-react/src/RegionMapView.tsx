import { useCallback, useMemo, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type ScenarioSummary, type RegionMapExtra, type MapRegion } from "./apiClient";
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
 * One shared "what-if" form (every feature except the map's `group_by` column) is
 * applied to every region at once, plus each region's own pinned real values
 * (`feature_overrides`, e.g. its population) — a single batched /predict call scores
 * all regions together, then renders as a Spain bubble map (grouped, spatially) or a
 * flat ranked table (non-grouped) of the same result, per the user's "grouped or
 * non-grouped" toggle. Plotly's built-in `scattergeo` needs no GeoJSON file and no
 * mapbox token — see PlotlyChart.tsx.
 */
export function RegionMapView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as RegionMapExtra;
  const { theme } = useTheme();
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = scenario.feature_schema ?? {};
  const sharedFeatureColumns = useMemo(() => featureColumns.filter((f) => f !== extras.group_by), [featureColumns, extras.group_by]);

  const [shared, setShared] = useState<Record_>(() => initialRecord(sharedFeatureColumns, featureSchema));
  const [results, setResults] = useState<RegionResult[] | null>(null);
  const [selected, setSelected] = useState<RegionResult | null>(null);
  const [view, setView] = useState<"map" | "table">("map");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(feature: string, value: number | string) {
    setShared((r) => ({ ...r, [feature]: value }));
  }

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const records = extras.regions.map((region) => ({ ...shared, ...region.feature_overrides, [extras.group_by]: region.key }));
      const response = await predict(config.predictionUrl, scenario.slug, records, accessToken);
      const rows: RegionResult[] = extras.regions.map((region, i) => ({
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
    description: `Batched ${scenario.title} predictions across all ${extras.regions.length} regions, currently shown on the map/table. Use this if asked which region is highest/lowest, or to compare regions.`,
    value: results ? results.map((r) => ({ region: r.region.label, prediction: r.prediction })) : null,
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
          lat: extras.regions.map((r) => r.lat),
          lon: extras.regions.map((r) => r.lon),
          text: extras.regions.map((r) => r.label),
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
          size: results.map((r) => 12 + ((r.prediction - lo) / span) * 28),
          color: values,
          colorscale: "YlOrRd",
          showscale: true,
          colorbar: { title: { text: extras.value_label }, thickness: 14 },
          line: { color: theme.cssVars["--border-strong"] ?? "#2c5a8a", width: 1 },
        },
        hovertemplate: "%{text}<extra></extra>",
      },
    ];
  }, [results, extras, theme.categoryPalette, theme.cssVars, formatValue]);

  // Computed from the scenario's own regions (padded), not hardcoded to Iberia — so
  // this same renderer frames correctly for any future region_map scenario's
  // geography. `scope: "world"` (not "europe") is required for this to actually
  // work: Plotly's continent-scoped base maps clip to a fixed template extent
  // regardless of lataxis/lonaxis, which silently drops any region outside that
  // template's frame (confirmed empirically: Spain's Canary Islands, well outside
  // mainland Europe's usual frame, disappeared entirely under scope: "europe").
  const geoBounds = useMemo(() => {
    const lats = extras.regions.map((r) => r.lat);
    const lons = extras.regions.map((r) => r.lon);
    const pad = 2.5;
    return {
      lat: [Math.min(...lats) - pad, Math.max(...lats) + pad] as [number, number],
      lon: [Math.min(...lons) - pad, Math.max(...lons) + pad] as [number, number],
    };
  }, [extras.regions]);

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

  const contributionItems = selected ? mapContributions({ ...shared, ...selected.region.feature_overrides, [extras.group_by]: selected.region.key }, selected.contributions).map((item) => ({ ...item, label: featureLabel(scenario, item.label) })) : [];

  const rankedForTable = results ? [...results].sort((a, b) => b.prediction - a.prediction) : null;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Scenario applied to every region</h3>
        <p className="panel-hint">
          Set a shared day/context below — it's applied to all {extras.regions.length} regions at once. Each region keeps its own real profile
          ({sharedFeatureColumns.length < featureColumns.length ? "population/industrial index, etc." : "fixed attributes"}), so this isolates the effect of what you change here.
        </p>
        <div className="feature-grid">
          {sharedFeatureColumns.map((feature) => (
            <FeatureInput key={feature} feature={feature} spec={featureSchema[feature]} value={shared[feature]} onChange={(v) => update(feature, v)} />
          ))}
        </div>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? "Predicting…" : `Predict all ${extras.regions.length} regions`}
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
                  <p className="panel-hint">Click a bubble to see that region's SHAP explanation.</p>
                )}
              </div>
            </div>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Region</th>
                    <th>{extras.value_label}</th>
                  </tr>
                </thead>
                <tbody>
                  {rankedForTable!.map((row, i) => (
                    <tr key={row.region.key} onClick={() => setSelected(row)} style={{ cursor: "pointer" }}>
                      <td>{i + 1}</td>
                      <td>{row.region.label}</td>
                      <td>{formatValue(row.prediction)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
