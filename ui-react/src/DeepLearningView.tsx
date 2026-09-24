import { useMemo, useState } from "react";
import type { DlModelInfo, ScenarioSummary, UiExtras } from "./apiClient";
import { Icon, type IconName } from "./Icon";
import { StatTile } from "./charts";
import { DlDataView } from "./DlDataView";
import { DlPredictView } from "./DlPredictView";
import { DlInsightsView } from "./DlInsightsView";
import { TriageBoardView } from "./TriageBoardView";
import { ReadingRoomView } from "./ReadingRoomView";
import { DlModelUnavailable, labelsOf, pct, useDlModel } from "./dlShared";
import "./deepLearning.css";

type Tab = "scenario" | "data" | "predict" | "insights" | "extra";

const EXTRA_TABS: Partial<Record<UiExtras["kind"], { icon: IconName; label: string }>> = {
  triage_board: { icon: "chat", label: "Triage Board" },
  reading_room: { icon: "scan", label: "Reading Room" },
};

/**
 * Generic `deep_learning` workspace (NLP or computer vision), driven entirely by the
 * scenario's `deep_learning` block and the deployed model's manifest from dl-inference
 * — the same "one renderer per kind, no per-scenario UI code" principle as
 * TabularView. Tabs: Scenario (what the model does, its data and model card), Data
 * (the published held-out samples — images or texts — and their class mix), Try the
 * model (live inference + explanation + similar cases), Model insights (learning
 * curves, held-out evaluation, calibration, selective prediction) and an optional 5th
 * tab from `ui_extras` (TriageBoardView / ReadingRoomView).
 */
export function DeepLearningView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [tab, setTab] = useState<Tab>("scenario");
  const model = useDlModel(scenario.slug, accessToken);
  const extra = scenario.ui_extras ? EXTRA_TABS[scenario.ui_extras.kind] : undefined;
  const isImage = scenario.deep_learning?.modality === "image";

  const needsModel = tab !== "scenario";
  return (
    <div className="workspace">
      <div className="workspace-tabs">
        <button className={tab === "scenario" ? "active" : ""} onClick={() => setTab("scenario")}>
          <Icon name="book" /> Scenario
        </button>
        <button className={tab === "data" ? "active" : ""} onClick={() => setTab("data")}>
          <Icon name="data" /> {isImage ? "Images" : "Texts"}
        </button>
        <button className={tab === "predict" ? "active" : ""} onClick={() => setTab("predict")}>
          <Icon name="target" /> Try the model
        </button>
        <button className={tab === "insights" ? "active" : ""} onClick={() => setTab("insights")}>
          <Icon name="scan" /> Model insights
        </button>
        {extra && (
          <button className={tab === "extra" ? "active" : ""} onClick={() => setTab("extra")}>
            <Icon name={extra.icon} /> {extra.label}
          </button>
        )}
      </div>

      {tab === "scenario" && <DlScenarioView scenario={scenario} model={model.data} />}
      {needsModel && model.loading && !model.data && <div className="app-loading">Loading the deployed model…</div>}
      {needsModel && model.error && <DlModelUnavailable error={model.error} />}
      {needsModel && model.data && (
        <>
          {tab === "data" && <DlDataView scenario={scenario} accessToken={accessToken} />}
          {tab === "predict" && <DlPredictView scenario={scenario} accessToken={accessToken} />}
          {tab === "insights" && <DlInsightsView scenario={scenario} model={model.data} />}
          {tab === "extra" && scenario.ui_extras?.kind === "triage_board" && (
            <TriageBoardView scenario={scenario} extra={scenario.ui_extras} model={model.data} accessToken={accessToken} />
          )}
          {tab === "extra" && scenario.ui_extras?.kind === "reading_room" && (
            <ReadingRoomView scenario={scenario} extra={scenario.ui_extras} model={model.data} accessToken={accessToken} />
          )}
        </>
      )}
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  return `${(seconds / 60).toFixed(1)} min`;
}

function DlScenarioView({ scenario, model }: { scenario: ScenarioSummary; model: DlModelInfo | null }) {
  const dl = scenario.deep_learning;
  const labels = labelsOf(scenario);
  const trainedOn = useMemo(() => {
    if (!model) return null;
    return model.device.kind === "cuda" ? `GPU · ${model.device.name}` : `CPU (reduced budget) · ${model.device.name}`;
  }, [model]);
  if (!dl) return null;
  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>
          {scenario.icon} {scenario.title}
        </h3>
        <p style={{ marginTop: "-0.2rem" }}>{scenario.description}</p>
        <div className="scenario-meta-row">
          <span className="scenario-meta-pill">Industry: Healthcare</span>
          <span className="scenario-meta-pill">{dl.modality === "text" ? "NLP · text classification" : "Computer vision · image classification"}</span>
          <span className="scenario-meta-pill">{labels.length} classes</span>
          <span className="scenario-meta-pill">Deep learning · Hugging Face</span>
        </div>
        {scenario.credits && (
          <p className="panel-hint" style={{ marginTop: "0.6rem" }}>
            Data & model credit: {scenario.credits.source} —{" "}
            <a href={scenario.credits.url} target="_blank" rel="noreferrer">
              original source
            </a>
            {scenario.credits.note ? ` (${scenario.credits.note})` : ""}
          </p>
        )}
        <p className="dl-disclaimer">
          Demonstration only — not a medical device and not clinically validated. Never use it for a real diagnosis.
        </p>
      </div>

      <div className="panel-card">
        <h3>Model card</h3>
        <div className="kpi-row">
          <StatTile label="Base model" value={dl.base_model.split("/")[1] ?? dl.base_model} sub={`${dl.base_model_params} parameters`} />
          <StatTile label="Input" value={dl.modality === "text" ? `≤ ${dl.max_length} tokens` : `${dl.image_size}×${dl.image_size} px`} sub={dl.input_label} />
          {model ? (
            <>
              <StatTile
                label="Held-out accuracy"
                value={pct(model.evaluation.metrics.accuracy)}
                sub={`macro-F1 ${model.evaluation.metrics.macro_f1.toFixed(3)} · n=${model.evaluation.n}`}
                highlight
              />
              <StatTile label="Trained on" value={model.device.kind === "cuda" ? "GPU" : "CPU"} sub={trainedOn ?? ""} color={model.device.kind === "cuda" ? "var(--green)" : "var(--amber)"} />
              <StatTile
                label="Served as"
                value={`ONNX${model.quantized ? " int8" : ""}`}
                sub={`${model.model_size_mb} MB · ~${model.latency_ms} ms/request on CPU`}
              />
              <StatTile label="Training time" value={formatDuration(model.training_seconds)} sub={`${model.history.length} epochs · ${model.train_size} examples`} />
            </>
          ) : (
            <StatTile label="Deployed model" value="—" sub="not trained / not deployed yet" />
          )}
        </div>
        <p className="panel-hint">
          Fine-tuned from{" "}
          <a href={`https://huggingface.co/${dl.base_model}`} target="_blank" rel="noreferrer">
            {dl.base_model}
          </a>{" "}
          (pinned revision <code>{dl.base_model_revision.slice(0, 7)}</code>), exported to ONNX and served by{" "}
          <code>dl-inference</code> with onnxruntime — no PyTorch in the serving path.
          {model && ` Last trained ${new Date(model.trained_at).toLocaleString()}.`}
        </p>
      </div>

      <div className="panel-card">
        <h3>What the model predicts</h3>
        <p style={{ marginTop: "-0.2rem" }}>
          <strong>{dl.target_label}</strong> — {dl.target_description}
        </p>
        <div className="table-scroll">
          <table className="data-table" style={{ marginTop: "0.5rem" }}>
            <thead>
              <tr>
                <th>Class</th>
                <th>Meaning</th>
              </tr>
            </thead>
            <tbody>
              {labels.map((l) => (
                <tr key={l.key}>
                  <td>{l.label}</td>
                  <td className="panel-hint">{l.description ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel-card">
        <h3>How each prediction is explained</h3>
        {dl.modality === "text" ? (
          <p>
            <strong>Word attributions (Banzhaf values).</strong> The model re-reads the message many times with random
            halves of the words hidden behind its <code>[MASK]</code> token; each word's score is its average effect on
            the predicted condition's log-odds. Red words argue for the condition, blue words against it. Plus the most
            similar training messages, by the model's own embedding.
          </p>
        ) : (
          <p>
            <strong>Occlusion heatmap.</strong> The X-ray is re-scored with each of {dl.occlusion_grid}×{dl.occlusion_grid}{" "}
            patches blanked out in turn; the brighter a region, the more the finding's log-odds drop without it — i.e.
            where the model is actually looking. Plus the most similar prior studies, by the model's own embedding.
          </p>
        )}
      </div>
    </div>
  );
}
