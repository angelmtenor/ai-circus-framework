/**
 * The shared parameter controls: one visual language for choosing values, whether the user is
 * querying data (a range of values) or asking a model (one value). Static DOM and SVG only —
 * nothing animates, so a tab built from these stays quiet when idle.
 */
import { useMemo, type ReactNode } from "react";
import { histogramCounts } from "./controlsLogic";
import "./controls.css";

/** Where the real values sit: bars inside the selected range are lit, the rest are dimmed. */
function MiniHistogram({ counts, min, max, low, high }: { counts: number[]; min: number; max: number; low: number; high: number }) {
  const peak = Math.max(1, ...counts);
  const width = (max - min) / counts.length;
  return (
    <svg className="range-histogram" viewBox={`0 0 ${counts.length} 20`} preserveAspectRatio="none" aria-hidden="true">
      {counts.map((c, i) => {
        const from = min + i * width;
        const inside = from + width > low && from < high;
        const h = c === 0 ? 0 : Math.max(1.5, (c / peak) * 20);
        return <rect key={i} x={i + 0.08} y={20 - h} width={0.84} height={h} className={inside ? "in" : "out"} />;
      })}
    </svg>
  );
}

/**
 * A two-handle range slider, optionally over a histogram of the real values — the filter for any
 * numeric or date column. `format` renders the two ends (a date for a time column).
 */
export function RangeSlider({
  label,
  min,
  max,
  step,
  low,
  high,
  onChange,
  format = (v) => String(v),
  values,
  unit,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  low: number;
  high: number;
  onChange: (low: number, high: number) => void;
  format?: (value: number) => string;
  values?: number[];
  unit?: string;
}) {
  const counts = useMemo(() => (values ? histogramCounts(values, min, max) : null), [values, min, max]);
  const span = max - min || 1;
  const left = ((low - min) / span) * 100;
  const right = ((high - min) / span) * 100;
  const full = low <= min && high >= max;
  return (
    <div className={`range-control${full ? "" : " range-control--active"}`}>
      <div className="range-control-head">
        <span className="range-control-label">{label}</span>
        <span className="range-control-values">
          {format(low)} – {format(high)}
          {unit ? ` ${unit}` : ""}
        </span>
      </div>
      {counts && <MiniHistogram counts={counts} min={min} max={max} low={low} high={high} />}
      <div className="range-slider">
        <div className="range-slider-track" />
        <div className="range-slider-fill" style={{ left: `${left}%`, width: `${Math.max(0, right - left)}%` }} />
        <input
          type="range"
          aria-label={`${label} from`}
          min={min}
          max={max}
          step={step}
          value={low}
          onChange={(e) => onChange(Math.min(Number(e.target.value), high), high)}
        />
        <input
          type="range"
          aria-label={`${label} to`}
          min={min}
          max={max}
          step={step}
          value={high}
          onChange={(e) => onChange(low, Math.max(Number(e.target.value), low))}
        />
      </div>
      <div className="range-control-bounds">
        <span>{format(min)}</span>
        {!full && (
          <button type="button" className="range-control-reset" onClick={() => onChange(min, max)}>
            all
          </button>
        )}
        <span>{format(max)}</span>
      </div>
    </div>
  );
}

/**
 * A set of options to keep: chips, plus "all" / "none" once there are enough of them to need it.
 */
export function ChoiceChips({
  label,
  options,
  selected,
  onChange,
  optionLabel = (o) => o,
  wide = false,
}: {
  wide?: boolean;
  label: string;
  options: string[];
  selected: ReadonlySet<string>;
  onChange: (selected: Set<string>) => void;
  optionLabel?: (option: string) => string;
}) {
  const all = selected.size === options.length;
  function toggle(option: string) {
    const next = new Set(selected);
    if (next.has(option)) next.delete(option);
    else next.add(option);
    onChange(next);
  }
  return (
    <div className={`choice-control${all ? "" : " choice-control--active"}${wide ? " choice-control--wide" : ""}`}>
      <div className="range-control-head">
        <span className="range-control-label">{label}</span>
        <span className="range-control-values">
          {selected.size} of {options.length}
          {options.length > 3 && (
            <>
              <button type="button" className="range-control-reset" onClick={() => onChange(new Set(options))} disabled={all}>
                all
              </button>
              <button type="button" className="range-control-reset" onClick={() => onChange(new Set())} disabled={selected.size === 0}>
                none
              </button>
            </>
          )}
        </span>
      </div>
      <div className="filter-row-chips">
        {options.map((option) => (
          <button
            type="button"
            key={option}
            className={`filter-chip ${selected.has(option) ? "active" : ""}`}
            aria-pressed={selected.has(option)}
            onClick={() => toggle(option)}
          >
            {optionLabel(option)}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * One number: a slider with its value, the same card as a range filter, plus a box to type an exact value. The
 * prediction form, the what-if forms and the forecast levers all use it.
 */
export function NumberSlider({
  label,
  info,
  min,
  max,
  step,
  value,
  onChange,
  unit,
  format = (v) => String(v),
  disabled = false,
  editable = true,
}: {
  label: string;
  info?: ReactNode;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
  unit?: string;
  format?: (value: number) => string;
  disabled?: boolean;
  // A box to type an exact value next to the slider (off for coarse levers).
  editable?: boolean;
}) {
  const fill = max > min ? ((Math.min(max, Math.max(min, value)) - min) / (max - min)) * 100 : 0;
  return (
    <label className={`range-control number-control${disabled ? " number-control--disabled" : ""}`}>
      <span className="range-control-head">
        <span className="range-control-label">
          {label} {info}
        </span>
        <span className="range-control-values">
          {format(value)}
          {unit ? ` ${unit}` : ""}
        </span>
      </span>
      <input
        type="range"
        className="number-control-slider"
        style={{ ["--fill" as string]: `${fill}%` }}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={label}
      />
      <span className="range-control-bounds">
        <span>{format(min)}</span>
        {editable && (
          <input
            type="number"
            className="number-control-input"
            min={min}
            max={max}
            step={step}
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(Number(e.target.value))}
            aria-label={`${label}, exact value`}
          />
        )}
        <span>{format(max)}</span>
      </span>
    </label>
  );
}

/**
 * One option out of several: segmented buttons when they fit on a line, a dropdown when there are many.
 */
export function ChoiceSelect({
  label,
  info,
  options,
  value,
  onChange,
  optionLabel = (o) => o,
}: {
  label: string;
  info?: ReactNode;
  options: string[];
  value: string;
  onChange: (value: string) => void;
  optionLabel?: (option: string) => string;
}) {
  const segmented = options.length <= 5 && options.reduce((n, o) => n + optionLabel(o).length, 0) <= 36;
  return (
    <div className="choice-control choice-select">
      <span className="range-control-head">
        <span className="range-control-label">
          {label} {info}
        </span>
      </span>
      {segmented ? (
        <div className="choice-segmented" role="radiogroup" aria-label={label}>
          {options.map((o) => (
            <button type="button" key={o} role="radio" aria-checked={o === value} className={o === value ? "active" : ""} onClick={() => onChange(o)}>
              {optionLabel(o)}
            </button>
          ))}
        </div>
      ) : (
        <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
          {options.map((o) => (
            <option key={o} value={o}>
              {optionLabel(o)}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
