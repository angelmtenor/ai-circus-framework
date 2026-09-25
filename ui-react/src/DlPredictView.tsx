import { useCallback, useEffect, useState } from "react";
import { dlPredict, type DlPredictBody, type DlPrediction, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import {
  fileToBase64,
  HeatmapImage,
  HeatmapLegend,
  isAnomaly,
  labelName,
  labelsOf,
  MaskLegend,
  ProbabilityBars,
  SimilarCases,
  Thumbnail,
  TokenHighlights,
  TopWords,
  useDlImage,
  useDlSamples,
  probText,
} from "./dlShared";

type Input = { kind: "text"; text: string } | { kind: "sample"; id: string; text?: string } | { kind: "upload"; dataUrl: string; name: string };

/**
 * Live inference against dl-inference: the model's class probabilities, an
 * explanation for any class the user picks ("why X?" / "why not Y?") and the most
 * similar training cases — for free text or an uploaded image, as well as any
 * published held-out sample (whose true label is then shown alongside).
 */
export function DlPredictView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const dl = scenario.deep_learning!;
  const isImage = dl.modality === "image";
  const anomaly = isAnomaly(scenario);
  const [showMask, setShowMask] = useState(true);
  const samples = useDlSamples(scenario.slug, accessToken);
  const [input, setInput] = useState<Input | null>(null);
  const [draft, setDraft] = useState(dl.input_examples[0] ?? "");
  const [target, setTarget] = useState<string | null>(null);
  const [result, setResult] = useState<DlPrediction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [opacity, setOpacity] = useState(0.55);

  const run = useCallback(
    async (next: Input, explainTarget: string | null) => {
      setBusy(true);
      setError(null);
      const body: DlPredictBody = { explain: true, similar: isImage ? 4 : 3, ...(explainTarget ? { target: explainTarget } : {}) };
      if (next.kind === "text") body.text = next.text;
      else if (next.kind === "sample") body.sample_id = next.id;
      else body.image_base64 = next.dataUrl;
      try {
        setResult(await dlPredict(config.dlInferenceUrl, scenario.slug, body, accessToken));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [scenario.slug, accessToken, isImage],
  );

  const submit = (next: Input) => {
    setInput(next);
    setTarget(null);
    setResult(null);
    void run(next, null);
  };

  // Image scenarios open straight onto the first held-out study.
  useEffect(() => {
    if (isImage && !input && samples.data?.length) submit({ kind: "sample", id: samples.data[0].id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isImage, samples.data]);

  const sample = input?.kind === "sample" ? samples.data?.find((s) => s.id === input.id) : undefined;
  const sampleImage = useDlImage(scenario.slug, isImage && input?.kind === "sample" ? input.id : null, accessToken);
  const maskImage = useDlImage(scenario.slug, sample?.has_mask ? sample.id : null, accessToken, "masks");
  const shownImage =
    input?.kind === "upload" ? (result?.input_image_png ? `data:image/png;base64,${result.input_image_png}` : input.dataUrl) : sampleImage;
  const explainedLabel = result?.explained_class ? labelName(scenario, result.explained_class) : "";

  return (
    <div className="tab-panel dl-predict">
      <div className="panel-card">
        <h3>{isImage ? dl.input_label : "Describe the symptoms"}</h3>
        {isImage ? (
          <>
            <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
              Pick a held-out sample (its expert label is shown after the model's read) or upload your own image (PNG/JPEG —
              processed in memory, never stored).
            </p>
            <div className="dl-strip">
              {(samples.data ?? []).slice(0, 18).map((s) => (
                <button
                  key={s.id}
                  className={`dl-strip-item${input?.kind === "sample" && input.id === s.id ? " active" : ""}`}
                  onClick={() => submit({ kind: "sample", id: s.id })}
                  title={s.id}
                >
                  <Thumbnail slug={scenario.slug} sampleId={s.id} accessToken={accessToken} size={64} />
                </button>
              ))}
              <label className="dl-upload" title="Upload an image">
                <input
                  type="file"
                  accept="image/png,image/jpeg"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file) submit({ kind: "upload", dataUrl: await fileToBase64(file), name: file.name });
                    e.target.value = "";
                  }}
                />
                ⬆ Upload
              </label>
            </div>
          </>
        ) : (
          <>
            <textarea
              className="dl-textarea"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={4}
              maxLength={4000}
              placeholder="e.g. I've had a fever and a rash for two days…"
            />
            <div className="dl-toolbar">
              <button className="btn-primary" disabled={busy || !draft.trim()} onClick={() => submit({ kind: "text", text: draft.trim() })}>
                {busy ? "Analyzing…" : "Analyze"}
              </button>
              <span className="panel-hint">Try:</span>
              {dl.input_examples.map((ex, i) => (
                <button
                  key={i}
                  className="chip"
                  onClick={() => {
                    setDraft(ex);
                    submit({ kind: "text", text: ex });
                  }}
                  title={ex}
                >
                  {ex.slice(0, 38)}…
                </button>
              ))}
              {samples.data && samples.data.length > 0 && (
                <button
                  className="chip"
                  onClick={() => {
                    const s = samples.data![Math.floor(Math.random() * samples.data!.length)];
                    setDraft(s.text ?? "");
                    submit({ kind: "sample", id: s.id, text: s.text });
                  }}
                >
                  🎲 random held-out case
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="dl-result-grid">
          <div className="panel-card">
            <div className="dl-verdict">
              <div>
                <div className="kpi-label">{dl.target_label}</div>
                <div className="dl-verdict-label">{labelName(scenario, result.predicted)}</div>
                <div className="panel-hint">
                  {probText(result.confidence)} confidence · {result.latency_ms} ms on CPU
                  {sample && (
                    <>
                      {" "}
                      · expert label: <strong>{labelName(scenario, sample.label)}</strong>{" "}
                      {sample.label === result.predicted ? "✓" : "✗"}
                    </>
                  )}
                </div>
              </div>
            </div>
            <ProbabilityBars items={result.probabilities} highlight={result.predicted} limit={isImage ? 2 : 6} />
            <p className="panel-hint">{labelsOf(scenario).find((l) => l.key === result.predicted)?.description}</p>
          </div>

          <div className="panel-card">
            <div className="dl-explain-head">
              <h3>{anomaly ? "Where does it deviate from normal?" : `Why ${explainedLabel}?`}</h3>
              <label style={anomaly ? { display: "none" } : undefined}>
                Explain{" "}
                <select
                  value={target ?? result.predicted}
                  onChange={(e) => {
                    setTarget(e.target.value);
                    if (input) void run(input, e.target.value);
                  }}
                >
                  {[...result.probabilities]
                    .sort((a, b) => b.probability - a.probability)
                    .map((p) => (
                      <option key={p.key} value={p.key}>
                        {p.label} ({probText(p.probability)})
                      </option>
                    ))}
                </select>
              </label>
            </div>
            {result.explanation?.type === "tokens" && (
              <>
                <TokenHighlights
                  text={input?.kind === "text" ? input.text : (input?.kind === "sample" ? (sample?.text ?? input.text ?? "") : "")}
                  tokens={result.explanation.tokens}
                />
                <TopWords tokens={result.explanation.tokens} />
                <p className="panel-hint">
                  <span className="dl-swatch dl-swatch--for" /> evidence for {explainedLabel} ·{" "}
                  <span className="dl-swatch dl-swatch--against" /> evidence against — {result.explanation.method}.
                </p>
              </>
            )}
            {result.explanation?.type === "heatmap" && (
              <>
                <HeatmapImage
                  src={shownImage}
                  grid={result.explanation.grid}
                  vmax={result.explanation.vmax}
                  maskSrc={showMask ? maskImage : null}
                  opacity={opacity}
                />
                <div className="dl-toolbar">
                  <label>
                    Heatmap{" "}
                    <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
                  </label>
                  <HeatmapLegend {...(anomaly ? { low: "normal", high: "anomalous" } : {})} />
                  {sample?.has_mask && (
                    <label className="dl-check">
                      <input type="checkbox" checked={showMask} onChange={(e) => setShowMask(e.target.checked)} /> <MaskLegend />
                    </label>
                  )}
                </div>
                <p className="panel-hint">
                  {anomaly
                    ? `${result.explanation.method}. Fixed scale: no heat means every patch looks like a normal one; bright means unlike anything in the normal memory bank.`
                    : `${result.explanation.method}: regions whose removal most lowers the model's case for ${explainedLabel}.`}
                </p>
              </>
            )}
            {busy && <p className="panel-hint">Recomputing…</p>}
          </div>

          <div className="panel-card dl-similar-panel">
            <h3>{anomaly ? "Closest known-normal references" : isImage ? "Most similar training images" : "Most similar training cases"}</h3>
            <p className="panel-hint" style={{ marginTop: "-0.2rem" }}>
              {anomaly
                ? "The good training images that look most alike, by the backbone's own embedding — compare them side by side with the flagged region."
                : "Nearest neighbours in the model's own embedding space — example-based evidence a domain expert can sanity-check."}
            </p>
            <SimilarCases scenario={scenario} cases={result.similar} accessToken={accessToken} />
          </div>
        </div>
      )}
      {!result && busy && <div className="app-loading">Running the model…</div>}
    </div>
  );
}
