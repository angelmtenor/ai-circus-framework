export type Record_ = Record<string, number | string>;

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
