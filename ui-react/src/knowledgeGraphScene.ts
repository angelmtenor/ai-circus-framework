/**
 * The knowledge-graph stage next to a conversational_rag chat: an imperative canvas
 * renderer (d3-force layout, d3-zoom camera) KnowledgeGraphView drives through
 * `setModel` / `setView`.
 *
 * Colour is a node's *role* (actor / action / condition — the three network hues that
 * validate for a spatial graph — and a recessive grey for source documents); the glyph
 * is its ontology class, so colour is never alone. When an answer arrives, its trace
 * (the seeds rag-agent linked and the relations it retrieved) lights up: everything
 * else dims, the seeds pulse, particles run along each retrieved relation in its
 * direction, and the camera frames the subgraph. The loop only runs while something
 * moves; with reduced motion the highlight is static.
 */
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import { interpolateZoom } from "d3-interpolate";
import { select, type Selection } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import type { KnowledgeGraphExtra, KnowledgeGraphRole, KnowledgeGraphShape, KnowledgeGraphTrace, NetworkGraph } from "./apiClient";
import { NETWORK_PALETTE, type SurfaceMode } from "./riskPalette";

export const DOCUMENT_EDGE_KIND = "cited_in";
export const SYNONYM_EDGE_KIND = "similar_to"; // inferred by etl-vectorize: steers retrieval, not a fact

export interface KgNode extends SimulationNodeDatum {
  id: string;
  label: string;
  type: string;
  classLabel: string;
  role: KnowledgeGraphRole;
  shape: KnowledgeGraphShape;
  description?: string;
  citation?: string;
  degree: number;
  r: number;
}

export type KgLink = {
  key: string; // "source|kind|target", as in the trace
  source: KgNode;
  target: KgNode;
  kind: string;
  label: string;
  citation?: string;
  evidence?: string;
  document: boolean;
  synonym: boolean;
};

export type KgModel = { nodes: KgNode[]; links: KgLink[]; byId: Map<string, KgNode> };

export type KgView = {
  mode: SurfaceMode;
  reducedMotion: boolean;
  trace: KnowledgeGraphTrace | null;
  selected: string | null;
};

type Callbacks = {
  onHover: (node: KgNode | null, clientX: number, clientY: number) => void;
  onSelect: (node: KgNode | null) => void;
};

export const linkKey = (source: string, kind: string, target: string) => `${source}|${kind}|${target}`;

/** Palette per role and surface — see riskPalette.ts's NETWORK_PALETTE (validated all-pairs). */
export function roleColor(mode: SurfaceMode, role: KnowledgeGraphRole): string {
  const p = NETWORK_PALETTE[mode];
  return role === "actor" ? p.communities[0] : role === "action" ? p.communities[1] : role === "condition" ? p.communities[2] : p.context;
}

export function buildKgModel(graph: NetworkGraph, extras: KnowledgeGraphExtra): KgModel {
  const classes = new Map(extras.classes.map((c) => [c.key, c]));
  const byId = new Map<string, KgNode>();
  for (const n of graph.nodes) {
    const cls = classes.get(n.type ?? "");
    byId.set(n.id, {
      id: n.id,
      label: n.label ?? n.id,
      type: n.type ?? "",
      classLabel: cls?.label ?? n.type ?? "",
      role: cls?.role ?? "action",
      shape: cls?.shape ?? "circle",
      description: n.description,
      citation: n.citation,
      degree: 0,
      r: 5,
    });
  }
  const links: KgLink[] = [];
  for (const e of graph.edges) {
    const source = byId.get(e.source);
    const target = byId.get(e.target);
    if (!source || !target) continue;
    const document = e.kind === DOCUMENT_EDGE_KIND;
    const synonym = e.kind === SYNONYM_EDGE_KIND;
    links.push({
      key: linkKey(e.source, e.kind, e.target),
      source,
      target,
      kind: e.kind,
      label: e.label ?? e.kind.replace(/_/g, " "),
      citation: e.citation,
      evidence: e.evidence,
      document,
      synonym,
    });
    if (!document && !synonym) {
      source.degree += 1;
      target.degree += 1;
    }
  }
  for (const node of byId.values()) {
    node.r = node.role === "document" ? 7 : 4.5 + Math.min(9, Math.sqrt(node.degree) * 1.9);
  }
  return { nodes: [...byId.values()], links, byId };
}

function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (1664525 * s + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function shapePath(ctx: CanvasRenderingContext2D, shape: KnowledgeGraphShape, x: number, y: number, r: number): void {
  ctx.beginPath();
  switch (shape) {
    case "square":
      ctx.roundRect(x - r * 0.9, y - r * 0.9, r * 1.8, r * 1.8, r * 0.3);
      break;
    case "diamond":
      ctx.moveTo(x, y - r * 1.2);
      ctx.lineTo(x + r * 1.2, y);
      ctx.lineTo(x, y + r * 1.2);
      ctx.lineTo(x - r * 1.2, y);
      ctx.closePath();
      break;
    case "triangle":
      ctx.moveTo(x, y - r * 1.25);
      ctx.lineTo(x + r * 1.15, y + r * 0.8);
      ctx.lineTo(x - r * 1.15, y + r * 0.8);
      ctx.closePath();
      break;
    case "hexagon":
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 3) * i + Math.PI / 6;
        ctx[i === 0 ? "moveTo" : "lineTo"](x + Math.cos(a) * r * 1.12, y + Math.sin(a) * r * 1.12);
      }
      ctx.closePath();
      break;
    case "pill":
      ctx.roundRect(x - r * 1.35, y - r * 0.7, r * 2.7, r * 1.4, r * 0.7);
      break;
    default:
      ctx.arc(x, y, r, 0, Math.PI * 2);
  }
}

const WARM_TICKS = 360;
const CAMERA_MS = 900;

export class KnowledgeGraphScene {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly callbacks: Callbacks;
  private readonly selection: Selection<HTMLCanvasElement, unknown, null, undefined>;
  private readonly zoomBehavior: ZoomBehavior<HTMLCanvasElement, unknown>;
  private model: KgModel | null = null;
  private view: KgView = { mode: "dark", reducedMotion: false, trace: null, selected: null };
  private transform: ZoomTransform = zoomIdentity;
  private width = 1;
  private height = 1;
  private dpr = 1;
  private raf = 0;
  private dirty = true;
  private hoverId: string | null = null;
  private press: { x: number; y: number } | null = null;
  private camera: { start: number; interp: ReturnType<typeof interpolateZoom> } | null = null;
  // What is lit: the trace (or the selected node's neighbourhood), rebuilt on setView.
  private litNodes = new Set<string>();
  private litLinks = new Set<string>();
  private seeds = new Set<string>();
  private traceStart = 0;
  private topLabels = new Set<string>();

  constructor(canvas: HTMLCanvasElement, callbacks: Callbacks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.callbacks = callbacks;
    this.selection = select(canvas);
    this.zoomBehavior = zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.3, 6])
      .on("zoom", (event: { transform: ZoomTransform; sourceEvent: Event | null }) => {
        this.transform = event.transform;
        if (event.sourceEvent) this.camera = null; // the user took the camera
        this.invalidate();
      });
    this.selection.call(this.zoomBehavior).on("dblclick.zoom", null);
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("pointerleave", this.onPointerLeave);
  }

  destroy(): void {
    cancelAnimationFrame(this.raf);
    this.selection.on(".zoom", null);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.canvas.removeEventListener("pointerleave", this.onPointerLeave);
  }

  resize(width: number, height: number): void {
    const first = this.width <= 1;
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;
    if (first && this.model) this.frame(null, false);
    this.invalidate();
  }

  setModel(model: KgModel): void {
    this.model = model;
    const random = lcg(42);
    for (const node of model.nodes) {
      node.x = (random() - 0.5) * 400;
      node.y = (random() - 0.5) * 400;
    }
    const sim = forceSimulation<KgNode>(model.nodes)
      .randomSource(lcg(7))
      .force(
        "link",
        forceLink<KgNode, KgLink>(model.links)
          .distance((l) => (l.document ? 70 : l.synonym ? 34 : 42))
          .strength((l) => (l.document ? 0.08 : l.synonym ? 0.3 : 0.55)),
      )
      .force("charge", forceManyBody<KgNode>().strength((n) => (n.role === "document" ? -260 : -95)).distanceMax(600))
      .force("collide", forceCollide<KgNode>((n) => n.r + 7).iterations(2))
      .force("x", forceX<KgNode>(0).strength(0.09))
      .force("y", forceY<KgNode>(0).strength(0.09))
      .stop();
    for (let i = 0; i < WARM_TICKS; i++) sim.tick();
    // Always-on labels: the most connected concepts and every document.
    const ranked = model.nodes.filter((n) => n.role !== "document").sort((a, b) => b.degree - a.degree);
    this.topLabels = new Set(ranked.slice(0, 9).map((n) => n.id));
    this.frame(null, false);
    this.invalidate();
  }

  setView(view: KgView): void {
    const traceChanged = view.trace !== this.view.trace;
    const selectionChanged = view.selected !== this.view.selected;
    this.view = view;
    this.litNodes = new Set();
    this.litLinks = new Set();
    this.seeds = new Set();
    const model = this.model;
    if (model && view.selected && model.byId.has(view.selected)) {
      this.litNodes.add(view.selected);
      for (const link of model.links) {
        if (link.source.id === view.selected || link.target.id === view.selected) {
          this.litLinks.add(link.key);
          this.litNodes.add(link.source.id);
          this.litNodes.add(link.target.id);
        }
      }
    } else if (model && view.trace) {
      for (const id of [...view.trace.nodes, ...view.trace.seeds]) if (model.byId.has(id)) this.litNodes.add(id);
      for (const id of view.trace.seeds) this.seeds.add(id);
      for (const [s, k, t] of view.trace.edges) this.litLinks.add(linkKey(s, k, t));
      // The documents the lit concepts came from, so the passage nodes glow too.
      for (const link of model.links) {
        if (link.document && this.litNodes.has(link.source.id) && this.litNodes.has(link.target.id)) this.litLinks.add(link.key);
      }
    }
    if (traceChanged || selectionChanged) {
      this.traceStart = performance.now();
      if (selectionChanged && view.selected) this.frame(this.litNodes, true, 0.8);
      else if (traceChanged) this.frame(this.litNodes.size ? this.litNodes : null, true);
    }
    this.invalidate();
  }

  /** Frame the given nodes (or the whole graph), animated unless reduced motion. */
  frame(ids: Set<string> | null, animate: boolean, minScale = 0.35): void {
    const model = this.model;
    if (!model || this.width <= 1) return;
    const nodes = ids && ids.size ? model.nodes.filter((n) => ids.has(n.id)) : model.nodes;
    // The whole graph is framed on its central 94 % so a stray component doesn't shrink it.
    const trim = ids && ids.size ? 0 : 0.03;
    const xs = nodes.map((n) => n.x!).sort((a, b) => a - b);
    const ys = nodes.map((n) => n.y!).sort((a, b) => a - b);
    const at = (v: number[], q: number) => v[Math.min(v.length - 1, Math.max(0, Math.round(q * (v.length - 1))))];
    const x0 = at(xs, trim) - 12;
    const x1 = at(xs, 1 - trim) + 12;
    const y0 = at(ys, trim) - 12;
    const y1 = at(ys, 1 - trim) + 12;
    const pad = ids && ids.size ? 90 : 40; // room for the labels around a lit subgraph
    // The graph-space width the view must show so the box fits both ways.
    const size = Math.max(x1 - x0 + pad * 2, (y1 - y0 + pad * 2) * (this.width / this.height), 120);
    const k = Math.min(3, Math.max(minScale, this.width / size));
    const chipClearance = 22 / k; // the status chip sits along the stage's bottom edge
    const [cx, cy] = [(x0 + x1) / 2, (y0 + y1) / 2 + chipClearance];
    if (!animate || this.view.reducedMotion) {
      this.camera = null;
      this.selection.call(this.zoomBehavior.transform, zoomIdentity.translate(this.width / 2 - cx * k, this.height / 2 - cy * k).scale(k));
      return;
    }
    const current = this.transform;
    const from: [number, number, number] = [
      (this.width / 2 - current.x) / current.k,
      (this.height / 2 - current.y) / current.k,
      this.width / current.k,
    ];
    this.camera = { start: performance.now(), interp: interpolateZoom(from, [cx, cy, this.width / k]) };
    this.invalidate();
  }

  // ── events ────────────────────────────────────────────────────────────────

  private nodeAt(clientX: number, clientY: number): KgNode | null {
    if (!this.model) return null;
    const rect = this.canvas.getBoundingClientRect();
    const [gx, gy] = this.transform.invert([clientX - rect.left, clientY - rect.top]);
    let best: KgNode | null = null;
    let bestD = Infinity;
    for (const node of this.model.nodes) {
      const d = Math.hypot(node.x! - gx, node.y! - gy);
      if (d < node.r + 6 / this.transform.k && d < bestD) {
        best = node;
        bestD = d;
      }
    }
    return best;
  }

  private onPointerDown = (e: PointerEvent) => {
    this.press = { x: e.clientX, y: e.clientY };
  };

  private onPointerMove = (e: PointerEvent) => {
    const node = this.nodeAt(e.clientX, e.clientY);
    const id = node?.id ?? null;
    this.canvas.style.cursor = node ? "pointer" : "grab";
    if (id !== this.hoverId) {
      this.hoverId = id;
      this.invalidate();
    }
    this.callbacks.onHover(node, e.clientX, e.clientY);
  };

  private onPointerUp = (e: PointerEvent) => {
    if (this.press && Math.hypot(e.clientX - this.press.x, e.clientY - this.press.y) < 5) {
      this.callbacks.onSelect(this.nodeAt(e.clientX, e.clientY));
    }
    this.press = null;
  };

  private onPointerLeave = () => {
    this.hoverId = null;
    this.callbacks.onHover(null, 0, 0);
    this.invalidate();
  };

  // ── drawing ───────────────────────────────────────────────────────────────

  private invalidate = () => {
    this.dirty = true;
    if (!this.raf) this.raf = requestAnimationFrame(this.tick);
  };

  private animating(): boolean {
    return !this.view.reducedMotion && (this.litLinks.size > 0 || this.camera !== null);
  }

  private tick = (now: number) => {
    this.raf = 0;
    if (this.camera) {
      const t = Math.min(1, (now - this.camera.start) / CAMERA_MS);
      const eased = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      const [cx, cy, w] = this.camera.interp(eased);
      const k = this.width / w;
      this.selection.call(this.zoomBehavior.transform, zoomIdentity.translate(this.width / 2 - cx * k, this.height / 2 - cy * k).scale(k));
      if (t >= 1) this.camera = null;
    }
    if (this.dirty || this.animating()) {
      this.dirty = false;
      this.draw(now);
    }
    if (this.animating() && document.visibilityState === "visible") this.raf = requestAnimationFrame(this.tick);
  };

  private draw(now: number): void {
    const { ctx, model } = this;
    const palette = NETWORK_PALETTE[this.view.mode];
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const bg = ctx.createRadialGradient(this.width / 2, this.height / 2, 0, this.width / 2, this.height / 2, Math.max(this.width, this.height) * 0.75);
    bg.addColorStop(0, palette.stage);
    bg.addColorStop(1, palette.stageEdge);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, this.width, this.height);
    if (!model) return;

    const { k, x: tx, y: ty } = this.transform;
    ctx.setTransform(this.dpr * k, 0, 0, this.dpr * k, this.dpr * tx, this.dpr * ty);
    const focus = this.litNodes.size > 0;
    const hover = this.hoverId;
    const elapsed = now - this.traceStart;
    const motion = !this.view.reducedMotion;

    // Links: document links faint and straight, relations gently curved.
    for (const link of model.links) {
      const lit = this.litLinks.has(link.key);
      const touched = hover !== null && (link.source.id === hover || link.target.id === hover);
      const faint = link.document || link.synonym;
      const alpha = lit || touched ? (faint ? 0.45 : 0.95) : focus ? 0.05 : faint ? 0.1 : 0.32;
      ctx.globalAlpha = alpha;
      ctx.strokeStyle = lit || touched ? (faint ? palette.dim : palette.ink) : palette.link;
      ctx.lineWidth = (lit ? (faint ? 1 : 2) : faint ? 0.6 : 1) / Math.sqrt(k);
      ctx.setLineDash(link.document ? [3 / k, 4 / k] : link.synonym ? [1 / k, 3 / k] : []);
      const { sx, sy, cx, cy, ex, ey } = this.curve(link);
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.quadraticCurveTo(cx, cy, ex, ey);
      ctx.stroke();
      if (!faint && (lit || touched)) this.arrow(ex, ey, cx, cy, ctx.strokeStyle as string, 5 / Math.sqrt(k));
    }
    ctx.setLineDash([]);

    // Particles along each lit relation, source -> target.
    if (focus && motion) {
      ctx.globalAlpha = 1;
      for (const link of model.links) {
        if (link.document || link.synonym || !this.litLinks.has(link.key)) continue;
        const { sx, sy, cx, cy, ex, ey } = this.curve(link);
        const phase = (hashOf(link.key) % 1000) / 1000;
        for (const offset of [0, 0.5]) {
          const t = (elapsed / 1600 + phase + offset) % 1;
          const x = (1 - t) * (1 - t) * sx + 2 * (1 - t) * t * cx + t * t * ex;
          const y = (1 - t) * (1 - t) * sy + 2 * (1 - t) * t * cy + t * t * ey;
          ctx.fillStyle = roleColor(this.view.mode, link.source.role);
          ctx.beginPath();
          ctx.arc(x, y, 2.2 / Math.sqrt(k), 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    // Nodes.
    for (const node of model.nodes) {
      const lit = this.litNodes.has(node.id);
      const dimmed = focus && !lit && node.id !== hover;
      const color = roleColor(this.view.mode, node.role);
      ctx.globalAlpha = dimmed ? 0.16 : 1;
      if (lit && motion) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 16 * k;
      }
      shapePath(ctx, node.shape, node.x!, node.y!, node.r);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = 1.5 / k;
      ctx.strokeStyle = node.id === this.view.selected || node.id === hover ? palette.ink : palette.stage;
      ctx.stroke();
      if (this.seeds.has(node.id)) {
        const pulse = motion ? (Math.sin(elapsed / 260) + 1) / 2 : 0.5;
        ctx.globalAlpha = 0.85 - pulse * 0.5;
        ctx.strokeStyle = palette.ink;
        ctx.lineWidth = 1.6 / k;
        ctx.beginPath();
        ctx.arc(node.x!, node.y!, node.r + 5 + pulse * 4, 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    // Labels: lit / hovered / selected; otherwise hubs, and everything when zoomed in. Placed
    // greedily by priority (hovered, selected, seeds, then by degree); one that would
    // overlap an already placed label is skipped — hover or zoom reveals it.
    ctx.globalAlpha = 1;
    const fontPx = 11.5 / k; // constant on screen
    ctx.font = `600 ${fontPx}px Inter, system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineJoin = "round";
    const priority = (n: KgNode) =>
      n.id === hover ? 1e6 : n.id === this.view.selected ? 1e5 : (this.seeds.has(n.id) ? 1e3 : 0) + n.degree;
    const wanted = model.nodes
      .filter(
        (n) =>
          n.id === hover ||
          n.id === this.view.selected ||
          (focus ? this.litNodes.has(n.id) : this.topLabels.has(n.id) || k > 1.9),
      )
      .sort((a, b) => priority(b) - priority(a));
    const placed: [number, number, number, number][] = [];
    for (const node of wanted) {
      const lit = this.litNodes.has(node.id);
      const text = node.label.length > 34 ? `${node.label.slice(0, 32)}…` : node.label;
      const y = node.y! + node.r * 1.3 + 3 / k;
      const half = ctx.measureText(text).width / 2 + 2 / k;
      const box: [number, number, number, number] = [node.x! - half, y, node.x! + half, y + fontPx * 1.15];
      if (priority(node) < 1e5 && placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) {
        continue;
      }
      placed.push(box);
      ctx.strokeStyle = palette.halo;
      ctx.lineWidth = 3.5 / k;
      ctx.strokeText(text, node.x!, y);
      ctx.fillStyle = node.role === "document" && !lit ? palette.dim : palette.ink;
      ctx.fillText(text, node.x!, y);
    }
    ctx.globalAlpha = 1;
  }

  private curve(link: KgLink) {
    const sx = link.source.x!;
    const sy = link.source.y!;
    const tx = link.target.x!;
    const ty = link.target.y!;
    const bend = link.document || link.synonym ? 0 : 0.12;
    const cx = (sx + tx) / 2 - (ty - sy) * bend;
    const cy = (sy + ty) / 2 + (tx - sx) * bend;
    // Stop at the target's rim so the arrowhead stays visible.
    const dx = tx - cx;
    const dy = ty - cy;
    const d = Math.hypot(dx, dy) || 1;
    const rim = link.target.r + 2;
    return { sx, sy, cx, cy, ex: tx - (dx / d) * rim, ey: ty - (dy / d) * rim };
  }

  private arrow(x: number, y: number, fromX: number, fromY: number, color: string, size: number): void {
    const a = Math.atan2(y - fromY, x - fromX);
    const { ctx } = this;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - size * Math.cos(a - 0.45), y - size * Math.sin(a - 0.45));
    ctx.lineTo(x - size * Math.cos(a + 0.45), y - size * Math.sin(a + 0.45));
    ctx.closePath();
    ctx.fill();
  }
}

function hashOf(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619) >>> 0;
  return h;
}
