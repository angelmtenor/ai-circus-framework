import { useEffect, useMemo, useState } from "react";
import type { FeatureSpec } from "./apiClient";
import { ChoiceChips, RangeSlider } from "./controls";
import { extent, formatEpochDay, snapExtent, toEpochDay } from "./controlsLogic";

export type DatasetRow = Record<string, string | number | boolean | null>;

type NumericFilter = { type: "numeric"; min: number; max: number };
type CategoricalFilter = { type: "categorical"; selected: Set<string> };
// Free text (e.g. a review): keep rows containing the query (case-insensitive).
type TextFilter = { type: "text"; query: string };
type Filter = NumericFilter | CategoricalFilter | TextFilter;

// Collapsed by default past this many filters: the first few are the ones a user reaches for.
const VISIBLE_FILTERS = 6;

/** Each numeric column's slider: from its real values (snapped to the step), else the schema's range. */
type Bounds = { min: number; max: number; step: number; values: number[] };

function numericBounds(rows: DatasetRow[], column: string, spec: { min: number; max: number; step?: number | null }): Bounds {
  const values = rows.map((r) => Number(r[column])).filter((v) => Number.isFinite(v));
  const found = extent(values);
  const step = spec.step && spec.step > 0 ? spec.step : niceStep(found ? found[1] - found[0] : spec.max - spec.min);
  const [min, max] = found ? snapExtent(found, step) : [spec.min, spec.max];
  return { min, max, step, values };
}

/** About a hundred slider positions over `range`, at a round step (1, 0.1, 10, …). */
function niceStep(range: number): number {
  if (!(range > 0)) return 1;
  return 10 ** Math.floor(Math.log10(range / 100));
}

function dateBounds(rows: DatasetRow[], column: string): Bounds | null {
  const values = rows.map((r) => toEpochDay(r[column])).filter((v): v is number => v !== null);
  const found = extent(values);
  return found ? { min: found[0], max: found[1], step: 1, values } : null;
}

function normalizeCategorical(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

function passesFilter(row: DatasetRow, feature: string, filter: Filter, isDate: boolean): boolean {
  const value = row[feature];
  if (value === null || value === undefined) return true;
  if (filter.type === "numeric") {
    const v = isDate ? toEpochDay(value) : Number(value);
    return v === null || (v >= filter.min && v <= filter.max);
  }
  if (filter.type === "text") return !filter.query.trim() || String(value).toLowerCase().includes(filter.query.trim().toLowerCase());
  return filter.selected.has(normalizeCategorical(value));
}

function isActive(filter: Filter, bounds: Bounds | undefined, spec: FeatureSpec | undefined): boolean {
  if (filter.type === "text") return filter.query.trim() !== "";
  if (filter.type === "numeric") return !!bounds && (filter.min > bounds.min || filter.max < bounds.max);
  return spec?.type === "categorical" && filter.selected.size !== spec.options.length;
}

/**
 * Query/filter builder driven by feature_schema — a two-handle range slider over each numeric
 * column's real values (with their histogram), option chips for categorical ones, a "contains" box
 * for free text, and a date range for the time column of a time series — applied client-side over
 * an already-fetched dataset sample. No ML here; this is the "query the data" piece shared by the
 * Data & BI tab and the ML Predictions batch mode, so filter behaviour stays identical between them.
 *
 * `searchColumns` (the record's identity: the dataset's id column plus any display columns such as
 * a name — see DatasetSample) adds a free-text search box, so a specific record can be found by who
 * it is rather than only by its feature values.
 */
export function DatasetFilterPanel({
  featureColumns,
  featureSchema,
  rows,
  onFilteredChange,
  labelFor = (f) => f,
  searchColumns = [],
  dateColumn,
}: {
  featureColumns: string[];
  featureSchema: Record<string, FeatureSpec>;
  rows: DatasetRow[];
  onFilteredChange: (rows: DatasetRow[]) => void;
  labelFor?: (feature: string) => string;
  searchColumns?: string[];
  dateColumn?: string;
}) {
  const columns = useMemo(
    () => (dateColumn ? [dateColumn, ...featureColumns.filter((c) => c !== dateColumn)] : featureColumns),
    [dateColumn, featureColumns],
  );
  // The sliders' ends and histograms come from the rows as fetched, not from the filtered rows.
  const bounds = useMemo(() => {
    const out: Record<string, Bounds> = {};
    for (const column of columns) {
      if (column === dateColumn) {
        const b = dateBounds(rows, column);
        if (b) out[column] = b;
        continue;
      }
      const spec = featureSchema[column];
      if (spec?.type === "numeric") out[column] = numericBounds(rows, column, spec);
    }
    return out;
  }, [rows, columns, dateColumn, featureSchema]);

  const initial = useMemo(() => {
    const filters: Record<string, Filter> = {};
    for (const column of columns) {
      const b = bounds[column];
      if (b) {
        filters[column] = { type: "numeric", min: b.min, max: b.max };
        continue;
      }
      const spec = featureSchema[column];
      if (!spec || column === dateColumn) continue;
      filters[column] = spec.type === "text" ? { type: "text", query: "" } : spec.type === "categorical" ? { type: "categorical", selected: new Set(spec.options) } : { type: "text", query: "" };
    }
    return filters;
  }, [columns, bounds, featureSchema, dateColumn]);

  const [filters, setFilters] = useState<Record<string, Filter>>(initial);
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  // A new sample (another size) brings new bounds: start again from them.
  useEffect(() => setFilters(initial), [initial]);

  const filteredRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter(
      (row) =>
        (!needle || searchColumns.some((c) => String(row[c] ?? "").toLowerCase().includes(needle))) &&
        columns.every((column) => !filters[column] || passesFilter(row, column, filters[column], column === dateColumn)),
    );
  }, [rows, columns, filters, search, searchColumns, dateColumn]);

  useEffect(() => {
    onFilteredChange(filteredRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredRows]);

  const active = columns.filter((c) => filters[c] && isActive(filters[c], bounds[c], featureSchema[c])).length + (search.trim() ? 1 : 0);
  const shown = showAll ? columns : columns.slice(0, VISIBLE_FILTERS);

  return (
    <div className="filter-panel">
      {searchColumns.length > 0 && (
        <div className="filter-row">
          <span className="filter-row-label">Search {searchColumns.map(labelFor).join(" / ")}</span>
          <input
            type="search"
            className="filter-row-search"
            placeholder={`e.g. an ${labelFor(searchColumns[0])}${searchColumns.length > 1 ? " or a name" : ""}`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      )}
      <div className="filter-grid">
        {shown.map((column) => {
          const filter = filters[column];
          const spec = featureSchema[column];
          if (!filter) return null;
          if (filter.type === "numeric") {
            const b = bounds[column];
            const isDate = column === dateColumn;
            return (
              <RangeSlider
                key={column}
                label={labelFor(column)}
                min={b.min}
                max={b.max}
                step={b.step}
                low={filter.min}
                high={filter.max}
                values={b.values}
                format={isDate ? formatEpochDay : (v) => formatNumber(v, b.step)}
                onChange={(min, max) => setFilters((f) => ({ ...f, [column]: { type: "numeric", min, max } }))}
              />
            );
          }
          if (filter.type === "text") {
            return (
              <label className="range-control" key={column}>
                <span className="range-control-head">
                  <span className="range-control-label">{labelFor(column)} contains</span>
                </span>
                <input
                  type="search"
                  placeholder={spec?.type === "text" ? "e.g. politics, micromanag, no direction" : "text to look for"}
                  value={filter.query}
                  onChange={(e) => setFilters((f) => ({ ...f, [column]: { type: "text", query: e.target.value } }))}
                />
              </label>
            );
          }
          if (spec?.type !== "categorical") return null;
          return (
            <ChoiceChips
              key={column}
              wide={spec.options.length > 8}
              label={labelFor(column)}
              options={spec.options}
              selected={filter.selected}
              onChange={(selected) => setFilters((f) => ({ ...f, [column]: { type: "categorical", selected } }))}
            />
          );
        })}
      </div>
      <div className="filter-summary">
        <span>
          <strong>{filteredRows.length.toLocaleString()}</strong> of {rows.length.toLocaleString()} rows match
          {active > 0 && ` · ${active} filter${active > 1 ? "s" : ""} on`}
        </span>
        {active > 0 && (
          <button
            type="button"
            className="range-control-reset"
            onClick={() => {
              setFilters(initial);
              setSearch("");
            }}
          >
            reset filters
          </button>
        )}
        {columns.length > VISIBLE_FILTERS && (
          <button type="button" className="range-control-reset" onClick={() => setShowAll((s) => !s)}>
            {showAll ? "fewer filters" : `all ${columns.length} filters`}
          </button>
        )}
      </div>
    </div>
  );
}

/** A slider end at the precision of its step: whole numbers stay whole, 0.1 steps keep one decimal. */
function formatNumber(value: number, step: number): string {
  const digits = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step)));
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
