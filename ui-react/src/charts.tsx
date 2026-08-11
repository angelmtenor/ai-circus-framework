/**
 * Hand-rolled, dependency-free SVG chart primitives for fixed-purpose analytical
 * views (SHAP bars, partial dependence, held-out evaluation — see ExploreModelView.tsx
 * and MlPredictionsView.tsx). The flexible, user-configurable "Data" dashboard instead
 * uses Plotly (see PlotlyChart.tsx/chartBuilder.ts) for its broader chart-type/3D needs.
 *
 * Colors reference the active theme's CSS custom properties (see themes/) rather than
 * literal hex — these are inline JSX SVGs, so `fill="var(--blue)"` resolves live, and
 * every chart re-themes for free alongside the rest of the app.
 */

export const CHART_COLORS = {
  bg: "transparent",
  panel: "var(--panel)",
  border: "var(--border)",
  text: "var(--text)",
  dim: "var(--dim)",
  accent: "var(--accent)",
  green: "var(--green)",
  blue: "var(--blue)",
  amber: "var(--amber)",
  red: "var(--red)",
  purple: "var(--purple)",
};

export function StatTile({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="kpi-tile">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={color ? { color } : undefined}>
        {value}
      </div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function BarList({
  items,
  signed = true,
  positiveColor = CHART_COLORS.red,
  negativeColor = CHART_COLORS.green,
  neutralColor = CHART_COLORS.accent,
  valueFormatter = (v: number) => v.toFixed(3),
}: {
  items: { label: string; value: number }[];
  signed?: boolean;
  positiveColor?: string;
  negativeColor?: string;
  neutralColor?: string;
  valueFormatter?: (v: number) => string;
}) {
  const sorted = [...items].sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  const max = Math.max(1e-9, ...sorted.map((i) => Math.abs(i.value)));
  return (
    <div className="bar-list">
      {sorted.map((item) => {
        const pct = (Math.abs(item.value) / max) * 100;
        const color = !signed ? neutralColor : item.value >= 0 ? positiveColor : negativeColor;
        return (
          <div className="bar-row" key={item.label}>
            <div className="bar-row-label" title={item.label}>
              {item.label}
            </div>
            <div className="bar-row-track">
              <div className="bar-row-fill" style={{ width: `${pct}%`, background: color }} />
            </div>
            <div className="bar-row-value">{valueFormatter(item.value)}</div>
          </div>
        );
      })}
    </div>
  );
}

function niceTicks(lo: number, hi: number, n = 5): number[] {
  const step = (hi - lo) / (n - 1) || 1;
  return Array.from({ length: n }, (_, i) => lo + i * step);
}

export const CATEGORY_PALETTE = [
  CHART_COLORS.blue,
  CHART_COLORS.purple,
  CHART_COLORS.amber,
  CHART_COLORS.green,
  CHART_COLORS.red,
  "var(--teal)",
  "var(--pink)",
  "var(--lime)",
];

export function colorScale(categories: string[]): Map<string, string> {
  const scale = new Map<string, string>();
  categories.forEach((c, i) => scale.set(c, CATEGORY_PALETTE[i % CATEGORY_PALETTE.length]));
  return scale;
}

export function ScatterPlot({
  points,
  xLabel,
  yLabel,
  refLine = true,
  sharedDomain = true,
  width = 480,
  height = 260,
  color = CHART_COLORS.blue,
  legend,
}: {
  points: { x: number; y: number; lower?: number; upper?: number; color?: string }[];
  xLabel: string;
  yLabel: string;
  refLine?: boolean;
  // true (default) puts x and y on the same scale — correct for predicted-vs-actual
  // (same units, y=x reference line makes sense); set false for two unrelated
  // features (e.g. plotting the data), where each axis needs its own domain.
  sharedDomain?: boolean;
  width?: number;
  height?: number;
  color?: string;
  legend?: { label: string; color: string }[];
}) {
  const pad = 38;
  const xs = points.map((p) => p.x);
  const ys = points.flatMap((p) => [p.y, p.lower ?? p.y, p.upper ?? p.y]);
  const allValues = [...xs, ...ys];
  const xLo = sharedDomain ? Math.min(...allValues) : Math.min(...xs);
  const xHi = sharedDomain ? Math.max(...allValues) : Math.max(...xs);
  const yLo = sharedDomain ? Math.min(...allValues) : Math.min(...ys);
  const yHi = sharedDomain ? Math.max(...allValues) : Math.max(...ys);
  const xSpan = xHi - xLo || 1;
  const ySpan = yHi - yLo || 1;
  const sx = (v: number) => pad + ((v - xLo) / xSpan) * (width - pad * 1.4);
  const sy = (v: number) => height - pad - ((v - yLo) / ySpan) * (height - pad * 1.4);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg" role="img" aria-label={`${yLabel} vs ${xLabel}`}>
      {niceTicks(yLo, yHi).map((v, i) => (
        <g key={i}>
          <line x1={sx(xLo)} x2={sx(xHi)} y1={sy(v)} y2={sy(v)} stroke={CHART_COLORS.border} strokeWidth={1} />
          <text x={sx(xLo) - 6} y={sy(v)} fontSize={9} fill={CHART_COLORS.dim} textAnchor="end" dominantBaseline="middle">
            {v.toFixed(v % 1 === 0 ? 0 : 1)}
          </text>
        </g>
      ))}
      {!sharedDomain &&
        niceTicks(xLo, xHi).map((v, i) => (
          <text key={i} x={sx(v)} y={height - pad + 12} fontSize={9} fill={CHART_COLORS.dim} textAnchor="middle">
            {v.toFixed(v % 1 === 0 ? 0 : 1)}
          </text>
        ))}
      {refLine && (
        <line x1={sx(xLo)} y1={sy(xLo)} x2={sx(xHi)} y2={sy(xHi)} stroke={CHART_COLORS.dim} strokeDasharray="4 4" strokeWidth={1} />
      )}
      {points.map((p, i) => (
        <g key={i}>
          {p.lower !== undefined && p.upper !== undefined && (
            <line
              x1={sx(p.x)}
              x2={sx(p.x)}
              y1={sy(p.lower)}
              y2={sy(p.upper)}
              stroke={p.color ?? color}
              strokeOpacity={0.25}
              strokeWidth={2}
            />
          )}
          <circle cx={sx(p.x)} cy={sy(p.y)} r={2.6} fill={p.color ?? color} opacity={0.8} />
        </g>
      ))}
      <text x={width / 2} y={height - 4} fontSize={10} fill={CHART_COLORS.dim} textAnchor="middle">
        {xLabel}
      </text>
      <text x={11} y={height / 2} fontSize={10} fill={CHART_COLORS.dim} textAnchor="middle" transform={`rotate(-90 11 ${height / 2})`}>
        {yLabel}
      </text>
      {legend && (
        <g>
          {legend.map((item, i) => (
            <g key={item.label} transform={`translate(${width - pad * 3.4}, ${pad * 0.3 + i * 12})`}>
              <circle cx={0} cy={0} r={3} fill={item.color} />
              <text x={6} y={0} fontSize={9} fill={CHART_COLORS.dim} dominantBaseline="middle">
                {item.label}
              </text>
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}

export function Histogram({
  values,
  bins = 20,
  color = CHART_COLORS.blue,
  width = 480,
  height = 180,
  xLabel,
  zeroLine = false,
}: {
  values: number[];
  bins?: number;
  color?: string;
  width?: number;
  height?: number;
  xLabel?: string;
  zeroLine?: boolean;
}) {
  const pad = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const binWidth = (max - min || 1) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.max(0, Math.floor((v - min) / binWidth)));
    counts[idx]++;
  }
  const maxCount = Math.max(...counts, 1);
  const plotW = width - pad * 1.4;
  const barW = plotW / bins;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg">
      {counts.map((c, i) => {
        const h = (c / maxCount) * (height - pad * 1.6);
        return (
          <rect
            key={i}
            x={pad + i * barW}
            y={height - pad - h}
            width={Math.max(barW - 1, 1)}
            height={h}
            fill={color}
            opacity={0.85}
          />
        );
      })}
      <line x1={pad} x2={width - pad * 0.4} y1={height - pad} y2={height - pad} stroke={CHART_COLORS.border} />
      {zeroLine && min < 0 && max > 0 && (
        <line
          x1={pad + ((0 - min) / (max - min)) * plotW}
          x2={pad + ((0 - min) / (max - min)) * plotW}
          y1={pad * 0.4}
          y2={height - pad}
          stroke={CHART_COLORS.red}
          strokeDasharray="3 3"
        />
      )}
      {xLabel && (
        <text x={width / 2} y={height - 4} fontSize={10} fill={CHART_COLORS.dim} textAnchor="middle">
          {xLabel}
        </text>
      )}
    </svg>
  );
}

export function LineChart({
  points,
  xLabel,
  yLabel,
  color = CHART_COLORS.green,
  bandColor = CHART_COLORS.green,
  width = 480,
  height = 240,
}: {
  points: { x: number; y: number; lower?: number; upper?: number }[];
  xLabel: string;
  yLabel: string;
  color?: string;
  bandColor?: string;
  width?: number;
  height?: number;
}) {
  const pad = 38;
  if (points.length === 0) return null;
  const sortedPoints = [...points].sort((a, b) => a.x - b.x);
  const xs = sortedPoints.map((p) => p.x);
  const ys = sortedPoints.flatMap((p) => [p.y, p.lower ?? p.y, p.upper ?? p.y]);
  const xLo = Math.min(...xs);
  const xHi = Math.max(...xs);
  const yLo = Math.min(...ys);
  const yHi = Math.max(...ys);
  const sx = (v: number) => pad + ((v - xLo) / (xHi - xLo || 1)) * (width - pad * 1.4);
  const sy = (v: number) => height - pad - ((v - yLo) / (yHi - yLo || 1)) * (height - pad * 1.4);

  const hasBand = sortedPoints.every((p) => p.lower !== undefined && p.upper !== undefined);
  const bandPath = hasBand
    ? `M ${sortedPoints.map((p) => `${sx(p.x)},${sy(p.upper!)}`).join(" L ")} L ${sortedPoints
        .slice()
        .reverse()
        .map((p) => `${sx(p.x)},${sy(p.lower!)}`)
        .join(" L ")} Z`
    : null;
  const linePath = `M ${sortedPoints.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" L ")}`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg" role="img" aria-label={`${yLabel} vs ${xLabel}`}>
      {niceTicks(yLo, yHi).map((v, i) => (
        <g key={i}>
          <line x1={sx(xLo)} x2={sx(xHi)} y1={sy(v)} y2={sy(v)} stroke={CHART_COLORS.border} strokeWidth={1} />
          <text x={sx(xLo) - 6} y={sy(v)} fontSize={9} fill={CHART_COLORS.dim} textAnchor="end" dominantBaseline="middle">
            {v.toFixed(2)}
          </text>
        </g>
      ))}
      {bandPath && <path d={bandPath} fill={bandColor} opacity={0.15} stroke="none" />}
      <path d={linePath} fill="none" stroke={color} strokeWidth={2} />
      {sortedPoints.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={2.6} fill={color} />
      ))}
      <text x={width / 2} y={height - 4} fontSize={10} fill={CHART_COLORS.dim} textAnchor="middle">
        {xLabel}
      </text>
      <text x={11} y={height / 2} fontSize={10} fill={CHART_COLORS.dim} textAnchor="middle" transform={`rotate(-90 11 ${height / 2})`}>
        {yLabel}
      </text>
    </svg>
  );
}

export function CategoryBars({
  items,
  color = CHART_COLORS.amber,
  width = 480,
  height = 220,
  valueFormatter = (v: number) => v.toFixed(2),
}: {
  items: { category: string; score: number; n?: number }[];
  color?: string;
  width?: number;
  height?: number;
  valueFormatter?: (v: number) => string;
}) {
  const pad = 30;
  const max = Math.max(1e-9, ...items.map((i) => i.score));
  const plotW = width - pad * 1.2;
  const barW = plotW / Math.max(items.length, 1);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="chart-svg">
      {items.map((item, i) => {
        const h = (item.score / max) * (height - pad * 2.2);
        const x = pad + i * barW;
        return (
          <g key={item.category}>
            <rect x={x + barW * 0.15} y={height - pad - h} width={barW * 0.7} height={h} fill={color} opacity={0.85} rx={2} />
            <text x={x + barW / 2} y={height - pad - h - 4} fontSize={9} fill={CHART_COLORS.text} textAnchor="middle">
              {valueFormatter(item.score)}
            </text>
            <text x={x + barW / 2} y={height - pad + 12} fontSize={9} fill={CHART_COLORS.dim} textAnchor="middle">
              {item.category.length > 10 ? `${item.category.slice(0, 9)}…` : item.category}
            </text>
            {item.n !== undefined && (
              <text x={x + barW / 2} y={height - pad + 23} fontSize={8} fill={CHART_COLORS.dim} textAnchor="middle">
                n={item.n}
              </text>
            )}
          </g>
        );
      })}
      <line x1={pad} x2={width - pad * 0.2} y1={height - pad} y2={height - pad} stroke={CHART_COLORS.border} />
    </svg>
  );
}

export function Gauge({
  value,
  size = 120,
  color = CHART_COLORS.blue,
  label,
}: {
  value: number;
  size?: number;
  color?: string;
  label?: string;
}) {
  const r = size / 2 - 10;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, value));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={CHART_COLORS.border} strokeWidth={10} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={10}
        strokeDasharray={`${c * pct} ${c}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y={label ? "46%" : "50%"} textAnchor="middle" dominantBaseline="middle" fontSize={size * 0.2} fill={CHART_COLORS.text} fontWeight={700}>
        {(pct * 100).toFixed(1)}%
      </text>
      {label && (
        <text x="50%" y="64%" textAnchor="middle" fontSize={size * 0.08} fill={CHART_COLORS.dim}>
          {label}
        </text>
      )}
    </svg>
  );
}
