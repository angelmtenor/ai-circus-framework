import { useCallback, useEffect, useMemo, useState } from "react";
import { datasetSample, modelCard, predict, type DatasetSample, type ModelCard, type ScenarioSummary, type TutorialWidget } from "./apiClient";
import { config } from "./config";
import { BarList, StatTile } from "./charts";
import { PlotlyChart } from "./PlotlyChart";
import { buildChart, specToChartCardConfig, type ValueLabelFor } from "./chartBuilder";
import { renderMarkdown } from "./markdown";
import { explainByFeature, featureLabel } from "./predictUtils";
import { useTheme } from "./useTheme";
import { Icon } from "./Icon";
import "./tutorial.css";

const METRIC_NAMES: Record<string, string> = { roc_auc: "ROC AUC", accuracy: "Accuracy", r2: "R²" };
// training's CANDIDATE_ESTIMATORS keys, as a reader would name them.
const MODEL_NAMES: Record<string, string> = {
  logistic_regression: "Logistic regression",
  linear_regression: "Linear regression",
  lightgbm: "LightGBM",
  lightgbm_small_data: "LightGBM (small data)",
};
const modelName = (key: string) => MODEL_NAMES[key] ?? key.replace(/_/g, " ");
const POSITIVE_HEX = "#3f93eb";
const NEGATIVE_HEX = "#ea4f58";

type ExampleResult = { prediction: number; items: { feature: string; value: number }[] };

/**
 * Generic "Tutorial" tab for any tabular_ml scenario with a `tutorial:` block (see
 * libs/shared/scenario_schema.py's TutorialConfig): YAML chapters of markdown prose,
 * live charts (the same ChartSpec as default_charts, over the real dataset sample) and
 * live widgets backed by prediction's endpoints — the dataset sample, the model card
 * (GET /model/{slug}/card: leaderboard, leakage-free hold-out ROC/confusion matrix,
 * global SHAP) and /predict for the worked examples. No scenario-specific code.
 */
export function TutorialView({
  scenario,
  accessToken,
  onOpenExtra,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  onOpenExtra?: () => void;
}) {
  const tutorial = scenario.tutorial!;
  const [step, setStep] = useState(-1); // -1 = the intro page
  const [sample, setSample] = useState<DatasetSample | null>(null);
  const [card, setCard] = useState<ModelCard | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);
  const [examples, setExamples] = useState<ExampleResult[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    datasetSample(config.predictionUrl, scenario.slug, 5000, accessToken)
      .then((s) => !cancelled && setSample(s))
      .catch(() => undefined);
    modelCard(config.predictionUrl, scenario.slug, accessToken)
      .then((c) => !cancelled && setCard(c))
      .catch((e) => !cancelled && setCardError((e as Error).message));
    if (tutorial.examples.length > 0) {
      predict(
        config.predictionUrl,
        scenario.slug,
        tutorial.examples.map((e) => e.record),
        accessToken,
      )
        .then((r) => {
          if (cancelled) return;
          const features = scenario.feature_columns ?? [];
          setExamples(r.predictions.map((p) => ({ prediction: p.prediction, items: explainByFeature(features, p.prediction, p.contributions).items })));
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [scenario.slug, scenario.feature_columns, accessToken, tutorial.examples]);

  const total = tutorial.steps.length;
  const current = step >= 0 ? tutorial.steps[step] : null;
  const go = (next: number) => {
    setStep(Math.max(-1, Math.min(total - 1, next)));
    document.querySelector(".tut-main")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="tab-panel tut">
      <nav className="tut-rail" aria-label="Tutorial chapters">
        <button className={`tut-rail-item${step === -1 ? " active" : ""}`} onClick={() => go(-1)}>
          <span className="tut-rail-num">★</span> Introduction
        </button>
        {tutorial.steps.map((s, i) => (
          <button key={s.title} className={`tut-rail-item${step === i ? " active" : ""}${i < step ? " done" : ""}`} onClick={() => go(i)}>
            <span className="tut-rail-num">{i + 1}</span> {s.title.replace(/^\d+\s*·\s*/, "")}
          </button>
        ))}
        <div className="tut-progress" aria-hidden="true">
          <div style={{ width: `${((step + 1) / total) * 100}%` }} />
        </div>
      </nav>

      <div className="tut-main">
        {current === null ? (
          <div className="panel-card tut-hero">
            <div className="tut-hero-icon">{scenario.icon}</div>
            <h2>{scenario.title}</h2>
            <div className="tut-prose">{renderMarkdown(tutorial.intro)}</div>
            <div className="kpi-row">
              {sample && <StatTile label="Rows in the dataset" value={sample.total_rows.toLocaleString()} />}
              {card && <StatTile label="Model" value={modelName(card.model_name)} />}
              {card?.metrics.cv_roc_auc_mean !== undefined && (
                <StatTile label="Cross-validated ROC AUC" value={card.metrics.cv_roc_auc_mean.toFixed(3)} sub={`± ${card.metrics.cv_roc_auc_std?.toFixed(3)}`} highlight />
              )}
              {card?.metrics.holdout_roc_auc !== undefined && <StatTile label="Hold-out ROC AUC" value={card.metrics.holdout_roc_auc.toFixed(3)} />}
            </div>
            <div className="tut-hero-actions">
              <button className="btn-primary" onClick={() => go(0)}>
                Start the tutorial →
              </button>
              {onOpenExtra && (
                <button className="btn-secondary" onClick={onOpenExtra}>
                  Skip to the interactive view
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="panel-card tut-step">
            <div className="tut-step-kicker">
              Chapter {step + 1} of {total}
            </div>
            <h2>{current.title}</h2>
            <div className="tut-prose">{renderMarkdown(current.body)}</div>
            {current.takeaway && (
              <div className="tut-takeaway">
                <Icon name="sparkle" />
                <span>{current.takeaway}</span>
              </div>
            )}
            {current.charts.length > 0 && <TutorialCharts scenario={scenario} sample={sample} specs={current.charts} />}
            {current.widgets.map((widget) => (
              <Widget key={widget} widget={widget} scenario={scenario} sample={sample} card={card} cardError={cardError} examples={examples} />
            ))}
            <div className="tut-nav">
              <button className="btn-secondary" onClick={() => go(step - 1)}>
                ← {step === 0 ? "Introduction" : "Previous"}
              </button>
              {step < total - 1 ? (
                <button className="btn-primary" onClick={() => go(step + 1)}>
                  Next: {tutorial.steps[step + 1].title.replace(/^\d+\s*·\s*/, "")} →
                </button>
              ) : (
                onOpenExtra && (
                  <button className="btn-primary" onClick={onOpenExtra}>
                    Open the interactive view →
                  </button>
                )
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TutorialCharts({ scenario, sample, specs }: { scenario: ScenarioSummary; sample: DatasetSample | null; specs: ScenarioSummary["default_charts"] }) {
  const { theme } = useTheme();
  const target = scenario.target ?? null;
  const labelFor = useCallback(
    (f: string) => (f === target ? `${scenario.target_label ?? target}` : featureLabel(scenario, f)),
    [target, scenario],
  );
  const valueLabelFor = useCallback<ValueLabelFor>(
    (column, value) => (column === target ? (scenario.target_value_labels?.[value] ?? value) : value),
    [target, scenario.target_value_labels],
  );
  const configs = useMemo(() => (specs ?? []).map(specToChartCardConfig), [specs]);
  if (!sample) return <div className="app-loading">Loading data…</div>;
  return (
    <div className="tut-charts">
      {configs.map((cfg) => {
        const { data, layout } = buildChart(sample.rows, cfg, theme.categoryPalette, labelFor, valueLabelFor);
        const title =
          cfg.type === "bar" && cfg.y === target && cfg.agg === "mean"
            ? `${scenario.target_label ?? target} rate by ${labelFor(cfg.x).toLowerCase()}${cfg.colorBy && cfg.colorBy !== cfg.x ? ` and ${labelFor(cfg.colorBy).toLowerCase()}` : ""}`
            : cfg.type === "histogram"
              ? `${labelFor(cfg.x)} distribution${cfg.colorBy ? ` by ${labelFor(cfg.colorBy).toLowerCase()}` : ""}`
              : `${labelFor(cfg.y || cfg.x)}${cfg.x && cfg.y ? ` by ${labelFor(cfg.x).toLowerCase()}` : ""}`;
        return (
          <figure key={cfg.id} className="tut-chart">
            <figcaption>{title}</figcaption>
            <PlotlyChart data={data} layout={{ ...layout, ...(cfg.y === target && cfg.agg === "mean" ? { yaxis: { title: { text: "rate" }, tickformat: ".0%", range: [0, 1] } } : {}) }} height={280} />
          </figure>
        );
      })}
    </div>
  );
}

function Widget({
  widget,
  scenario,
  sample,
  card,
  cardError,
  examples,
}: {
  widget: TutorialWidget;
  scenario: ScenarioSummary;
  sample: DatasetSample | null;
  card: ModelCard | null;
  cardError: string | null;
  examples: ExampleResult[] | null;
}) {
  const needsCard = widget === "model_card" || widget === "roc_curve" || widget === "confusion_matrix" || widget === "feature_importance";
  if (needsCard && !card) {
    return cardError ? (
      <p className="error">The model isn't available yet ({cardError}) — train it first (make k3s-pipeline).</p>
    ) : (
      <div className="app-loading">Loading the model card…</div>
    );
  }
  if ((widget === "dataset_preview" || widget === "class_balance") && !sample) return <div className="app-loading">Loading data…</div>;
  switch (widget) {
    case "dataset_preview":
      return <DatasetPreview scenario={scenario} sample={sample!} />;
    case "class_balance":
      return <ClassBalance scenario={scenario} sample={sample!} />;
    case "model_card":
      return <ModelLeaderboard card={card!} />;
    case "roc_curve":
      return <RocCurve card={card!} />;
    case "confusion_matrix":
      return <ConfusionMatrix scenario={scenario} card={card!} />;
    case "feature_importance":
      return (
        <div className="tut-widget">
          <h4>Global importance — mean |SHAP| (probability points)</h4>
          <BarList
            items={card!.global_feature_importance.map((f) => ({ label: featureLabel(scenario, f.feature), value: f.importance * 100 }))}
            signed={false}
            valueFormatter={(v) => `${v.toFixed(1)} pts`}
          />
        </div>
      );
    case "examples":
      return <Examples scenario={scenario} results={examples} />;
    default:
      return null;
  }
}

function DatasetPreview({ scenario, sample }: { scenario: ScenarioSummary; sample: DatasetSample }) {
  const identity = new Set([sample.id_column, ...(sample.display_columns ?? [])].filter(Boolean) as string[]);
  return (
    <div className="tut-widget">
      <h4>
        First rows of {sample.total_rows.toLocaleString()} — model-ready
      </h4>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {sample.columns.map((c) => (
                <th key={c}>{c === scenario.target ? `${scenario.target_label ?? c} (target)` : featureLabel(scenario, c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sample.rows.slice(0, 8).map((row, i) => (
              <tr key={i}>
                {sample.columns.map((c) => (
                  <td key={c} className={identity.has(c) ? "data-table-id" : undefined}>
                    {c === scenario.target ? (scenario.target_value_labels?.[String(row[c])] ?? String(row[c])) : String(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ClassBalance({ scenario, sample }: { scenario: ScenarioSummary; sample: DatasetSample }) {
  const target = scenario.target ?? "";
  const n = sample.rows.length;
  const positives = sample.rows.filter((r) => Number(r[target]) === 1).length;
  const rate = n ? positives / n : 0;
  const positive = scenario.target_value_labels?.["1"] ?? "1";
  const negative = scenario.target_value_labels?.["0"] ?? "0";
  return (
    <div className="tut-widget">
      <h4>Class balance of the target</h4>
      <div className="tut-balance" role="img" aria-label={`${positive} ${(rate * 100).toFixed(1)}%, ${negative} ${((1 - rate) * 100).toFixed(1)}%`}>
        <div className="tut-balance-neg" style={{ width: `${(1 - rate) * 100}%` }}>
          {negative} · {n - positives} ({((1 - rate) * 100).toFixed(0)}%)
        </div>
        <div className="tut-balance-pos" style={{ width: `${rate * 100}%` }}>
          {positive} · {positives} ({(rate * 100).toFixed(0)}%)
        </div>
      </div>
      <p className="panel-hint">
        Baseline to beat: always answering “{rate < 0.5 ? negative : positive}” is already {(Math.max(rate, 1 - rate) * 100).toFixed(1)}% accurate.
      </p>
    </div>
  );
}

function ModelLeaderboard({ card }: { card: ModelCard }) {
  const metric = card.selection_metric ?? "accuracy";
  const name = METRIC_NAMES[metric] ?? metric;
  const cv = card.cv_folds && card.cv_folds >= 2;
  return (
    <div className="tut-widget">
      <h4>Model selection — the real leaderboard from training</h4>
      <div className="table-scroll">
        <table className="data-table tut-leaderboard">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>{cv ? `${card.cv_folds}-fold CV ${name}` : `Hold-out ${name}`}</th>
              <th>Hold-out ROC AUC</th>
              <th>Hold-out accuracy</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {card.candidates.map((c) => (
              <tr key={c.name} className={c.name === card.model_name ? "tut-winner" : undefined}>
                <td>{modelName(c.name)}</td>
                <td>
                  <strong>{c.selection_score.toFixed(3)}</strong>
                  {cv && c.metrics[`cv_${metric}_std`] !== undefined && <span className="panel-hint"> ± {c.metrics[`cv_${metric}_std`].toFixed(3)}</span>}
                </td>
                <td>{c.metrics.holdout_roc_auc?.toFixed(3) ?? "—"}</td>
                <td>{c.metrics.holdout_accuracy !== undefined ? `${(c.metrics.holdout_accuracy * 100).toFixed(1)}%` : "—"}</td>
                <td>{c.name === card.model_name && <span className="tut-badge">✓ deployed</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="panel-hint">
        {cv ? `Ranked on the mean of ${card.cv_folds} cross-validation folds over ${card.training_rows} training rows; ` : `Ranked on ${card.holdout_rows} hold-out rows; `}
        a more complex candidate must win by more than {card.accuracy_gain_threshold_for_complexity ?? "—"} {name} to be adopted (Green Code). The
        {` ${card.holdout_rows ?? "held-out"}`} hold-out rows were never used to choose. The winner was then refit on all rows for deployment.
      </p>
    </div>
  );
}

function RocCurve({ card }: { card: ModelCard }) {
  const evaluation = card.holdout_evaluation;
  if (!evaluation) return <p className="panel-hint">No hold-out ROC curve recorded for this model (retrain to add it).</p>;
  const { fpr, tpr } = evaluation.roc_curve;
  const size = 300;
  const pad = 36;
  const inner = size - pad - 12;
  const px = (v: number) => pad + v * inner;
  const py = (v: number) => 12 + (1 - v) * inner;
  const path = fpr.map((x, i) => `${i === 0 ? "M" : "L"} ${px(x).toFixed(1)} ${py(tpr[i]).toFixed(1)}`).join(" ");
  const area = `${path} L ${px(1)} ${py(0)} L ${px(0)} ${py(0)} Z`;
  const auc = card.metrics.holdout_roc_auc;
  return (
    <div className="tut-widget tut-roc">
      <h4>ROC curve — {evaluation.n} hold-out rows</h4>
      <div className="tut-roc-body">
        <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label={`ROC curve, area ${auc?.toFixed(3)}`}>
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <g key={t}>
              <line x1={px(t)} x2={px(t)} y1={py(0)} y2={py(1)} className="tut-grid" />
              <line x1={px(0)} x2={px(1)} y1={py(t)} y2={py(t)} className="tut-grid" />
              <text x={px(t)} y={py(0) + 14} textAnchor="middle" className="tut-tick">
                {t}
              </text>
              <text x={px(0) - 6} y={py(t) + 3} textAnchor="end" className="tut-tick">
                {t}
              </text>
            </g>
          ))}
          <line x1={px(0)} y1={py(0)} x2={px(1)} y2={py(1)} className="tut-diagonal" />
          <path d={area} className="tut-roc-area" />
          <path d={path} className="tut-roc-line" />
          <text x={px(0.55)} y={py(0.3)} className="tut-roc-auc">
            AUC {auc?.toFixed(3)}
          </text>
          <text x={px(0.5)} y={size - 2} textAnchor="middle" className="tut-axis">
            false-positive rate
          </text>
          <text x={10} y={py(0.5)} textAnchor="middle" transform={`rotate(-90 10 ${py(0.5)})`} className="tut-axis">
            true-positive rate
          </text>
        </svg>
        <p className="panel-hint">
          Each point is one possible cut-off. Bottom-left: flag nobody; top-right: flag everybody. The dashed diagonal is a coin flip (AUC 0.5); the closer
          the curve hugs the top-left corner, the better the model separates the two classes.
        </p>
      </div>
    </div>
  );
}

function ConfusionMatrix({ scenario, card }: { scenario: ScenarioSummary; card: ModelCard }) {
  const evaluation = card.holdout_evaluation;
  if (!evaluation) return null;
  const { tn, fp, fn, tp } = evaluation.confusion_matrix;
  const positive = scenario.target_value_labels?.["1"] ?? "positive";
  const negative = scenario.target_value_labels?.["0"] ?? "negative";
  const precision = tp / Math.max(1, tp + fp);
  const recall = tp / Math.max(1, tp + fn);
  const accuracy = (tp + tn) / Math.max(1, tp + tn + fp + fn);
  const cell = (value: number, correct: boolean, text: string) => (
    <div className={`tut-cm-cell ${correct ? "tut-cm-cell--ok" : "tut-cm-cell--err"}`}>
      <strong>{value}</strong>
      <span>{text}</span>
    </div>
  );
  return (
    <div className="tut-widget">
      <h4>Confusion matrix at a {evaluation.threshold * 100}% cut-off</h4>
      <div className="tut-cm">
        <div />
        <div className="tut-cm-head">predicted {negative.toLowerCase()}</div>
        <div className="tut-cm-head">predicted {positive.toLowerCase()}</div>
        <div className="tut-cm-side">really {negative.toLowerCase()}</div>
        {cell(tn, true, "true negatives")}
        {cell(fp, false, "false positives")}
        <div className="tut-cm-side">really {positive.toLowerCase()}</div>
        {cell(fn, false, "false negatives")}
        {cell(tp, true, "true positives")}
      </div>
      <div className="kpi-row">
        <StatTile label="Accuracy" value={`${(accuracy * 100).toFixed(1)}%`} />
        <StatTile label="Precision" value={`${(precision * 100).toFixed(1)}%`} sub={`of those called “${positive.toLowerCase()}”`} />
        <StatTile label="Recall" value={`${(recall * 100).toFixed(1)}%`} sub={`of the really “${positive.toLowerCase()}”`} />
      </div>
    </div>
  );
}

function Examples({ scenario, results }: { scenario: ScenarioSummary; results: ExampleResult[] | null }) {
  const examples = scenario.tutorial?.examples ?? [];
  const positive = scenario.target_value_labels?.["1"] ?? "positive";
  if (!results) return <div className="app-loading">Asking the model…</div>;
  return (
    <div className="tut-widget">
      <h4>Scored live by the deployed model</h4>
      <div className="tut-examples">
        {examples.map((example, i) => {
          const r = results[i];
          return (
            <div key={example.label} className="tut-example">
              <div className="tut-example-head">
                <strong>{example.label}</strong>
                <span className="tut-example-prob" style={{ color: r.prediction >= 0.5 ? POSITIVE_HEX : NEGATIVE_HEX }}>
                  {(r.prediction * 100).toFixed(0)}%
                </span>
              </div>
              <div className="tut-example-meter" aria-hidden="true">
                <div style={{ width: `${r.prediction * 100}%`, background: r.prediction >= 0.5 ? POSITIVE_HEX : NEGATIVE_HEX }} />
              </div>
              <span className="panel-hint">probability of “{positive.toLowerCase()}” — mainly:</span>
              <ul>
                {r.items.slice(0, 3).map((item) => (
                  <li key={item.feature}>
                    {featureLabel(scenario, item.feature)} = {String(example.record[item.feature])}{" "}
                    <span className={item.value >= 0 ? "tut-up" : "tut-down"}>
                      {item.value >= 0 ? "+" : "−"}
                      {Math.abs(item.value * 100).toFixed(1)} pts
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}
