import { useEffect, useMemo, useRef, useState } from "react";
import {
  dlImageBlob,
  dlModelInfo,
  dlPredict,
  dlSamples,
  type DlModelInfo,
  type DlSample,
  type DlSimilarCase,
  type DlTokenWeight,
  type ScenarioSummary,
} from "./apiClient";
import { config } from "./config";

/**
 * Shared pieces of the generic deep_learning workspace (DeepLearningView.tsx and its
 * tabs): data hooks against dl-inference plus the explanation renderers — word
 * highlights for text, an occlusion heatmap over the exact pixels the model saw for
 * images, probability bars, similar-case cards and a confusion matrix. Nothing here is
 * scenario-specific: labels, modality and grid size all come from the scenario's
 * `deep_learning` block or the deployed model's manifest.
 */

export type LabelInfo = { key: string; label: string; description?: string | null };

/** task=anomaly_detection: learned from normal samples only; the explanation is the
 * detector's own anomaly map on a fixed scale (no heat = normal). */
export function isAnomaly(scenario: ScenarioSummary): boolean {
  return scenario.deep_learning?.task === "anomaly_detection";
}

export function labelsOf(scenario: ScenarioSummary): LabelInfo[] {
  return scenario.deep_learning?.labels ?? [];
}

export function labelName(scenario: ScenarioSummary, key: string): string {
  return labelsOf(scenario).find((l) => l.key === key)?.label ?? key;
}

export function pct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

/** A model probability for display: one decimal, and never a bare "100%"/"0%" — even a
 * calibrated model is never certain, and rounding 99.96% up to 100% reads as if it were. */
export function probText(p: number): string {
  if (p >= 0.999) return ">99.9%";
  if (p <= 0.001) return "<0.1%";
  return `${(p * 100).toFixed(1)}%`;
}

/** Top class of a probability vector, by label key. */
export function topOf(scenario: ScenarioSummary, probs: number[]): { key: string; confidence: number; index: number } {
  let index = 0;
  probs.forEach((p, i) => {
    if (p > probs[index]) index = i;
  });
  return { key: labelsOf(scenario)[index]?.key ?? String(index), confidence: probs[index] ?? 0, index };
}

// ── data hooks ────────────────────────────────────────────────────────────────

type Loadable<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };

function useLoad<T>(load: () => Promise<T>, deps: unknown[]): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e) => !cancelled && setError((e as Error).message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);
  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}

export function useDlModel(slug: string, accessToken: string | null): Loadable<DlModelInfo> {
  return useLoad(() => dlModelInfo(config.dlInferenceUrl, slug, accessToken), [slug, accessToken]);
}

export function useDlSamples(slug: string, accessToken: string | null): Loadable<DlSample[]> {
  return useLoad(async () => (await dlSamples(config.dlInferenceUrl, slug, accessToken)).samples, [slug, accessToken]);
}

// Module-level: an image is fetched once per session, however many tabs show it.
const imageUrls = new Map<string, Promise<string>>();

/** A published sample's image — or, with kind="masks", its ground-truth defect mask
 * (only for samples flagged `has_mask`) — as an object URL. */
export function useDlImage(
  slug: string,
  sampleId: string | null,
  accessToken: string | null,
  kind: "images" | "masks" = "images",
): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!sampleId) {
      setUrl(null);
      return;
    }
    const key = `${slug}/${kind}/${sampleId}`;
    if (!imageUrls.has(key)) {
      const pending = dlImageBlob(config.dlInferenceUrl, slug, sampleId, accessToken, kind).then((b) => URL.createObjectURL(b));
      pending.catch(() => imageUrls.delete(key));
      imageUrls.set(key, pending);
    }
    let cancelled = false;
    imageUrls
      .get(key)!
      .then((u) => !cancelled && setUrl(u))
      .catch(() => !cancelled && setUrl(null));
    return () => {
      cancelled = true;
    };
  }, [slug, sampleId, accessToken, kind]);
  return url;
}

/** A readable "model not deployed/trained yet" state instead of a raw fetch error. */
export function DlModelUnavailable({ error }: { error: string }) {
  const notTrained = error.includes("503") || error.includes("No trained");
  const unreachable = error.includes("Failed to fetch") || error.includes("NetworkError");
  return (
    <div className="panel-card dl-unavailable">
      <h3>{unreachable ? "Deep-learning service not deployed" : notTrained ? "Model not trained yet" : "Model unavailable"}</h3>
      {unreachable && (
        <p>
          The optional <code>dl-inference</code> service isn't reachable. Deploy it with <code>make k3s-dl-up</code> (or{" "}
          <code>make k3s-all-dl</code> on a fresh cluster).
        </p>
      )}
      {notTrained && (
        <p>
          Train it on a GPU host with <code>make dl-train</code>, or ask an admin to use <strong>Platform → Deep Learning →
          Train in cluster</strong>.
        </p>
      )}
      <p className="panel-hint">{error}</p>
    </div>
  );
}

// ── renderers ─────────────────────────────────────────────────────────────────

export function ProbabilityBars({
  items,
  highlight,
  limit = 6,
}: {
  items: { key: string; label: string; probability: number }[];
  highlight?: string | null;
  limit?: number;
}) {
  const sorted = [...items].sort((a, b) => b.probability - a.probability).slice(0, limit);
  return (
    <div className="dl-prob-bars">
      {sorted.map((item, i) => (
        <div key={item.key} className={`dl-prob-row${item.key === highlight ? " dl-prob-row--highlight" : ""}`}>
          <span className="dl-prob-label" title={item.label}>
            {item.label}
          </span>
          <span className="dl-prob-track">
            <span
              className="dl-prob-fill"
              style={{ width: `${Math.max(item.probability * 100, 0.5)}%`, opacity: i === 0 ? 1 : 0.55 }}
            />
          </span>
          <span className="dl-prob-value">{probText(item.probability)}</span>
        </div>
      ))}
    </div>
  );
}

/** Words colored by their attribution: warm = evidence FOR the explained class,
 * cool = evidence against, intensity relative to the strongest word. */
export function TokenHighlights({ text, tokens }: { text: string; tokens: DlTokenWeight[] }) {
  const max = Math.max(1e-9, ...tokens.map((t) => Math.abs(t.weight ?? 0)));
  const parts: React.ReactNode[] = [];
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
        title={t.weight === null ? "Beyond the model's input length" : `${w >= 0 ? "+" : ""}${w.toFixed(3)} log-odds`}
      >
        {text.slice(t.start, t.end)}
      </span>,
    );
    cursor = t.end;
  });
  if (cursor < text.length) parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <p className="dl-token-text">{parts}</p>;
}

export function TopWords({ tokens, limit = 8 }: { tokens: DlTokenWeight[]; limit?: number }) {
  const ranked = tokens
    .filter((t) => t.weight !== null && /\w/.test(t.text))
    .sort((a, b) => Math.abs(b.weight ?? 0) - Math.abs(a.weight ?? 0))
    .slice(0, limit);
  return (
    <div className="dl-top-words">
      {ranked.map((t, i) => (
        <span key={i} className={`dl-word-chip ${(t.weight ?? 0) >= 0 ? "dl-word-chip--for" : "dl-word-chip--against"}`}>
          {t.text} <small>{(t.weight ?? 0) >= 0 ? "+" : ""}{(t.weight ?? 0).toFixed(2)}</small>
        </span>
      ))}
    </div>
  );
}

// Perceptual-ish "inferno"-like ramp for evidence FOR the class (transparent at 0).
function heatColor(v: number): [number, number, number, number] {
  const t = Math.min(1, Math.max(0, v));
  const stops: [number, number, number][] = [
    [40, 11, 84],
    [187, 55, 84],
    [249, 142, 9],
    [252, 255, 164],
  ];
  const x = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const c = stops[i].map((a, k) => Math.round(a + (stops[i + 1][k] - a) * f));
  // No tint below 20% of the peak evidence: only real hotspots are colored, so the
  // radiograph underneath stays crisp everywhere else.
  const alpha = t < 0.2 ? 0 : Math.min(1, (t - 0.2) * 1.6);
  return [c[0], c[1], c[2], Math.round(255 * alpha)];
}

/**
 * An image (X-ray, part photo…) with its explanation heatmap over it, upsampled
 * smoothly from the N x N grid. Only positive evidence (regions whose removal lowers the
 * explained class's log-odds, or an anomaly map's abnormal patches) is painted —
 * relative to the grid's own peak, or to a fixed `vmax` (anomaly maps: no heat means
 * normal). `maskSrc` outlines a ground-truth defect mask on top. brightness/contrast =
 * the "window/level" a radiograph reader adjusts; `zoom` scales from the center.
 */
export function HeatmapImage({
  src,
  grid,
  opacity,
  vmax,
  maskSrc = null,
  brightness = 1,
  contrast = 1,
  zoom = 1,
  invert = false,
  size = 360,
}: {
  src: string | null;
  grid: number[][] | null;
  opacity: number;
  vmax?: number;
  maskSrc?: string | null;
  brightness?: number;
  contrast?: number;
  zoom?: number;
  invert?: boolean;
  size?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const maskRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!grid || grid.length === 0) return;
    const n = grid.length;
    const max = vmax ?? Math.max(1e-9, ...grid.flat());
    // Fixed-scale (anomaly) maps: a square-root display gamma, so a subtle defect a few
    // patches wide (peaking at ~half the scale) is still clearly visible; 0 stays 0.
    const gamma = vmax !== undefined ? 0.5 : 1;
    const small = document.createElement("canvas");
    small.width = n;
    small.height = n;
    const sctx = small.getContext("2d")!;
    const img = sctx.createImageData(n, n);
    grid.forEach((row, r) =>
      row.forEach((v, c) => {
        const [R, G, B, A] = heatColor(Math.min(1, Math.max(0, v) / max) ** gamma);
        const o = (r * n + c) * 4;
        img.data.set([R, G, B, A], o);
      }),
    );
    sctx.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(small, 0, 0, canvas.width, canvas.height);
  }, [grid, vmax]);

  useEffect(() => {
    const canvas = maskRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!maskSrc) return;
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      drawMaskOutline(ctx, img, canvas.width);
    };
    img.src = maskSrc;
    return () => {
      cancelled = true;
    };
  }, [maskSrc]);

  const filter = `brightness(${brightness}) contrast(${contrast})${invert ? " invert(1)" : ""}`;
  return (
    <div className="dl-heatmap" style={{ width: size, height: size }}>
      <div className="dl-heatmap-inner" style={{ transform: `scale(${zoom})` }}>
        {src ? <img src={src} alt="Model input" style={{ filter }} draggable={false} /> : <div className="dl-image-placeholder" />}
        <canvas ref={canvasRef} width={size} height={size} style={{ opacity }} />
        <canvas ref={maskRef} width={size} height={size} />
      </div>
    </div>
  );
}

/** Paint a 2-px outline of the white region of a binary mask image — the ground truth
 * stays readable on top of the heatmap instead of hiding it. */
function drawMaskOutline(ctx: CanvasRenderingContext2D, mask: HTMLImageElement, size: number) {
  const scratch = document.createElement("canvas");
  scratch.width = size;
  scratch.height = size;
  const sctx = scratch.getContext("2d")!;
  sctx.drawImage(mask, 0, 0, size, size);
  const src = sctx.getImageData(0, 0, size, size).data;
  const inside = (x: number, y: number) => x >= 0 && y >= 0 && x < size && y < size && src[(y * size + x) * 4] > 127;
  const out = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (!inside(x, y)) continue;
      const edge = [-2, -1, 1, 2].some((d) => !inside(x + d, y) || !inside(x, y + d));
      if (edge) out.data.set([34, 211, 238, 255], (y * size + x) * 4);
    }
  }
  ctx.putImageData(out, 0, 0);
}

export function HeatmapLegend({ low = "less", high = "more evidence" }: { low?: string; high?: string }) {
  return (
    <div className="dl-heat-legend">
      <span>{low}</span>
      <span className="dl-heat-legend-ramp" />
      <span>{high}</span>
    </div>
  );
}

/** Legend for the ground-truth outline drawn by HeatmapImage's `maskSrc`. */
export function MaskLegend() {
  return (
    <span className="dl-heat-legend">
      <span className="dl-mask-swatch" /> ground-truth defect
    </span>
  );
}

export function Thumbnail({
  slug,
  sampleId,
  accessToken,
  size = 72,
  onClick,
}: {
  slug: string;
  sampleId: string;
  accessToken: string | null;
  size?: number;
  onClick?: () => void;
}) {
  const src = useDlImage(slug, sampleId, accessToken);
  const zoomable = onClick ? " dl-thumb--zoomable" : "";
  return src ? (
    <img
      className={`dl-thumb${zoomable}`}
      src={src}
      alt={sampleId}
      width={size}
      height={size}
      loading="lazy"
      onClick={onClick}
      title={onClick ? "Click to enlarge" : undefined}
    />
  ) : (
    <span className="dl-thumb dl-image-placeholder" style={{ width: size, height: size }} />
  );
}

export function SimilarCases({
  scenario,
  cases,
  accessToken,
}: {
  scenario: ScenarioSummary;
  cases: DlSimilarCase[];
  accessToken: string | null;
}) {
  const isImage = scenario.deep_learning?.modality === "image";
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className={isImage ? "dl-similar-grid" : "dl-similar-list"}>
      {open !== null && (
        <ImageLightbox
          scenario={scenario}
          items={cases.map((c) => ({ id: c.id, label: c.label }))}
          index={open}
          onIndex={setOpen}
          onClose={() => setOpen(null)}
          accessToken={accessToken}
        />
      )}
      {cases.map((c, i) => (
        <div key={c.id} className="dl-similar-card">
          {isImage && <Thumbnail slug={scenario.slug} sampleId={c.id} accessToken={accessToken} size={88} onClick={() => setOpen(i)} />}
          <div>
            <span className="scenario-meta-pill">{labelName(scenario, c.label)}</span>{" "}
            <span className="panel-hint">{pct(c.similarity, 0)} similar</span>
            {c.text && <p className="dl-similar-text">“{c.text}”</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function ConfusionMatrix({ scenario, matrix }: { scenario: ScenarioSummary; matrix: number[][] }) {
  const labels = labelsOf(scenario);
  const max = Math.max(1, ...matrix.flat());
  const compact = labels.length > 6;
  return (
    <div className="table-scroll">
      <table className={`dl-confusion${compact ? " dl-confusion--compact" : ""}`}>
        <thead>
          <tr>
            <th className="dl-confusion-corner">true ↓ / predicted →</th>
            {labels.map((l) => (
              <th key={l.key} title={l.label}>
                <span>{compact ? l.label.slice(0, 10) : l.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, r) => (
            <tr key={r}>
              <th title={labels[r]?.label}>{labels[r]?.label}</th>
              {row.map((v, c) => (
                <td
                  key={c}
                  style={{
                    background: v
                      ? `color-mix(in srgb, ${r === c ? "var(--green)" : "var(--red)"} ${Math.round(15 + (v / max) * 70)}%, transparent)`
                      : undefined,
                  }}
                  title={`${labels[r]?.label} → ${labels[c]?.label}: ${v}`}
                >
                  {v || ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** `n` items with a stable pseudo-random order (seeded), so a replayed "stream" is the
 * same every visit without looking sorted by id/label. */
export function useSeededOrder<T>(items: T[] | null, seed: number): T[] {
  return useMemo(() => {
    if (!items) return [];
    const out = [...items];
    let s = seed;
    for (let i = out.length - 1; i > 0; i--) {
      s = (s * 1103515245 + 12345) % 2147483648;
      const j = s % (i + 1);
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  }, [items, seed]);
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// ── lightbox ──────────────────────────────────────────────────────────────────

export type LightboxItem = { id: string; label: string; probs?: number[]; has_mask?: boolean };

/**
 * Full-screen viewer for published images (gallery / similar cases): large image,
 * expert label vs. the model's own prediction, ←/→ (or the arrows) to page through the
 * list, Esc / click-outside to close, and an optional AI heatmap for held-out samples
 * (reference "r-" images are training data — dl-inference only explains "s-" ones).
 */
export function ImageLightbox({
  scenario,
  items,
  index,
  onIndex,
  onClose,
  accessToken,
}: {
  scenario: ScenarioSummary;
  items: LightboxItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
  accessToken: string | null;
}) {
  const item = items[index];
  const src = useDlImage(scenario.slug, item?.id ?? null, accessToken);
  const [showHeat, setShowHeat] = useState(false);
  const [showMask, setShowMask] = useState(false);
  const maskSrc = useDlImage(scenario.slug, showMask && item?.has_mask ? item.id : null, accessToken, "masks");
  const [grid, setGrid] = useState<{ grid: number[][]; vmax?: number } | null>(null);
  const [explaining, setExplaining] = useState(false);
  const explainable = item?.id.startsWith("s-") ?? false;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && index < items.length - 1) onIndex(index + 1);
      if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, items.length, onIndex, onClose]);

  useEffect(() => {
    setGrid(null);
    if (!showHeat || !explainable || !item) return;
    let cancelled = false;
    setExplaining(true);
    dlPredict(config.dlInferenceUrl, scenario.slug, { sample_id: item.id, similar: 0 }, accessToken)
      .then((r) => !cancelled && r.explanation?.type === "heatmap" && setGrid({ grid: r.explanation.grid, vmax: r.explanation.vmax }))
      .catch(() => undefined)
      .finally(() => !cancelled && setExplaining(false));
    return () => {
      cancelled = true;
    };
  }, [showHeat, explainable, item, scenario.slug, accessToken]);

  if (!item) return null;
  const top = item.probs ? topOf(scenario, item.probs) : null;
  const size = Math.max(260, Math.min(680, Math.round(window.innerHeight * 0.72), window.innerWidth - 120));
  return (
    <div className="dl-lightbox" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="dl-lightbox-body" onClick={(e) => e.stopPropagation()}>
        <div className="dl-lightbox-head">
          <span>
            <strong>{labelName(scenario, item.label)}</strong> <span className="panel-hint">expert label</span>
            {top && (
              <>
                {" · "}AI: <strong className={top.key === item.label ? "dl-ok" : "dl-bad"}>{labelName(scenario, top.key)}</strong>{" "}
                <span className="panel-hint">{probText(top.confidence)}</span>
              </>
            )}
          </span>
          <span className="panel-hint">
            {index + 1} / {items.length}
          </span>
          <button className="btn-secondary" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="dl-lightbox-stage">
          <button className="dl-lightbox-nav" disabled={index === 0} onClick={() => onIndex(index - 1)} aria-label="Previous">
            ‹
          </button>
          <HeatmapImage
            src={src}
            grid={showHeat ? (grid?.grid ?? null) : null}
            vmax={grid?.vmax}
            maskSrc={item.has_mask ? maskSrc : null}
            opacity={0.5}
            size={size}
          />
          <button className="dl-lightbox-nav" disabled={index >= items.length - 1} onClick={() => onIndex(index + 1)} aria-label="Next">
            ›
          </button>
        </div>
        <div className="dl-toolbar">
          {explainable && (
            <label className="dl-check">
              <input type="checkbox" checked={showHeat} onChange={(e) => setShowHeat(e.target.checked)} /> AI heatmap
              {explaining && <span className="panel-hint"> computing…</span>}
            </label>
          )}
          {showHeat && <HeatmapLegend {...(isAnomaly(scenario) ? { low: "normal", high: "anomalous" } : {})} />}
          {item.has_mask && (
            <label className="dl-check">
              <input type="checkbox" checked={showMask} onChange={(e) => setShowMask(e.target.checked)} /> Ground-truth defect
            </label>
          )}
          <span className="panel-hint">← → to browse · Esc to close</span>
        </div>
      </div>
    </div>
  );
}
