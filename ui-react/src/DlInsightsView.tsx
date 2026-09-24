import type { DlModelInfo, ScenarioSummary } from "./apiClient";
import { BarList, CHART_COLORS, MultiLineChart, StatTile } from "./charts";
import { ConfusionMatrix, labelName, pct } from "./dlShared";

/**
 * Understanding the deployed model: how training went (learning curves, where/how it
 * was trained), how well the *exported* model does on the held-out test split
 * (headline metrics, per-class F1, confusion matrix), whether its confidence can be
 * trusted (reliability diagram / ECE), and what abstaining on low-confidence cases buys
 * (accuracy-vs-coverage) — plus the ROC curve for binary tasks. Everything comes from
 * dl-training's manifest; nothing is recomputed in the browser.
 */
export function DlInsightsView({ scenario, model }: { scenario: ScenarioSummary; model: DlModelInfo }) {
  const ev = model.evaluation;
  const binary = model.labels.length === 2;
  const epochs = model.history;
  const trainable = model.trainable_params === model.total_params ? "full fine-tune" : `top layers only (${pct(model.trainable_params / model.total_params, 0)} of weights)`;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>Held-out performance of the deployed model</h3>
        <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
          Measured on {ev.n} test cases the model never saw, through the exact ONNX{model.quantized ? " int8" : ""} artifact
          dl-inference serves — not the PyTorch model it came from.
        </p>
        <div className="kpi-row">
          <StatTile label="Accuracy" value={pct(ev.metrics.accuracy)} highlight />
          <StatTile label="Macro F1" value={ev.metrics.macro_f1.toFixed(3)} info="Mean of per-class F1 — every class counts equally, however rare." />
          {ev.metrics.auroc !== undefined && (
            <StatTile
              label="AUROC"
              value={ev.metrics.auroc.toFixed(3)}
              info={binary ? "Probability a random positive study is ranked above a random negative one." : "One-vs-rest, averaged over classes."}
            />
          )}
          <StatTile
            label="Calibration error"
            value={ev.metrics.ece.toFixed(3)}
            sub={
              ev.metrics.ece_uncalibrated !== undefined && model.temperature !== undefined
                ? `${ev.metrics.ece_uncalibrated.toFixed(3)} before temperature scaling (T=${model.temperature.toFixed(2)})`
                : undefined
            }
            info="Expected calibration error: average gap between the model's confidence and its actual accuracy (0 = perfectly calibrated). Probabilities are calibrated with label smoothing during training plus a temperature fitted on the validation split, so '80% sure' means right about 80% of the time."
          />
        </div>
      </div>

      <div className="dl-insights-grid">
        <div className="panel-card">
          <h3>Learning curves</h3>
          <MultiLineChart
            series={[
              { label: "train loss", color: CHART_COLORS.blue, points: epochs.map((e) => ({ x: e.epoch, y: e.train_loss })) },
              { label: "validation loss", color: CHART_COLORS.amber, points: epochs.map((e) => ({ x: e.epoch, y: e.val_loss })) },
            ]}
            xLabel="epoch"
            yLabel="cross-entropy"
            yMin={0}
            yFormatter={(v) => v.toFixed(2)}
          />
          <MultiLineChart
            series={[
              { label: "validation accuracy", color: CHART_COLORS.green, points: epochs.map((e) => ({ x: e.epoch, y: e.val_accuracy })) },
              { label: "validation macro-F1", color: CHART_COLORS.purple, points: epochs.map((e) => ({ x: e.epoch, y: e.val_macro_f1 })), dashed: true },
            ]}
            xLabel="epoch"
            yLabel="score"
            yFormatter={(v) => v.toFixed(2)}
          />
          <p className="panel-hint">
            Trained on <strong>{model.device.kind === "cuda" ? `GPU (${model.device.name})` : `CPU (${model.device.name})`}</strong> with the{" "}
            {model.budget_kind} budget: {model.budget.epochs} epochs, batch {model.budget.batch_size}, learning rate {model.budget.learning_rate},{" "}
            {trainable}, {model.train_size.toLocaleString()} of {model.train_size_available.toLocaleString()} training examples — best
            validation epoch kept. {Math.round(model.training_seconds)} s of training.
          </p>
        </div>

        <div className="panel-card">
          <h3>Per-class F1</h3>
          <BarList
            items={ev.per_class.map((c) => ({ label: labelName(scenario, c.key), value: c.f1 }))}
            signed={false}
            valueFormatter={(v) => v.toFixed(2)}
          />
        </div>

        <div className="panel-card dl-wide">
          <h3>Confusion matrix</h3>
          <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
            Rows are the expert label, columns the model's prediction — the diagonal is what it gets right.
          </p>
          <ConfusionMatrix scenario={scenario} matrix={ev.confusion_matrix} />
        </div>

        <div className="panel-card">
          <h3>Can its confidence be trusted?</h3>
          <MultiLineChart
            series={[
              { label: "perfect calibration", color: CHART_COLORS.dim, points: [{ x: 0, y: 0 }, { x: 1, y: 1 }], dashed: true },
              { label: "model", color: CHART_COLORS.green, points: ev.reliability.map((b) => ({ x: b.confidence, y: b.accuracy })) },
            ]}
            xLabel="stated confidence"
            yLabel="actual accuracy"
            yMin={0}
            yFormatter={(v) => pct(v, 0)}
          />
          <p className="panel-hint">Reliability diagram — points on the diagonal mean "90% sure" really is right 90% of the time.</p>
        </div>

        <div className="panel-card">
          <h3>Abstain when unsure</h3>
          <MultiLineChart
            series={[
              { label: "accuracy of kept cases", color: CHART_COLORS.green, points: ev.coverage_curve.map((p) => ({ x: p.threshold, y: p.accuracy })) },
              { label: "share of cases kept", color: CHART_COLORS.blue, points: ev.coverage_curve.map((p) => ({ x: p.threshold, y: p.coverage })), dashed: true },
            ]}
            xLabel="confidence threshold"
            yLabel="share"
            yMin={0}
            yFormatter={(v) => pct(v, 0)}
          />
          <p className="panel-hint">
            Raising the threshold routes low-confidence cases to a human: accuracy on what the model keeps rises as coverage falls.
          </p>
        </div>

        {ev.roc_curve && (
          <div className="panel-card">
            <h3>ROC curve</h3>
            <MultiLineChart
              series={[
                { label: "chance", color: CHART_COLORS.dim, points: [{ x: 0, y: 0 }, { x: 1, y: 1 }], dashed: true },
                { label: `model (AUROC ${ev.metrics.auroc?.toFixed(3)})`, color: CHART_COLORS.accent, points: ev.roc_curve.map((p) => ({ x: p.fpr, y: p.tpr })) },
              ]}
              xLabel="false-positive rate (1 − specificity)"
              yLabel="sensitivity"
              yMin={0}
              yFormatter={(v) => v.toFixed(1)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
