import type { FeatureSpec, ScenarioSummary } from "./apiClient";
import { InfoButton } from "./InfoButton";

export type Record_ = Record<string, number | string>;

/** The friendly display name for a feature column — falls back to the raw column name
 * for scenarios/columns without a `label` (e.g. the target, or older scenario data).
 * Use this everywhere a raw feature name is currently displayed to the user.
 */
export function featureLabel(scenario: ScenarioSummary, feature: string): string {
  return scenario.feature_schema?.[feature]?.label ?? feature;
}

/** A fresh record seeded with each feature's default value — the starting point for
 * both the single-record prediction form and the "explore model" what-if form.
 */
export function initialRecord(featureColumns: string[], featureSchema: Record<string, FeatureSpec>): Record_ {
  const initial: Record_ = {};
  for (const feature of featureColumns) initial[feature] = featureSchema[feature].default;
  return initial;
}

/** One feature's editable input — a slider+number pair for numeric features, a
 * dropdown for categorical ones. Shared by the single-record prediction form and the
 * "explore model" what-if form.
 */
export function FeatureInput({
  feature,
  spec,
  value,
  onChange,
}: {
  feature: string;
  spec: FeatureSpec;
  value: number | string;
  onChange: (value: number | string) => void;
}) {
  const label = spec.label || feature;
  if (spec.type === "numeric") {
    return (
      <label className="feature-input">
        <span className="feature-input-label">
          {label} {spec.info && <InfoButton text={spec.info} />} <span className="feature-input-range">{spec.min}–{spec.max}</span>
        </span>
        <input type="range" min={spec.min} max={spec.max} step={spec.step ?? 1} value={value} onChange={(e) => onChange(Number(e.target.value))} />
        <input
          type="number"
          className="feature-input-number"
          min={spec.min}
          max={spec.max}
          step={spec.step ?? 1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      </label>
    );
  }
  return (
    <label className="feature-input">
      <span className="feature-input-label">
        {label} {spec.info && <InfoButton text={spec.info} />}
      </span>
      <select value={value as string} onChange={(e) => onChange(e.target.value)}>
        {spec.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Map transformed (one-hot) SHAP contribution keys back to the original feature the
 * user actually sees — numeric features match directly; for a categorical feature
 * only the one-hot column matching the record's *selected* value is kept (the
 * unselected columns' contributions aren't meaningful to show per-feature).
 */
export function mapContributions(record: Record_, contributions: Record<string, number>): { label: string; value: number }[] {
  const items: { label: string; value: number }[] = [];
  for (const [name, value] of Object.entries(contributions)) {
    const unprefixed = name.includes("__") ? name.slice(name.indexOf("__") + 2) : name;
    if (unprefixed in record) {
      items.push({ label: unprefixed, value });
      continue;
    }
    const match = Object.entries(record).find(([f, v]) => unprefixed === `${f}_${v}`);
    if (match) items.push({ label: match[0], value });
  }
  return items;
}

export function topContribution(record: Record_, contributions: Record<string, number>): { label: string; value: number } | null {
  const items = mapContributions(record, contributions);
  if (items.length === 0) return null;
  return items.reduce((a, b) => (Math.abs(b.value) > Math.abs(a.value) ? b : a));
}

export function exportJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
