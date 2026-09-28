import type { ReactNode } from "react";
import type { DlTokenWeight } from "./apiClient";
import "./textHighlights.css";

// Shared by deep_learning text scenarios (Banzhaf log-odds, via dlShared.tsx) and
// tabular_ml text features (SHAP probability points, via prediction's explain_text).
export type WeightFormat = (weight: number) => string;
const logOdds: WeightFormat = (w) => `${w >= 0 ? "+" : ""}${w.toFixed(3)} log-odds`;
/** SHAP of a probability model: a weight of 0.042 is +4.2 percentage points. */
export const probabilityPoints: WeightFormat = (w) => `${w >= 0 ? "+" : "−"}${Math.abs(w * 100).toFixed(1)} pts`;

/** Words colored by their attribution: warm = evidence FOR the explained class,
 * cool = evidence against, intensity relative to the strongest word. */
export function TokenHighlights({
  text,
  tokens,
  format = logOdds,
}: {
  text: string;
  tokens: DlTokenWeight[];
  format?: WeightFormat;
}) {
  const max = Math.max(1e-9, ...tokens.map((t) => Math.abs(t.weight ?? 0)));
  const parts: ReactNode[] = [];
  let cursor = 0;
  tokens.forEach((t, i) => {
    if (t.start > cursor) parts.push(<span key={`g${i}`}>{text.slice(cursor, t.start)}</span>);
    const w = t.weight ?? 0;
    const strength = Math.round((Math.abs(w) / max) * 70);
    const color = w >= 0 ? "var(--red)" : "var(--blue)";
    parts.push(
      <span
        key={i}
        className="dl-token"
        style={strength > 4 ? { background: `color-mix(in srgb, ${color} ${strength}%, transparent)` } : undefined}
        title={t.weight === null ? "Beyond the model's input length" : w === 0 ? "No weight (not in the vocabulary)" : format(w)}
      >
        {text.slice(t.start, t.end)}
      </span>,
    );
    cursor = t.end;
  });
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <p className="dl-token-text">{parts}</p>;
}

export function TopWords({
  tokens,
  limit = 8,
  format,
}: {
  tokens: DlTokenWeight[];
  limit?: number;
  format?: WeightFormat;
}) {
  const ranked = tokens
    .filter((t) => t.weight !== null && t.weight !== 0 && /\w/.test(t.text))
    .sort((a, b) => Math.abs(b.weight ?? 0) - Math.abs(a.weight ?? 0))
    .slice(0, limit);
  return (
    <div className="dl-top-words">
      {ranked.map((t, i) => (
        <span key={i} className={`dl-word-chip ${(t.weight ?? 0) >= 0 ? "dl-word-chip--for" : "dl-word-chip--against"}`}>
          {t.text}{" "}
          <small>{format ? format(t.weight ?? 0) : `${(t.weight ?? 0) >= 0 ? "+" : ""}${(t.weight ?? 0).toFixed(2)}`}</small>
        </span>
      ))}
    </div>
  );
}
