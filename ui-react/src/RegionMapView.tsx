import { useCallback, useEffect, useMemo, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type ScenarioSummary, type RegionMapExtra, type RegionMapLevel, type MapRegion } from "./apiClient";
import { config } from "./config";
import { useTheme } from "./useTheme";
import { GeoBubbleMap, type GeoFrame, type GeoMark } from "./GeoBubbleMap";
import { sequentialColour, sequentialGradient } from "./geoMapFrame";
import { surfaceMode } from "./riskPalette";
import { BarList } from "./charts";
import { initialRecord, featureLabel, mapContributions, FeatureInput, type Record_ } from "./predictUtils";

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
 * The map is GeoBubbleMap: plain SVG, framed on the active level's own regions.
 */
export function RegionMapView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const extras = scenario.ui_extras as RegionMapExtra;
  const { theme } = useTheme();
  const featureColumns = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const featureSchema = useMemo(() => scenario.feature_schema ?? {}, [scenario.feature_schema]);

  const [levelKey, setLevelKey] = useState(extras.levels[0].key);
  const activeLevel: RegionMapLevel = extras.levels.find((l) => l.key === levelKey) ?? extras.levels[0];

  // Every level's group_by column is hidden from the shared what-if form — each
  // bubble at ANY level always pins its own group_by value via the request builder
  // below, so a stray "region"/"province" slider would be misleading (its value is
  // never actually used).
  const groupByColumns = useMemo(() => new Set(extras.levels.map((l) => l.group_by).filter((g): g is string => g !== null)), [extras.levels]);

  // Any feature EVERY bubble in the active level pins via feature_overrides (e.g.
  // population_thousands/industrial_index — every luznova_regional_demand region
  // and province supplies its own) is hidden from the shared form too, for the same
  // reason: dragging that slider would silently do nothing, since every bubble's
  // own real value always wins over it. Computed generically from the data (not a
  // hardcoded feature name), and per active level — a feature only some bubbles
  // override still needs the shared slider for the rest.
  const pinnedByActiveLevel = useMemo(() => {
    const [first, ...rest] = activeLevel.regions;
    if (!first) return new Set<string>();
    return new Set(Object.keys(first.feature_overrides).filter((key) => rest.every((r) => key in r.feature_overrides)));
  }, [activeLevel]);

  const sharedFeatureColumns = useMemo(
    () => featureColumns.filter((f) => !groupByColumns.has(f) && !pinnedByActiveLevel.has(f)),
    [featureColumns, groupByColumns, pinnedByActiveLevel],
  );

  const [shared, setShared] = useState<Record_>(() => initialRecord(sharedFeatureColumns, featureSchema));

  // Backfills a default for any shared feature that just became editable after a
  // level switch (not reachable today — luznova_regional_demand's two levels pin
  // the same feature set — but a future region_map scenario's levels needn't, so
  // this keeps every rendered slider backed by a real value rather than undefined).
  useEffect(() => {
    setShared((prev) => {
      const missing = sharedFeatureColumns.filter((f) => !(f in prev));
      if (missing.length === 0) return prev;
      const next = { ...prev };
      for (const f of missing) next[f] = featureSchema[f].default;
      return next;
    });
  }, [sharedFeatureColumns, featureSchema]);

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

  // Framed on the ACTIVE level's own regions, not hardcoded to Iberia, so any future
  // region_map scenario's geography frames correctly at any of its levels.
  const frame = useMemo<GeoFrame>(() => ({ kind: "places", places: activeLevel.regions }), [activeLevel]);
  const mode = surfaceMode(theme.cssVars["--bg"] ?? "#05070f");
  const borderStrong = theme.cssVars["--border-strong"] ?? "#2c5a8a";
  const accent = theme.cssVars["--accent"] ?? "#33c7ff";

  const range = useMemo(() => {
    const values = (results ?? []).map((r) => r.prediction);
    return { lo: Math.min(...values), hi: Math.max(...values) };
  }, [results]);

  const marks = useMemo<GeoMark[]>(() => {
    if (!results) return [];
    const span = range.hi - range.lo || 1;
    const colour = sequentialColour(mode);
    const scaled = results.map((r) => ({ r, t: (r.prediction - range.lo) / span }));
    // Biggest first, so a small area next to a big one stays visible and can be pointed at.
    const dots = scaled
      .sort((a, b) => b.t - a.t)
      .map(({ r, t }): GeoMark => ({
        id: r.region.key,
        lat: r.region.lat,
        lon: r.region.lon,
        r: 5 + t * 13,
        fill: colour(t),
        fillOpacity: 0.9,
        stroke: borderStrong,
        strokeWidth: 1,
      }));
    if (selected) {
      const t = (selected.prediction - range.lo) / span;
      dots.push({ id: selected.region.key, lat: selected.region.lat, lon: selected.region.lon, r: 9 + t * 13, fill: "none", stroke: accent, strokeWidth: 2.5 });
    }
    return dots;
  }, [results, range, mode, borderStrong, accent, selected]);

  const contributionItems = selected
    ? mapContributions(buildRecord(selected.region), selected.contributions).map((item) => ({
        ...item,
        label: featureLabel(scenario, item.label),
      }))
    : [];

  const rankedForTable = results ? [...results].sort((a, b) => b.prediction - a.prediction) : null;

  const detailPanel = (
    <>
      {selected ? (
        <>
          <h4>{selected.region.label}</h4>
          <p className="panel-hint">
            {extras.value_label}: <strong>{formatValue(selected.prediction)}</strong>
          </p>
          <BarList items={contributionItems} valueFormatter={(v) => v.toFixed(3)} />
        </>
      ) : (
        <p className="panel-hint">Click a {view === "map" ? "bubble" : "row"} to see that area's SHAP explanation.</p>
      )}
    </>
  );

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <div className="region-map-header">
          <h3>Scenario applied to every area</h3>
          {extras.levels.length > 1 && (
            <div className="sub-tabs">
              {extras.levels.map((level) => (
                <button key={level.key} className={level.key === levelKey ? "active" : ""} onClick={() => setLevelKey(level.key)} disabled={loading}>
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
              <GeoBubbleMap
                marks={marks}
                frame={frame}
                width={760}
                height={460}
                label={`${extras.value_label} for ${results.length} ${activeLevel.label.toLowerCase()} areas on a map. The ranked table lists the same values.`}
                tip={(id) => {
                  const row = results.find((r) => r.region.key === id);
                  return row ? { title: row.region.label, lines: [`${extras.value_label}: ${formatValue(row.prediction)}`] } : null;
                }}
                onSelect={(id) => {
                  const row = results.find((r) => r.region.key === id);
                  if (row) setSelected(row);
                }}
              >
                <div className="geo-map__legend">
                  <span>{extras.value_label}</span>
                  <span>{formatValue(range.lo)}</span>
                  <span className="geo-map__ramp" style={{ background: sequentialGradient(mode) }} />
                  <span>{formatValue(range.hi)}</span>
                  <span>Bigger and {mode === "dark" ? "brighter" : "darker"} means higher.</span>
                </div>
              </GeoBubbleMap>
              <div className="region-map-detail">{detailPanel}</div>
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
              <div className="region-map-detail region-map-detail--below-table">{detailPanel}</div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
