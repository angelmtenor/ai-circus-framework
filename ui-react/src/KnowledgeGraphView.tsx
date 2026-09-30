import { useEffect, useMemo, useRef, useState } from "react";
import { knowledgeGraph, type KnowledgeGraphExtra, type KnowledgeGraphShape, type KnowledgeGraphTrace, type NetworkGraph } from "./apiClient";
import { config } from "./config";
import { buildKgModel, KnowledgeGraphScene, type KgNode, linkKey, roleColor } from "./knowledgeGraphScene";
import { surfaceMode, type SurfaceMode } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./knowledgeGraph.css";

/**
 * A conversational_rag scenario's knowledge graph (`ui_extras: knowledge_graph`), drawn
 * next to its chat. rag-agent's graph tools retrieve a small subgraph per answer and
 * report it as a `knowledge_graph_trace` event (RagView passes the latest one in): the
 * stage lights that subgraph up, so the reader sees *which* relations — each citing
 * its article — the answer was built from. Clicking a concept shows its relations with
 * their citation and the verbatim quote that backs them.
 */
export default function KnowledgeGraphView({
  scenarioSlug,
  extras,
  accessToken,
  trace,
}: {
  scenarioSlug: string;
  extras: KnowledgeGraphExtra;
  accessToken: string | null;
  trace: KnowledgeGraphTrace | null;
}) {
  const [graph, setGraph] = useState<NetworkGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dismissedTrace, setDismissedTrace] = useState<KnowledgeGraphTrace | null>(null);
  const [hover, setHover] = useState<{ node: KgNode; x: number; y: number } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<KnowledgeGraphScene | null>(null);
  const { theme } = useTheme();
  const mode = surfaceMode(theme.cssVars["--bg"]);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    let cancelled = false;
    knowledgeGraph(config.ragAgentUrl, scenarioSlug, accessToken)
      .then((g) => !cancelled && setGraph(g))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [scenarioSlug, accessToken]);

  const model = useMemo(() => (graph ? buildKgModel(graph, extras) : null), [graph, extras]);
  const activeTrace = trace && trace !== dismissedTrace ? trace : null;

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) return;
    const scene = new KnowledgeGraphScene(canvas, {
      onHover: (node, x, y) => setHover(node ? { node, x, y } : null),
      onSelect: (node) => setSelected(node?.id ?? null),
    });
    sceneRef.current = scene;
    scene.resize(stage.clientWidth, stage.clientHeight);
    const observer = new ResizeObserver(() => scene.resize(stage.clientWidth, stage.clientHeight));
    observer.observe(stage);
    return () => {
      observer.disconnect();
      scene.destroy();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (model) sceneRef.current?.setModel(model);
  }, [model]);

  useEffect(() => {
    sceneRef.current?.setView({ mode, reducedMotion, trace: activeTrace, selected });
  }, [mode, reducedMotion, activeTrace, selected, model]);

  // A new answer's trace takes over from a node the reader had selected.
  useEffect(() => {
    if (trace) setSelected(null);
  }, [trace]);

  const selectedNode = selected && model ? model.byId.get(selected) : undefined;
  const traceSummary = useMemo(() => {
    if (!activeTrace || !model) return null;
    const documents = activeTrace.nodes
      .map((id) => model.byId.get(id))
      .filter((n): n is KgNode => n?.role === "document")
      .map((n) => n.label.split(" — ")[0]);
    return { relations: activeTrace.edges.length, documents };
  }, [activeTrace, model]);

  return (
    <section className="kg-panel panel-card">
      <header className="kg-head">
        <div>
          <h3>{extras.title}</h3>
          {extras.subtitle && <p>{extras.subtitle}</p>}
        </div>
        {(selected || activeTrace) && (
          <button
            type="button"
            className="kg-reset"
            onClick={() => {
              setSelected(null);
              setDismissedTrace(trace);
              sceneRef.current?.frame(null, true);
            }}
          >
            Show all
          </button>
        )}
      </header>

      <div className="kg-stage" ref={stageRef}>
        <canvas ref={canvasRef} aria-label={`${extras.title}: interactive knowledge graph`} role="img" />
        {!graph && (
          <div className="kg-overlay">{error ? `Knowledge graph unavailable: ${error}` : "Loading the knowledge graph…"}</div>
        )}
        {traceSummary && !selectedNode && (
          <div className="kg-trace-chip" role="status">
            <span className="kg-trace-dot" />
            This answer walked <strong>{traceSummary.relations}</strong> relation{traceSummary.relations === 1 ? "" : "s"}
            {traceSummary.documents.length > 0 && <> · {traceSummary.documents.join(", ")}</>}
          </div>
        )}
        {!activeTrace && !selectedNode && graph && (
          <div className="kg-hint">Ask a question: its answer's relations light up here · click a concept</div>
        )}
        {hover && !selectedNode && <HoverCard node={hover.node} x={hover.x} y={hover.y} stage={stageRef.current} mode={mode} />}
        {selectedNode && model && (
          <NodeDetail node={selectedNode} model={model} mode={mode} extras={extras} onSelect={setSelected} />
        )}
      </div>

      <ul className="kg-legend" aria-label="Legend">
        {extras.classes.map((c) => (
          <li key={c.key}>
            <Glyph shape={c.shape} color={roleColor(mode, c.role)} />
            {c.label}
          </li>
        ))}
      </ul>
      {extras.disclaimer && <p className="kg-disclaimer">{extras.disclaimer}</p>}
    </section>
  );
}

function HoverCard({ node, x, y, stage, mode }: { node: KgNode; x: number; y: number; stage: HTMLElement | null; mode: SurfaceMode }) {
  const rect = stage?.getBoundingClientRect();
  if (!rect) return null;
  const left = Math.min(x - rect.left + 14, rect.width - 240);
  const top = Math.min(y - rect.top + 14, rect.height - 90);
  return (
    <div className="kg-hover" style={{ left, top }}>
      <span className="kg-class">
        <Glyph shape={node.shape} color={roleColor(mode, node.role)} /> {node.classLabel}
      </span>
      <strong>{node.label}</strong>
      {node.description && node.description !== node.label && <p>{node.description}</p>}
    </div>
  );
}

function NodeDetail({
  node,
  model,
  mode,
  extras,
  onSelect,
}: {
  node: KgNode;
  model: NonNullable<ReturnType<typeof buildKgModel>>;
  mode: SurfaceMode;
  extras: KnowledgeGraphExtra;
  onSelect: (id: string | null) => void;
}) {
  const touches = (l: { source: KgNode; target: KgNode }) => l.source.id === node.id || l.target.id === node.id;
  const relations = model.links.filter((l) => !l.document && !l.synonym && touches(l));
  const synonyms = model.links.filter((l) => l.synonym && touches(l)).map((l) => (l.source.id === node.id ? l.target : l.source));
  const documents = model.links.filter((l) => l.document && l.source.id === node.id).map((l) => l.target);
  const concepts = node.role === "document" ? model.links.filter((l) => l.document && l.target.id === node.id).map((l) => l.source) : [];
  const shapeOf = new Map(extras.classes.map((c) => [c.key, c]));
  return (
    <aside className="kg-detail" aria-label={`${node.label} details`}>
      <button type="button" className="kg-detail-close" onClick={() => onSelect(null)} aria-label="Close">
        ×
      </button>
      <span className="kg-class">
        <Glyph shape={node.shape} color={roleColor(mode, node.role)} /> {shapeOf.get(node.type)?.label ?? node.type}
      </span>
      <h4>{node.label}</h4>
      {node.role !== "document" && node.description && node.description !== node.label && (
        <p className="kg-detail-desc">{node.description}</p>
      )}
      {node.role === "document" && node.citation && <p className="kg-detail-desc">Source document: {node.citation}</p>}
      {synonyms.length > 0 && (
        <p className="kg-detail-desc">
          Also worded as{" "}
          {synonyms.map((other, i) => (
            <span key={other.id}>
              {i > 0 && ", "}
              <button type="button" className="kg-rel-target" onClick={() => onSelect(other.id)}>
                {other.label}
              </button>
            </span>
          ))}{" "}
          <span title="Inferred from embedding similarity, not stated in the text">(inferred)</span>
        </p>
      )}
      {relations.length > 0 && (
        <ol className="kg-relations">
          {relations.map((l) => {
            const outgoing = l.source.id === node.id;
            const other = outgoing ? l.target : l.source;
            return (
              <li key={linkKey(l.source.id, l.kind, l.target.id)}>
                <div className="kg-rel-line">
                  <span className="kg-rel-kind">{outgoing ? `${l.label} →` : `← ${l.label}`}</span>
                  <button type="button" className="kg-rel-target" onClick={() => onSelect(other.id)}>
                    <Glyph shape={other.shape} color={roleColor(mode, other.role)} /> {other.label}
                  </button>
                  {l.citation && <span className="kg-cite">{l.citation}</span>}
                </div>
                {l.evidence && <q className="kg-evidence">{l.evidence}</q>}
              </li>
            );
          })}
        </ol>
      )}
      {(documents.length > 0 || concepts.length > 0) && (
        <div className="kg-docs">
          {(documents.length > 0 ? documents : concepts).map((d) => (
            <button type="button" key={d.id} onClick={() => onSelect(d.id)}>
              <Glyph shape={d.shape} color={roleColor(mode, d.role)} /> {d.label}
            </button>
          ))}
        </div>
      )}
    </aside>
  );
}

function Glyph({ shape, color }: { shape: KnowledgeGraphShape; color: string }) {
  const common = { fill: color };
  return (
    <svg className="kg-glyph" viewBox="-7 -7 14 14" width="12" height="12" aria-hidden="true">
      {shape === "square" && <rect x="-5" y="-5" width="10" height="10" rx="1.6" {...common} />}
      {shape === "diamond" && <path d="M0 -6 L6 0 L0 6 L-6 0 Z" {...common} />}
      {shape === "triangle" && <path d="M0 -6 L5.6 4 L-5.6 4 Z" {...common} />}
      {shape === "hexagon" && <path d="M5.2 3 L0 6 L-5.2 3 L-5.2 -3 L0 -6 L5.2 -3 Z" {...common} />}
      {shape === "pill" && <rect x="-6.5" y="-3.4" width="13" height="6.8" rx="3.4" {...common} />}
      {shape === "circle" && <circle r="5" {...common} />}
    </svg>
  );
}

function usePrefersReducedMotion(): boolean {
  const query = useMemo(
    () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null),
    [],
  );
  const [reduced, setReduced] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [query]);
  return reduced;
}
