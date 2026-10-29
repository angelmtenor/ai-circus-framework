import { useEffect, useMemo, useState } from "react";
import { geoBounds, geoConicConformal, geoContains, geoGraticule10, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { Feature, FeatureCollection } from "geojson";
import countriesUrl from "world-atlas/countries-50m.json?url";
import { changeLabel, formatMetric, type DashboardPanel, type MetricResult, type MetricSpec } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { fetchJsonOnce } from "./logisticsViz";

const W = 760;
const H = 560;
// A group's colour comes from its name, not its rank among the groups on screen, so a region
// keeps its colour for every role (a store manager sees one region). Ordered so that retail's
// regions hash to blue (East), amber (North), green (South), teal (West), purple (Online).
// A scenario can pin colours instead (places.group_colours).
const GROUP_COLOURS = ["var(--pink)", "var(--lime)", "var(--teal)", "var(--purple)", "var(--green)", "var(--blue)", "var(--amber)"];

function groupColour(group: string): string {
  let hash = 0;
  for (const ch of group) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return GROUP_COLOURS[hash % GROUP_COLOURS.length];
}

type Place = { key: string; label: string; lat: number; lon: number; group: string | null };
type Bubble = Place & { value: number | null; compare: number | null; change: number | null; x: number; y: number; r: number };

/** Ratios and averages compare by spread (min..max), additive metrics by area from zero. */
function isRatio(spec: MetricSpec): boolean {
  return spec.format === "percent" || spec.expression.includes("/");
}

/**
 * chart: map — one bubble per place of `panel.places` (e.g. every store with its coordinates,
 * from the gold `mart_stores` dataset), sized by the chosen governed metric and coloured by
 * group or by change vs the comparison period. Places without coordinates (the online shop)
 * are listed beside the map. Metric values come from data-api like every other panel, so the
 * role's row filter applies: a store manager sees one bubble.
 */
export function DataStoreMap({
  panel,
  specs,
  client,
  period,
  compare,
}: {
  panel: DashboardPanel;
  specs: Map<string, MetricSpec>;
  client: DataProductProps["client"];
  period: string;
  compare: string;
}) {
  const places = panel.places!;
  const choices = [panel.metric, ...(panel.metrics ?? [])].filter((m) => specs.has(m));
  const [metric, setMetric] = useState(panel.metric);
  const [colourBy, setColourBy] = useState<"group" | "change">("group");
  const [rows, setRows] = useState<Place[] | null>(null);
  const [result, setResult] = useState<MetricResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [world, setWorld] = useState<Feature[] | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const spec = specs.get(metric)!;

  useEffect(() => {
    fetchJsonOnce<Topology>(countriesUrl)
      .then((topology) => setWorld((feature(topology, topology.objects.countries as GeometryCollection) as FeatureCollection).features))
      .catch(() => setWorld([]));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    client
      .datasetRows(places.dataset, 500, controller.signal)
      .then((r) =>
        setRows(
          r.rows.map((row) => ({
            key: String(row[places.key]),
            label: String(row[places.label] ?? row[places.key]),
            lat: Number(row[places.lat] ?? NaN),
            lon: Number(row[places.lon] ?? NaN),
            group: places.group ? (row[places.group] as string | null) ?? null : null,
          })),
        ),
      )
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => controller.abort();
  }, [client, places]);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setError(null);
    client
      .metric(metric, { dims: [places.key], period, compare: panel.compare ? compare : undefined, limit: 500, order: "value_desc" }, controller.signal)
      .then(setResult)
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => controller.abort();
  }, [client, metric, places.key, period, compare, panel.compare]);

  const groups = useMemo(() => [...new Set((rows ?? []).map((p) => p.group).filter((g): g is string => !!g))].sort(), [rows]);
  const colourOf = (group: string | null) =>
    group ? (places.group_colours?.[group] ? `var(--${places.group_colours[group]})` : groupColour(group)) : "var(--dim)";

  const projection = useMemo(() => {
    const located = (rows ?? []).filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));
    const lons = located.length ? located.map((p) => p.lon) : [-9.4, 3.3];
    const lats = located.length ? located.map((p) => p.lat) : [36, 43.8];
    const centre = [(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
    // At least a country's span, so a role that sees one place still gets its surroundings.
    const halfLon = Math.max(6, (Math.max(...lons) - Math.min(...lons)) / 2 + 1.1);
    const halfLat = Math.max(4.2, (Math.max(...lats) - Math.min(...lats)) / 2 + 1.1);
    // Every corner and every place: a conic projection bends the frame, so two corners are not enough.
    const frame: GeoPermissibleObjects = {
      type: "MultiPoint",
      coordinates: [
        [centre[0] - halfLon, centre[1] - halfLat],
        [centre[0] + halfLon, centre[1] - halfLat],
        [centre[0] - halfLon, centre[1] + halfLat],
        [centre[0] + halfLon, centre[1] + halfLat],
        ...located.map((p) => [p.lon, p.lat]),
      ],
    };
    return geoConicConformal()
      .parallels([centre[1] - 4, centre[1] + 4])
      .rotate([-centre[0], 0])
      .fitExtent(
        [
          [16, 16],
          [W - 16, H - 16],
        ],
        frame,
      );
  }, [rows]);
  const path = useMemo(() => geoPath(projection), [projection]);
  // The countries inside the frame; those holding a place are drawn as the focus.
  const land = useMemo(() => {
    if (!world) return null;
    const located = (rows ?? []).filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));
    const [[lon0, lat0], [lon1, lat1]] = [projection.invert!([0, H])!, projection.invert!([W, 0])!];
    const visible = world.filter((f) => {
      const [[x0, y0], [x1, y1]] = geoBounds(f);
      return x1 >= lon0 - 5 && x0 <= lon1 + 5 && y1 >= lat0 - 5 && y0 <= lat1 + 5;
    });
    const focus = visible.filter((f) => located.some((p) => geoContains(f, [p.lon, p.lat])));
    return { focus, context: visible.filter((f) => !focus.includes(f)) };
  }, [world, rows, projection]);
  // Labels without what every place shares (e.g. the chain's name).
  const short = useMemo(() => {
    const labels = (rows ?? []).filter((p) => Number.isFinite(p.lat)).map((p) => p.label);
    let prefix = labels.length > 1 ? labels[0] : "";
    for (const l of labels) while (prefix && !l.startsWith(prefix)) prefix = prefix.slice(0, -1);
    prefix = prefix.slice(0, prefix.lastIndexOf(" ") + 1);
    return (label: string) => (prefix && label.startsWith(prefix) ? label.slice(prefix.length) : label);
  }, [rows]);

  const { bubbles, unplaced } = useMemo(() => {
    const byKey = new Map((result?.rows ?? []).map((r) => [String(r[places.key]), r]));
    const scored = (rows ?? []).map((p) => {
      const r = byKey.get(p.key);
      return { ...p, value: r?.value ?? null, compare: r?.compare_value ?? null, change: r?.change_pct ?? null };
    });
    const values = scored.map((p) => p.value).filter((v): v is number => v !== null);
    const max = Math.max(1e-9, ...values);
    const min = Math.min(...values);
    const ratio = isRatio(spec);
    const radius = (v: number | null) => {
      if (v === null) return 4;
      if (ratio) return 7 + 21 * (max === min ? 0.5 : (v - min) / (max - min));
      return 4 + 26 * Math.sqrt(Math.max(0, v) / max);
    };
    const placed: Bubble[] = [];
    const rest: typeof scored = [];
    for (const p of scored) {
      const xy = Number.isFinite(p.lat) && Number.isFinite(p.lon) ? projection([p.lon, p.lat]) : null;
      if (xy) placed.push({ ...p, x: xy[0], y: xy[1], r: radius(p.value) });
      else if (p.value !== null) rest.push(p);
    }
    // Big bubbles first so the small ones stay on top and hoverable.
    placed.sort((a, b) => b.r - a.r);
    return { bubbles: placed, unplaced: rest };
  }, [rows, result, places.key, projection, spec]);

  const ranked = useMemo(() => [...bubbles, ...unplaced].filter((b) => b.value !== null).sort((a, b) => (b.value ?? 0) - (a.value ?? 0)), [bubbles, unplaced]);
  const top = ranked[0]?.value ?? 0;
  const bottom = ranked[ranked.length - 1]?.value ?? 0;
  // Additive metrics: share of the leader. Ratios: position within the spread (54.0%..54.8%).
  const barWidth = (v: number | null) =>
    isRatio(spec)
      ? 12 + 88 * (top === bottom ? 1 : ((v ?? bottom) - bottom) / (top - bottom))
      : top > 0
        ? Math.max(4, (100 * (v ?? 0)) / top)
        : 0;
  const byGroup = useMemo(() => {
    if (isRatio(spec)) return [];
    const totals = new Map<string, number>();
    for (const p of ranked) totals.set(p.group ?? "—", (totals.get(p.group ?? "—") ?? 0) + (p.value ?? 0));
    return [...totals.entries()].sort((a, b) => b[1] - a[1]);
  }, [ranked, spec]);
  const groupMax = Math.max(1, ...byGroup.map(([, v]) => v));
  const focus = hover ? [...bubbles, ...unplaced].find((b) => b.key === hover) : null;
  const tone = (change: number | null) => (change === null ? "var(--dim)" : change > 2 ? "var(--green)" : change < -2 ? "var(--red)" : "var(--amber)");
  const showLabels = new Set(ranked.slice(0, 5).map((p) => p.key));

  return (
    <div className="panel-card dp-panel dp-panel--wide dp-map-panel">
      <div className="dp-panel-head">
        <h4>{panel.title}</h4>
        {result && (
          <span className="panel-hint">
            total {formatMetric(result.total, spec.format)}
            {result.compare && <span className={`dp-change dp-change--${changeLabel(result.compare.change_pct).tone}`}> · {changeLabel(result.compare.change_pct).text}</span>}
          </span>
        )}
      </div>
      <div className="dp-map-controls">
        <div className="dp-segmented dp-map-metrics" role="tablist" aria-label="Indicator">
          {choices.map((m) => (
            <button key={m} className={m === metric ? "active" : ""} onClick={() => setMetric(m)} title={specs.get(m)?.description}>
              {specs.get(m)?.label ?? m}
            </button>
          ))}
        </div>
        {places.group && panel.compare && (
          <div className="dp-segmented dp-map-colour" aria-label="Colour">
            <button className={colourBy === "change" ? "active" : ""} onClick={() => setColourBy("change")}>
              Colour: change
            </button>
            <button className={colourBy === "group" ? "active" : ""} onClick={() => setColourBy("group")}>
              Colour: {places.group}
            </button>
          </div>
        )}
      </div>
      {error && <p className="panel-hint dp-restricted">{error.includes("Role") || error.includes("only sees") ? `Not available for your role — ${error}` : error}</p>}
      <div className="dp-map-body">
        <div className="dp-map-stage">
          <svg viewBox={`0 0 ${W} ${H}`} className="dp-map-svg" role="img" aria-label={`${spec.label} by ${places.key} on a map`}>
            <defs>
              <radialGradient id="dp-map-sea" cx="50%" cy="45%" r="75%">
                <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.10" />
                <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
              </radialGradient>
              <filter id="dp-map-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            <rect width={W} height={H} fill="url(#dp-map-sea)" />
            <path d={path(geoGraticule10()) ?? ""} className="dp-map-graticule" />
            {land?.context.map((f) => <path key={String(f.id)} d={path(f) ?? ""} className="dp-map-land dp-map-land--context" />)}
            {land?.focus.map((f) => <path key={String(f.id)} d={path(f) ?? ""} className="dp-map-land" />)}
            {bubbles.map((b) => {
              const colour = colourBy === "change" && panel.compare ? tone(b.change) : colourOf(b.group);
              const active = hover === b.key;
              return (
                <g
                  key={b.key}
                  className={`dp-map-bubble${active ? " is-active" : ""}`}
                  transform={`translate(${b.x},${b.y})`}
                  onMouseEnter={() => setHover(b.key)}
                  onMouseLeave={() => setHover((h) => (h === b.key ? null : h))}
                >
                  {b.key === ranked[0]?.key && b.value !== null && (
                    <circle r={b.r} fill="none" stroke={colour} className="dp-map-pulse" />
                  )}
                  <circle r={b.r} fill={colour} fillOpacity={active ? 0.75 : 0.42} stroke={colour} strokeWidth={active ? 2.5 : 1.4} filter={active ? "url(#dp-map-glow)" : undefined} />
                  <circle r={2} fill={colour} />
                  {(showLabels.has(b.key) || active) && (
                    <text y={-b.r - 6} className="dp-map-label">
                      {short(b.label)}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          {focus && (
            <div className="dp-map-tip">
              <b>{focus.label}</b>
              <span className="panel-hint">
                {focus.key}
                {focus.group ? ` · ${focus.group}` : ""}
              </span>
              <span className="dp-map-tip-value">{formatMetric(focus.value, spec.format)}</span>
              {panel.compare && (
                <span className={`dp-change dp-change--${changeLabel(focus.change).tone}`}>
                  {changeLabel(focus.change).text} vs {formatMetric(focus.compare, spec.format)}
                </span>
              )}
            </div>
          )}
          {colourBy === "group" || !panel.compare ? (
            <div className="dp-map-legend">
              {groups.map((g) => (
                <span key={g}>
                  <i style={{ background: colourOf(g) }} /> {g}
                </span>
              ))}
            </div>
          ) : (
            <div className="dp-map-legend">
              <span><i style={{ background: "var(--green)" }} /> growing</span>
              <span><i style={{ background: "var(--amber)" }} /> flat (±2%)</span>
              <span><i style={{ background: "var(--red)" }} /> falling</span>
              <span className="panel-hint">bubble = {spec.label.toLowerCase()}</span>
            </div>
          )}
        </div>
        <div className="dp-map-side">
          <h5>Ranking</h5>
          <ol className="dp-map-rank">
            {ranked.slice(0, 10).map((p, i) => (
              <li key={p.key} className={hover === p.key ? "is-active" : ""} onMouseEnter={() => setHover(p.key)} onMouseLeave={() => setHover(null)}>
                <span className="dp-map-rank-n">{i + 1}</span>
                <span className="dp-map-rank-name" title={p.key}>
                  {short(p.label)}
                </span>
                <span className="dp-map-rank-bar">
                  <i style={{ width: `${barWidth(p.value)}%`, background: colourOf(p.group) }} />
                </span>
                <span className="dp-map-rank-value">{formatMetric(p.value, spec.format)}</span>
              </li>
            ))}
          </ol>
          {!result && !error && <div style={{ height: 180 }} aria-busy="true" />}
          {byGroup.length > 1 && (
            <>
              <h5>By {places.group ?? "group"}</h5>
              <div className="dp-map-groups">
                {byGroup.map(([g, v]) => (
                  <div key={g} className="dp-map-group">
                    <span>{g}</span>
                    <span className="dp-map-rank-bar">
                      <i style={{ width: `${(100 * v) / groupMax}%`, background: colourOf(g === "—" ? null : g) }} />
                    </span>
                    <span className="dp-map-rank-value">{formatMetric(v, spec.format)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
          {unplaced.length > 0 && (
            <p className="panel-hint">
              Not on the map: {unplaced.map((p) => `${p.label} (${formatMetric(p.value, spec.format)})`).join(", ")}
            </p>
          )}
        </div>
      </div>
      <p className="panel-hint dp-metric-def">
        <code>{spec.name}</code> = {spec.expression} over <code>{spec.dataset}</code> · places from <code>{places.dataset}</code>
      </p>
    </div>
  );
}
