import { useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import type { MetricSpec } from "./dataApi";
import { dateMs, formatDate, formatTick, isIsoDate, niceScale, sharedPrefix, type ChartSeries } from "./dashboardChartLogic";
import "./dashboardCharts.css";

/**
 * The dashboards' line and bar charts: SVG at the real pixel width, so text stays readable on a
 * phone. Static between inputs, with no frame loop: each series is one path, and hovering redraws
 * only the crosshair or band and the tooltip.
 */

type ChartProps = {
  categories: string[];
  series: ChartSeries[];
  format: MetricSpec["format"];
  label: string;
  tipTitle: (category: string) => string;
  formatValue: (value: number | null) => string;
  height?: number;
};

const CHAR_WIDTH = 6.2;
const TICK_FONT = 11;
const MAX_ROTATED_CHARS = 24;
const ROTATION = 35;
// Full lines of point markers only while they stay readable as points.
const MAX_LINE_MARKERS = 40;

function usePlotWidth() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

function ChartFrame({
  plotRef,
  series,
  tip,
  children,
}: {
  plotRef: React.RefObject<HTMLDivElement | null>;
  series: ChartSeries[];
  tip: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="dash-chart">
      <div className="dash-chart__plot" ref={plotRef}>
        {children}
        {tip}
      </div>
      {series.length > 1 && (
        <div className="dash-chart__legend">
          {series.map((s) => (
            <span key={s.name}>
              <i
                className={s.dashed ? "dash-chart__swatch--dashed" : undefined}
                style={s.dashed ? { borderColor: s.color } : { background: s.color, opacity: s.opacity }}
              />
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ChartTip({
  x,
  width,
  title,
  series,
  index,
  formatValue,
}: {
  x: number;
  width: number;
  title: string;
  series: ChartSeries[];
  index: number;
  formatValue: (value: number | null) => string;
}) {
  return (
    <div className="dash-chart__tip" style={{ left: x }} data-side={x > width * 0.6 ? "left" : "right"}>
      <b>{title}</b>
      {series.map((s) => (
        <span key={s.name} className="dash-chart__tip-row">
          {series.length > 1 && (
            <span>
              <i style={{ background: s.color, opacity: s.opacity }} />
              {s.name}
            </span>
          )}
          <strong>{formatValue(s.values[index])}</strong>
        </span>
      ))}
    </div>
  );
}

function valueRange(series: ChartSeries[]): [number, number] {
  const values = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  // Lengths and positions read from zero: both charts start there unless the data goes below it.
  return [Math.min(0, ...values), Math.max(0, ...values)];
}

function leftMargin(ticks: number[], format: MetricSpec["format"]): number {
  return Math.max(...ticks.map((t) => formatTick(t, format).length)) * CHAR_WIDTH + 12;
}

function YAxis({ ticks, y, left, right, format }: { ticks: number[]; y: (v: number) => number; left: number; right: number; format: MetricSpec["format"] }) {
  return (
    <g className="dash-chart__axis">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={left} x2={right} y1={y(t)} y2={y(t)} />
          <text x={left - 6} y={y(t)} textAnchor="end" dominantBaseline="middle" fontSize={TICK_FONT}>
            {formatTick(t, format)}
          </text>
        </g>
      ))}
    </g>
  );
}

/** One line per series on a shared axis, by date when the categories are dates. */
export function DashLineChart({ categories, series, format, label, tipTitle, formatValue, height = 280 }: ChartProps) {
  const [plotRef, width] = usePlotWidth();
  const [hover, setHover] = useState<number | null>(null);
  const top = 10;
  const bottom = height - 26;
  const right = width - 12;

  const chart = useMemo(() => {
    if (width <= 0 || categories.length === 0) return null;
    const scale = niceScale(...valueRange(series));
    const left = leftMargin(scale.ticks, format);
    const isTime = categories.every(isIsoDate);
    const positions = categories.map((c, i) => (isTime ? dateMs(c) : i));
    const xLo = positions[0];
    const xHi = positions[positions.length - 1];
    const sx = (p: number) => (xHi === xLo ? (left + right) / 2 : left + ((p - xLo) / (xHi - xLo)) * (right - left));
    const sy = (v: number) => bottom - ((v - scale.lo) / (scale.hi - scale.lo)) * (bottom - top);
    const xs = positions.map(sx);
    const lines = series.map((s) => {
      let d = "";
      let pen = false;
      let dots = "";
      s.values.forEach((v, i) => {
        if (v === null) {
          pen = false;
          return;
        }
        const x = xs[i].toFixed(1);
        const y = sy(v).toFixed(1);
        d += `${pen ? "L" : "M"}${x} ${y}`;
        pen = true;
        if (categories.length <= MAX_LINE_MARKERS) dots += `M${(xs[i] - 3).toFixed(1)} ${y}a3 3 0 1 0 6 0a3 3 0 1 0 -6 0`;
      });
      return { name: s.name, color: s.color, dashed: s.dashed, d, dots };
    });
    const room = Math.max(2, Math.floor((right - left) / 76));
    let xTicks: { x: number; text: string }[];
    const years = isTime ? new Date(xHi).getUTCFullYear() - new Date(xLo).getUTCFullYear() : 0;
    if (isTime && years >= 2) {
      xTicks = [];
      for (let year = new Date(xLo).getUTCFullYear() + 1; year <= new Date(xHi).getUTCFullYear(); year += 1) {
        xTicks.push({ x: sx(Date.UTC(year, 0, 1)), text: String(year) });
      }
    } else {
      const step = Math.max(1, Math.ceil(categories.length / room));
      xTicks = categories.flatMap((c, i) => (i % step === 0 ? [{ x: xs[i], text: isTime ? formatDate(c, false) : c }] : []));
    }
    return { scale, left, xs, sy, lines, xTicks };
  }, [categories, series, format, width, right, bottom]);

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (!chart) return;
    const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
    let best = 0;
    for (let i = 1; i < chart.xs.length; i += 1) {
      if (Math.abs(chart.xs[i] - px) < Math.abs(chart.xs[best] - px)) best = i;
    }
    setHover(best);
  };

  return (
    <ChartFrame
      plotRef={plotRef}
      series={series}
      tip={
        chart && hover !== null ? (
          <ChartTip x={chart.xs[hover]} width={width} title={tipTitle(categories[hover])} series={series} index={hover} formatValue={formatValue} />
        ) : null
      }
    >
      <svg width={width} height={height} role="img" aria-label={label} onPointerMove={onPointerMove} onPointerLeave={() => setHover(null)}>
        {chart && (
          <>
            <YAxis ticks={chart.scale.ticks} y={chart.sy} left={chart.left} right={right} format={format} />
            <g className="dash-chart__axis">
              {chart.xTicks.map((t) => (
                <text key={t.text} x={t.x} y={height - 8} textAnchor={t.x > right - 24 ? "end" : "middle"} fontSize={TICK_FONT}>
                  {t.text}
                </text>
              ))}
            </g>
            {chart.lines.map((l) => (
              <g key={l.name}>
                <path d={l.d} fill="none" stroke={l.color} strokeWidth={2} strokeLinejoin="round" strokeDasharray={l.dashed ? "5 4" : undefined} />
                {l.dots && <path d={l.dots} fill={l.color} />}
              </g>
            ))}
            {hover !== null && (
              <g>
                <line className="dash-chart__crosshair" x1={chart.xs[hover]} x2={chart.xs[hover]} y1={top} y2={bottom} />
                {series.map((s) =>
                  s.values[hover] === null ? null : (
                    <circle key={s.name} className="dash-chart__point" cx={chart.xs[hover]} cy={chart.sy(s.values[hover] as number)} r={4} fill={s.color} />
                  ),
                )}
              </g>
            )}
          </>
        )}
      </svg>
    </ChartFrame>
  );
}

function barPath(x: number, width: number, base: number, end: number): string {
  const up = end <= base;
  const radius = Math.min(4, width / 2, Math.abs(end - base));
  const corner = up ? radius : -radius;
  return `M${x.toFixed(1)} ${base.toFixed(1)}V${(end + corner).toFixed(1)}Q${x.toFixed(1)} ${end.toFixed(1)} ${(x + radius).toFixed(1)} ${end.toFixed(1)}H${(x + width - radius).toFixed(1)}Q${(x + width).toFixed(1)} ${end.toFixed(1)} ${(x + width).toFixed(1)} ${(end + corner).toFixed(1)}V${base.toFixed(1)}Z`;
}

/** Columns per category, side by side when there are several series. */
export function DashBarChart({ categories, series, format, label, tipTitle, formatValue, height = 280 }: ChartProps) {
  const [plotRef, width] = usePlotWidth();
  const [hover, setHover] = useState<number | null>(null);
  const right = width - 8;
  const top = 10;

  const chart = useMemo(() => {
    if (width <= 0 || categories.length === 0) return null;
    const scale = niceScale(...valueRange(series));
    const prefix = sharedPrefix(categories);
    const names = categories.map((c) => {
      const text = c.slice(prefix.length);
      return text.length > MAX_ROTATED_CHARS ? `${text.slice(0, MAX_ROTATED_CHARS - 1)}…` : text;
    });
    const longest = Math.max(...names.map((n) => n.length)) * CHAR_WIDTH;
    const tickLeft = leftMargin(scale.ticks, format);
    const roughBand = (right - tickLeft) / categories.length;
    const shortLabels = longest <= 34;
    const flat = longest + 6 <= roughBand;
    const rotated = !flat && !shortLabels;
    const radians = (ROTATION * Math.PI) / 180;
    // A slanted label runs left of its bar, so the first one needs room to land on the page.
    const left = rotated ? Math.max(tickLeft, Math.cos(radians) * longest - roughBand / 2 + 4) : tickLeft;
    const bottomRoom = rotated ? Math.sin(radians) * longest + 18 : 26;
    const band = (right - left) / categories.length;
    const every = flat ? 1 : Math.max(1, Math.ceil((rotated ? 18 : longest + 8) / band));
    const bottom = height - bottomRoom;
    const sy = (v: number) => bottom - ((v - scale.lo) / (scale.hi - scale.lo)) * (bottom - top);
    const base = sy(0);
    const groupWidth = Math.min(band * 0.76, 56 * series.length);
    const gap = series.length > 1 ? Math.min(2, groupWidth * 0.06) : 0;
    const barWidth = (groupWidth - gap * (series.length - 1)) / series.length;
    const paths = series.map((s, si) => ({
      name: s.name,
      color: s.color,
      opacity: s.opacity,
      d: s.values
        .map((v, i) => {
          if (v === null || v === 0) return "";
          const x = left + i * band + (band - groupWidth) / 2 + si * (barWidth + gap);
          return barPath(x, barWidth, base, sy(v));
        })
        .join(""),
    }));
    return { scale, left, band, sy, bottom, names, every, rotated, paths };
  }, [categories, series, format, width, right, height]);

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (!chart) return;
    const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
    const index = Math.floor((px - chart.left) / chart.band);
    setHover(index >= 0 && index < categories.length ? index : null);
  };

  return (
    <ChartFrame
      plotRef={plotRef}
      // A comparison is a dashed line but a solid, muted bar, and the legend shows what is drawn.
      series={series.map((s) => ({ ...s, dashed: false }))}
      tip={
        chart && hover !== null ? (
          <ChartTip
            x={chart.left + (hover + 0.5) * chart.band}
            width={width}
            title={tipTitle(categories[hover])}
            series={series}
            index={hover}
            formatValue={formatValue}
          />
        ) : null
      }
    >
      <svg width={width} height={height} role="img" aria-label={label} onPointerMove={onPointerMove} onPointerLeave={() => setHover(null)}>
        {chart && (
          <>
            <YAxis ticks={chart.scale.ticks} y={chart.sy} left={chart.left} right={right} format={format} />
            {hover !== null && <rect className="dash-chart__band" x={chart.left + hover * chart.band} y={top} width={chart.band} height={chart.bottom - top} />}
            {chart.paths.map((p) => (
              <path key={p.name} d={p.d} fill={p.color} fillOpacity={p.opacity} />
            ))}
            <g className="dash-chart__axis">
              {chart.names.map((name, i) => {
                if (i % chart.every !== 0) return null;
                const x = chart.left + (i + 0.5) * chart.band;
                const y = chart.bottom + 15;
                return chart.rotated ? (
                  <text key={categories[i]} x={x} y={y} textAnchor="end" fontSize={TICK_FONT} transform={`rotate(${-ROTATION} ${x} ${y})`}>
                    {name}
                  </text>
                ) : (
                  <text key={categories[i]} x={x} y={y} textAnchor="middle" fontSize={TICK_FONT}>
                    {name}
                  </text>
                );
              })}
            </g>
          </>
        )}
      </svg>
    </ChartFrame>
  );
}
