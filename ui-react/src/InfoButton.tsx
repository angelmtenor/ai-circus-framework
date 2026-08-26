import { Icon } from "./Icon";

/**
 * Small inline "ⓘ" disclosure toggle — reveals a short technical explanation without
 * cluttering the default view. Used throughout the tabular_ml UI to keep the primary
 * copy plain-language while keeping precise detail one click away (see feature/target/
 * metric `info` fields in scenario_schema.py and metricGlossary.ts).
 */
export function InfoButton({ text }: { text: string }) {
  return (
    <details className="info-disclosure">
      <summary aria-label="More info">
        <Icon name="info" size={12} />
      </summary>
      <p className="info-disclosure-text">{text}</p>
    </details>
  );
}
