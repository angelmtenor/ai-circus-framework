import { useEffect, useMemo, useState } from "react";
import type { FeatureSpec } from "./apiClient";

export type DatasetRow = Record<string, string | number | boolean | null>;

type NumericFilter = { type: "numeric"; min: number; max: number };
type CategoricalFilter = { type: "categorical"; selected: Set<string> };
// Free text (e.g. a review): keep rows containing the query (case-insensitive).
type TextFilter = { type: "text"; query: string };
type Filter = NumericFilter | CategoricalFilter | TextFilter;

function initialFilters(featureColumns: string[], featureSchema: Record<string, FeatureSpec>): Record<string, Filter> {
  const filters: Record<string, Filter> = {};
  for (const feature of featureColumns) {
    const spec = featureSchema[feature];
    if (!spec) continue;
    filters[feature] =
      spec.type === "numeric"
        ? { type: "numeric", min: spec.min, max: spec.max }
        : spec.type === "text"
          ? { type: "text", query: "" }
          : { type: "categorical", selected: new Set(spec.options) };
  }
  return filters;
}

function normalizeCategorical(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

function passesFilter(row: DatasetRow, feature: string, filter: Filter): boolean {
  const value = row[feature];
  if (value === null || value === undefined) return true;
  if (filter.type === "numeric") return Number(value) >= filter.min && Number(value) <= filter.max;
  if (filter.type === "text") return !filter.query.trim() || String(value).toLowerCase().includes(filter.query.trim().toLowerCase());
  return filter.selected.has(normalizeCategorical(value));
}

/**
 * Query/filter builder driven by feature_schema — numeric range sliders, categorical
 * multiselects, a "contains" box for free-text features — applied client-side over an already-fetched dataset sample. No ML
 * here; this is the "query the data" piece shared by the Dataset (view/export) and
 * ML Predictions (batch-predict on the filtered rows) sections, so filter behavior
 * stays identical between them.
 *
 * `searchColumns` (the record's identity: the dataset's id column plus any display
 * columns such as a name — see DatasetSample) adds a free-text search box, so a
 * specific record can be found by who it is rather than only by its feature values.
 */
export function DatasetFilterPanel({
  featureColumns,
  featureSchema,
  rows,
  onFilteredChange,
  labelFor = (f) => f,
  searchColumns = [],
}: {
  featureColumns: string[];
  featureSchema: Record<string, FeatureSpec>;
  rows: DatasetRow[];
  onFilteredChange: (rows: DatasetRow[]) => void;
  labelFor?: (feature: string) => string;
  searchColumns?: string[];
}) {
  const [filters, setFilters] = useState<Record<string, Filter>>(() => initialFilters(featureColumns, featureSchema));
  const [search, setSearch] = useState("");

  const filteredRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter(
      (row) =>
        (!needle || searchColumns.some((c) => String(row[c] ?? "").toLowerCase().includes(needle))) &&
        featureColumns.every((feature) => passesFilter(row, feature, filters[feature])),
    );
  }, [rows, featureColumns, filters, search, searchColumns]);

  useEffect(() => {
    onFilteredChange(filteredRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredRows]);

  function updateNumeric(feature: string, key: "min" | "max", value: number) {
    setFilters((f) => ({ ...f, [feature]: { ...(f[feature] as NumericFilter), [key]: value } }));
  }

  function updateText(feature: string, query: string) {
    setFilters((f) => ({ ...f, [feature]: { type: "text", query } }));
  }

  function toggleCategory(feature: string, option: string) {
    setFilters((f) => {
      const current = f[feature] as CategoricalFilter;
      const selected = new Set(current.selected);
      if (selected.has(option)) selected.delete(option);
      else selected.add(option);
      return { ...f, [feature]: { ...current, selected } };
    });
  }

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
      {featureColumns.map((feature) => {
        const spec = featureSchema[feature];
        const filter = filters[feature];
        if (!spec || !filter) return null;
        if (spec.type === "numeric" && filter.type === "numeric") {
          return (
            <div className="filter-row" key={feature}>
              <span className="filter-row-label">{labelFor(feature)}</span>
              <div className="filter-row-range">
                <input
                  type="number"
                  value={filter.min}
                  min={spec.min}
                  max={filter.max}
                  onChange={(e) => updateNumeric(feature, "min", Number(e.target.value))}
                />
                <span>–</span>
                <input
                  type="number"
                  value={filter.max}
                  min={filter.min}
                  max={spec.max}
                  onChange={(e) => updateNumeric(feature, "max", Number(e.target.value))}
                />
                <span className="filter-row-bounds">
                  (full range: {spec.min}–{spec.max})
                </span>
              </div>
            </div>
          );
        }
        if (spec.type === "text" && filter.type === "text") {
          return (
            <div className="filter-row" key={feature}>
              <span className="filter-row-label">{labelFor(feature)} contains</span>
              <input
                type="search"
                className="filter-row-search"
                placeholder="e.g. politics, micromanag, no direction"
                value={filter.query}
                onChange={(e) => updateText(feature, e.target.value)}
              />
            </div>
          );
        }
        if (spec.type === "categorical" && filter.type === "categorical") {
          return (
            <div className="filter-row" key={feature}>
              <span className="filter-row-label">{labelFor(feature)}</span>
              <div className="filter-row-chips">
                {spec.options.map((option) => (
                  <button
                    key={option}
                    className={`filter-chip ${filter.selected.has(option) ? "active" : ""}`}
                    onClick={() => toggleCategory(feature, option)}
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          );
        }
        return null;
      })}
      <div className="filter-count">{filteredRows.length} of {rows.length} rows match</div>
    </div>
  );
}
