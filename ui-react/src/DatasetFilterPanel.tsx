import { useEffect, useMemo, useState } from "react";
import type { FeatureSpec } from "./apiClient";

export type DatasetRow = Record<string, string | number | null>;

type NumericFilter = { type: "numeric"; min: number; max: number };
type CategoricalFilter = { type: "categorical"; selected: Set<string> };
type Filter = NumericFilter | CategoricalFilter;

function initialFilters(featureColumns: string[], featureSchema: Record<string, FeatureSpec>): Record<string, Filter> {
  const filters: Record<string, Filter> = {};
  for (const feature of featureColumns) {
    const spec = featureSchema[feature];
    if (!spec) continue;
    filters[feature] =
      spec.type === "numeric" ? { type: "numeric", min: spec.min, max: spec.max } : { type: "categorical", selected: new Set(spec.options) };
  }
  return filters;
}

function passesFilter(row: DatasetRow, feature: string, filter: Filter): boolean {
  const value = row[feature];
  if (value === null || value === undefined) return true;
  if (filter.type === "numeric") return Number(value) >= filter.min && Number(value) <= filter.max;
  return filter.selected.has(String(value));
}

/**
 * Query/filter builder driven by feature_schema — numeric range sliders, categorical
 * multiselects — applied client-side over an already-fetched dataset sample. No ML
 * here; this is the "query the data" piece shared by the Dataset (view/export) and
 * ML Predictions (batch-predict on the filtered rows) sections, so filter behavior
 * stays identical between them.
 */
export function DatasetFilterPanel({
  featureColumns,
  featureSchema,
  rows,
  onFilteredChange,
}: {
  featureColumns: string[];
  featureSchema: Record<string, FeatureSpec>;
  rows: DatasetRow[];
  onFilteredChange: (rows: DatasetRow[]) => void;
}) {
  const [filters, setFilters] = useState<Record<string, Filter>>(() => initialFilters(featureColumns, featureSchema));

  const filteredRows = useMemo(
    () => rows.filter((row) => featureColumns.every((feature) => passesFilter(row, feature, filters[feature]))),
    [rows, featureColumns, filters],
  );

  useEffect(() => {
    onFilteredChange(filteredRows);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredRows]);

  function updateNumeric(feature: string, key: "min" | "max", value: number) {
    setFilters((f) => ({ ...f, [feature]: { ...(f[feature] as NumericFilter), [key]: value } }));
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
      {featureColumns.map((feature) => {
        const spec = featureSchema[feature];
        const filter = filters[feature];
        if (!spec || !filter) return null;
        if (spec.type === "numeric" && filter.type === "numeric") {
          return (
            <div className="filter-row" key={feature}>
              <span className="filter-row-label">{feature}</span>
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
        if (spec.type === "categorical" && filter.type === "categorical") {
          return (
            <div className="filter-row" key={feature}>
              <span className="filter-row-label">{feature}</span>
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
