import { useCallback, useEffect, useState } from "react";
import {
  dlModelInfo,
  dlRuntime,
  getDeepLearningStatus,
  trainDeepLearningScenario,
  type DlClusterStatus,
  type DlModelInfo,
  type DlRuntime,
  type ScenarioSummary,
} from "./apiClient";
import { config } from "./config";
import { Icon } from "./Icon";
import { pct } from "./dlShared";
import "./deepLearning.css";

const POLL_SECONDS = 10;

/**
 * Admin-only "Deep Learning" tab of the Platform page — operations for the optional
 * deep_learning scenarios, in one place:
 *
 * - **GPU**: whether any k3s node advertises `nvidia.com/gpu` (data-platform-manager's
 *   GET /deep-learning/status). Without one, in-cluster training falls back to each
 *   scenario's reduced CPU budget — the page says so before the admin clicks anything,
 *   and points at `make dl-train` for the host's own GPU.
 * - **Models**: every deep_learning scenario's deployed model as dl-inference serves it
 *   (trained on which device, when, held-out accuracy, size/latency).
 * - **Train**: start (or restart) a scenario's in-cluster training Job, with its state
 *   polled while the page is open.
 */
export function DeepLearningAdmin({
  baseUrl,
  accessToken,
  scenarios,
}: {
  baseUrl: string;
  accessToken: string | null;
  scenarios: ScenarioSummary[];
}) {
  const dlScenarios = scenarios.filter((s) => s.kind === "deep_learning");
  const [cluster, setCluster] = useState<DlClusterStatus | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<DlRuntime | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [models, setModels] = useState<Record<string, DlModelInfo | string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    getDeepLearningStatus(baseUrl, accessToken)
      .then((s) => (setCluster(s), setClusterError(null)))
      .catch((e) => setClusterError((e as Error).message));
    dlRuntime(config.dlInferenceUrl, accessToken)
      .then((r) => (setRuntime(r), setRuntimeError(null)))
      .catch((e) => setRuntimeError((e as Error).message));
    dlScenarios.forEach((s) =>
      dlModelInfo(config.dlInferenceUrl, s.slug, accessToken)
        .then((m) => setModels((prev) => ({ ...prev, [s.slug]: m })))
        .catch((e) => setModels((prev) => ({ ...prev, [s.slug]: (e as Error).message }))),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, accessToken, dlScenarios.map((s) => s.slug).join(",")]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_SECONDS * 1000);
    return () => clearInterval(id);
  }, [load]);

  const gpus = cluster?.cluster_gpus ?? 0;
  const train = async (slug: string, title: string) => {
    if (
      gpus === 0 &&
      !window.confirm(
        `No GPU in this cluster — "${title}" will train on CPU with its reduced budget (minutes to tens of minutes, ` +
          `lower accuracy than a GPU run, ~2-4 GB RAM). For the full GPU run use \`make dl-train\` on a GPU host.\n\nTrain on CPU anyway?`,
      )
    )
      return;
    setBusy(slug);
    setNotice(null);
    try {
      const r = await trainDeepLearningScenario(baseUrl, slug, accessToken);
      setNotice(`Started ${r.job} (${r.gpu ? "GPU" : "CPU"}). dl-inference picks the new model up within a minute of it finishing.`);
      load();
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <p className="panel-hint">
        Deep-learning scenarios fine-tune Hugging Face models — minutes on a GPU, far longer on a CPU — so training never runs as part of{" "}
        <code>make all</code>. Train here (in-cluster Job) or on a GPU host with <code>make dl-train-nlp</code> / <code>make dl-train-cv</code>.
      </p>

      <div className="settings-grid">
        <div className={`panel-card settings-card platform-card platform-card--${gpus > 0 ? "up" : "degraded"}`}>
          <div className="settings-card-header">
            <h3>Cluster GPU</h3>
            <span className={`settings-badge ${gpus > 0 ? "settings-badge--on" : "settings-badge--partial"}`}>
              {cluster ? (gpus > 0 ? `${gpus} GPU${gpus > 1 ? "s" : ""}` : "no GPU") : "…"}
            </span>
          </div>
          {clusterError && <p className="error">{clusterError}</p>}
          {cluster && !cluster.available && <p className="settings-card-model">{cluster.reason}</p>}
          {cluster?.available && (
            <p className="settings-card-model">
              {gpus > 0
                ? `In-cluster training requests one nvidia.com/gpu (${cluster.gpu_nodes.filter((n) => n.gpus).map((n) => n.name).join(", ")}).`
                : `No node advertises nvidia.com/gpu (${cluster.gpu_nodes.map((n) => n.name).join(", ")}) — in-cluster training uses each scenario's CPU budget. A host GPU is used by make dl-train.`}
            </p>
          )}
        </div>
        <div className={`panel-card settings-card platform-card platform-card--${runtime ? "up" : "not_deployed"}`}>
          <div className="settings-card-header">
            <h3>dl-inference</h3>
            <span className={`settings-badge ${runtime ? "settings-badge--on" : "settings-badge--off"}`}>{runtime ? "up" : "not deployed"}</span>
          </div>
          {runtime ? (
            <p className="settings-card-model">
              onnxruntime {runtime.onnxruntime_version} on CPU · {runtime.threads_per_session} threads/session · {runtime.rss_mb} MB RSS ·{" "}
              {runtime.cached_models.length} model(s) loaded
            </p>
          ) : (
            <p className="settings-card-model">
              Optional overlay — deploy with <code>make k3s-dl-up</code>. {runtimeError && <span className="panel-hint">({runtimeError})</span>}
            </p>
          )}
        </div>
      </div>

      {notice && <p className="panel-hint dl-notice">{notice}</p>}

      <section className="platform-group">
        <h3>Deep-learning models</h3>
        <div className="settings-grid">
          {dlScenarios.map((s) => {
            const m = models[s.slug];
            const info = typeof m === "object" ? m : null;
            const job = cluster?.jobs.find((j) => j.scenario_slug === s.slug);
            const running = job?.state === "running";
            return (
              <div key={s.slug} className="panel-card settings-card">
                <div className="settings-card-header">
                  <h3>
                    {s.icon} {s.title}
                  </h3>
                  <span className={`settings-badge ${info ? "settings-badge--on" : "settings-badge--off"}`}>{info ? "deployed" : "no model"}</span>
                </div>
                {info ? (
                  <ul className="dl-admin-facts">
                    <li>
                      <strong>{info.base_model}</strong> · {info.base_model_params} params · ONNX{info.quantized ? " int8" : ""} {info.model_size_mb} MB
                    </li>
                    <li>
                      Trained on{" "}
                      <span className={info.device.kind === "cuda" ? "dl-ok" : "dl-warn"}>
                        {info.device.kind === "cuda" ? `GPU — ${info.device.name}` : `CPU (reduced budget) — ${info.device.name}`}
                      </span>{" "}
                      in {Math.round(info.training_seconds)} s · {new Date(info.trained_at).toLocaleString()}
                    </li>
                    <li>
                      Held-out accuracy {pct(info.evaluation.metrics.accuracy)} · macro-F1 {info.evaluation.metrics.macro_f1.toFixed(3)}
                      {info.evaluation.metrics.auroc !== undefined && ` · AUROC ${info.evaluation.metrics.auroc.toFixed(3)}`} · ~{info.latency_ms} ms/request
                    </li>
                  </ul>
                ) : (
                  <p className="settings-card-model">{typeof m === "string" ? m : "Loading…"}</p>
                )}
                <div className="dl-admin-actions">
                  <button className="btn-primary" disabled={!cluster?.available || running || busy === s.slug} onClick={() => train(s.slug, s.title)}>
                    <Icon name="sparkle" size={12} /> {running ? "Training…" : gpus > 0 ? "Train in cluster (GPU)" : "Train in cluster (CPU)"}
                  </button>
                  {job && job.state !== "not_run" && (
                    <span className={`settings-badge ${job.state === "succeeded" ? "settings-badge--on" : job.state === "failed" ? "settings-badge--fail" : "settings-badge--partial"}`}>
                      job {job.state}
                      {job.completed_at ? ` · ${new Date(job.completed_at).toLocaleTimeString()}` : ""}
                    </span>
                  )}
                </div>
                <p className="panel-hint" title={`Job logs: kubectl -n ai-circus logs job/${job?.job_name ?? `dl-training-${s.slug.replaceAll("_", "-")}`}`}>
                  Or on a GPU host: <span className="dl-inline-code">make dl-train SCENARIOS={s.slug}</span>
                </p>
              </div>
            );
          })}
          {dlScenarios.length === 0 && <p className="panel-hint">No deep_learning scenario is seeded.</p>}
        </div>
      </section>
    </>
  );
}
