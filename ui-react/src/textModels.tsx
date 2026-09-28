import { predict, type PredictionResult, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { featureLabel, type Record_ } from "./predictUtils";
import { probabilityPoints, TokenHighlights, TopWords } from "./textHighlights";

// Free-text (`type: text`) scenarios: one record scored by the deployed model with
// per-word explanations and — when the scenario has a `model.text_challenger` — by the
// sentence-embedding challenger too, for a side-by-side comparison. Shared by the
// Predict tab, the Voyage/Office what-if panel and the rubric (Leadership) check.

export type TextScore = {
  champion: PredictionResult;
  challenger: PredictionResult | null;
  // Why the challenger has no score (not trained yet, gateway down…) — shown, not thrown.
  challengerError: string | null;
};

export function textFeatures(scenario: ScenarioSummary): string[] {
  return (scenario.feature_columns ?? []).filter((f) => scenario.feature_schema?.[f]?.type === "text");
}

/** Score one record: the deployed model (with word-level explanations) and, if asked
 * and configured, the challenger in parallel. A challenger failure never fails the call. */
export async function scoreTextRecord(
  scenario: ScenarioSummary,
  record: Record_,
  accessToken: string | null,
  withChallenger: boolean,
): Promise<TextScore> {
  const champion = predict(config.predictionUrl, scenario.slug, [record], accessToken, { explainText: true });
  const challenger =
    withChallenger && scenario.text_challenger
      ? predict(config.predictionUrl, scenario.slug, [record], accessToken, { model: "challenger" }).then(
          (r) => ({ result: r.predictions[0], error: null }),
          (e: Error) => ({ result: null, error: e.message }),
        )
      : Promise.resolve({ result: null, error: null });
  const [c, ch] = await Promise.all([champion, challenger]);
  return { champion: c.predictions[0], challenger: ch.result, challengerError: ch.error };
}

function positiveLabel(scenario: ScenarioSummary): string {
  return scenario.target_value_labels?.["1"] ?? scenario.target_label ?? "the positive class";
}

/** Each text feature of `record`, its words coloured by their SHAP share (red = toward
 * the positive class), plus how much the words *absent* from it contributed. */
export function TextFeatureExplanations({
  scenario,
  record,
  result,
}: {
  scenario: ScenarioSummary;
  record: Record_;
  result: PredictionResult;
}) {
  const explanations = result.text_explanations;
  if (!explanations) return null;
  return (
    <div className="text-explain">
      {Object.entries(explanations).map(([feature, tokens]) => {
        const total = result.contributions[feature] ?? 0;
        const present = tokens.reduce((sum, t) => sum + (t.weight ?? 0), 0);
        return (
          <div key={feature} className="text-explain-block">
            <div className="text-explain-head">
              <strong>{featureLabel(scenario, feature)}</strong>
              <span className="text-explain-total">
                text total {probabilityPoints(total)} · <span className="text-explain-red">red</span> pushes toward “
                {positiveLabel(scenario)}”, <span className="text-explain-blue">blue</span> away
              </span>
            </div>
            <TokenHighlights text={String(record[feature] ?? "")} tokens={tokens} format={probabilityPoints} />
            <TopWords tokens={tokens} format={probabilityPoints} />
            {Math.abs(total - present) >= 0.005 && (
              <p className="text-explain-rest">
                Words <em>absent</em> from this text (vs. an average one) add {probabilityPoints(total - present)}.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** The deployed TF-IDF model and the transformer challenger, side by side. */
export function ModelDuel({ scenario, score }: { scenario: ScenarioSummary; score: TextScore }) {
  const challenger = scenario.text_challenger;
  const rows: { name: string; detail: string; value: number | null; note?: string }[] = [
    { name: "TF-IDF + LightGBM", detail: "deployed · bag of words", value: score.champion.prediction },
  ];
  if (challenger) {
    rows.push({
      name: `${challenger.label} + LightGBM`,
      detail: "challenger · sentence-transformer",
      value: score.challenger?.prediction ?? null,
      note: score.challenger ? undefined : (score.challengerError ?? "not requested"),
    });
  }
  return (
    <div className="model-duel">
      {rows.map((row) => (
        <div key={row.name} className="model-duel-row">
          <div className="model-duel-name">
            <strong>{row.name}</strong>
            <small>{row.detail}</small>
          </div>
          {row.value === null ? (
            <span className="model-duel-note">{row.note}</span>
          ) : (
            <>
              <div className="model-duel-bar">
                <span style={{ width: `${Math.round(row.value * 100)}%` }} className={row.value >= 0.5 ? "hot" : "cool"} />
              </div>
              <span className="model-duel-value">{(row.value * 100).toFixed(0)}%</span>
            </>
          )}
        </div>
      ))}
      <p className="model-duel-caption">Probability of “{positiveLabel(scenario)}”.</p>
    </div>
  );
}
