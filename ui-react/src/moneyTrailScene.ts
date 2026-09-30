/**
 * The money trail's world map (see MoneyTrailView.tsx), imperative like networkScene.ts —
 * the React view owns the data and pushes it in:
 *
 * - a land map (world-atlas, d3-geo Equal Earth) with every country's holders clustered
 *   around its bank hub (a phyllotaxis spiral; the hubs are nudged apart by a small
 *   d3-force run so Europe stays legible) — a schematic, never a geographic claim;
 * - each holder a dot coloured by its risk tier (a person a circle, a company a diamond),
 *   banks as violet squares, holders outside the consortium as hollow grey rings;
 * - the flows of the current hour: while playing, one particle per payment hopping from
 *   payer to payee (its *shape* is the payment format — never colour alone); paused, the
 *   hour's flows as static arcs. A selected holder shows all of its flows, arrowheaded.
 *
 * Renders on demand; animates only while playing, visible and not under reduced motion.
 * Software canvases are slow, so the pixel ratio drops to 1 when frames run long.
 */
import { forceCollide, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import { geoEqualEarth, geoGraticule10, geoPath, type GeoProjection, type GeoPermissibleObjects } from "d3-geo";
import type { FeatureCollection } from "geojson";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { MoneyTrailExtra } from "./apiClient";
import { rgba, sizeCanvas } from "./logisticsViz";
import type { Model, MtFlow, MtNode } from "./moneyTrailModel";
import type { SurfaceMode } from "./riskPalette";

export type MtColors = {
  oceanTop: string;
  oceanBottom: string;
  land: string;
  border: string;
  graticule: string;
  ink: string;
  dim: string;
  halo: string;
  bank: string;
  money: string; // particles and the hour's arcs
  out: string; // a selected holder's payments sent
  in: string; // ... and received
  scheme: string; // flows inside one laundering attempt (on reveal)
  context: string;
  ring: string;
  tiers: string[]; // per tier
};

export type MtView = {
  mode: SurfaceMode;
  colors: MtColors;
  hour: number | null; // null = the whole ten days
  playing: boolean;
  tickMs: number; // wall-clock per replayed hour
  selectedId: string | null;
  revealed: boolean;
  flagTier: number;
  highlight: Set<string> | null; // node ids kept bright (a typology filter); others recede
  reducedMotion: boolean;
};

export type MtCallbacks = {
  onSelect: (id: string | null) => void;
  onHover: (node: MtNode | null, x: number, y: number) => void;
};

/** Payment format -> mark. Shape carries the format so colour never has to. */
export type MarkShape = "circle" | "diamond" | "square" | "triangle" | "cross" | "hexagon";
export const FORMAT_SHAPES: Record<string, MarkShape> = {
  ach: "circle",
  wire: "diamond",
  cheque: "square",
  credit_card: "triangle",
  cash: "cross",
  bitcoin: "hexagon",
};
const SHAPE_POINTS: Record<Exclude<MarkShape, "circle" | "cross">, [number, number][]> = {
  diamond: [[0, -1.25], [1.25, 0], [0, 1.25], [-1.25, 0]],
  square: [[-0.95, -0.95], [0.95, -0.95], [0.95, 0.95], [-0.95, 0.95]],
  triangle: [[0, -1.2], [1.15, 0.95], [-1.15, 0.95]],
  hexagon: [[1.1, 0], [0.55, 0.95], [-0.55, 0.95], [-1.1, 0], [-0.55, -0.95], [0.55, -0.95]],
}; // fmt: skip

/** SVG `points` (or a path for circle/cross) for a mark of half-size `s` centred at (cx, cy). */
export function shapeSvg(shape: MarkShape, cx: number, cy: number, s: number): { points?: string; d?: string } {
  if (shape === "circle") return { d: `M${cx - s},${cy}a${s},${s} 0 1,0 ${2 * s},0a${s},${s} 0 1,0 ${-2 * s},0` };
  if (shape === "cross") return { d: `M${cx - s},${cy}h${2 * s}M${cx},${cy - s}v${2 * s}` };
  return { points: SHAPE_POINTS[shape].map(([x, y]) => `${(cx + x * s).toFixed(2)},${(cy + y * s).toFixed(2)}`).join(" ") };
}

function drawMark(ctx: CanvasRenderingContext2D, shape: MarkShape, x: number, y: number, s: number): void {
  ctx.beginPath();
  if (shape === "circle") {
    ctx.arc(x, y, s, 0, Math.PI * 2);
  } else if (shape === "cross") {
    ctx.moveTo(x - s, y);
    ctx.lineTo(x + s, y);
    ctx.moveTo(x, y - s);
    ctx.lineTo(x, y + s);
    ctx.lineWidth = Math.max(1.4, s * 0.55);
    ctx.stroke();
    return;
  } else {
    SHAPE_POINTS[shape].forEach(([px, py], i) => (i ? ctx.lineTo(x + px * s, y + py * s) : ctx.moveTo(x + px * s, y + py * s)));
    ctx.closePath();
  }
  ctx.fill();
}

type Particle = { flow: MtFlow; hour: number; born: number; life: number; amount: number };

const CELL = 14;
const MAX_ARCS = 170;
const MAX_PARTICLES = 420;
const MAX_ALL_ARCS = 260;

function hash01(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10007) / 10007;
}

function rank(node: MtNode): number {
  return node.kind === "entity" ? 0 : node.kind === "row" ? 1 : 2;
}

export class MoneyTrailScene {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private base = document.createElement("canvas");
  private model: Model | null = null;
  private extras: MoneyTrailExtra | null = null;
  private land: GeoPermissibleObjects | null = null;
  private view: MtView | null = null;
  private projection: GeoProjection | null = null;
  private width = 0;
  private height = 0;
  private dpr = 1;
  private maxDpr = 2;
  private k = 1;
  private raf = 0;
  private visible = true;
  private grid = new Map<number, MtNode[]>();
  private anchors = new Map<string, { x: number; y: number; radius: number; count: number }>();
  private particles: Particle[] = [];
  private lastHour: number | null = null;
  private slowFrames = 0;
  private hover: MtNode | null = null;
  private glow = new Map<string, HTMLCanvasElement>();
  private observer: IntersectionObserver;
  private cb: MtCallbacks;

  constructor(canvas: HTMLCanvasElement, cb: MtCallbacks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.cb = cb;
    canvas.addEventListener("pointermove", this.onMove);
    canvas.addEventListener("pointerleave", this.onLeave);
    canvas.addEventListener("click", this.onClick);
    this.observer = new IntersectionObserver((entries) => {
      this.visible = entries.some((e) => e.isIntersecting);
      this.kick();
    });
    this.observer.observe(canvas);
    document.addEventListener("visibilitychange", this.kick);
  }

  destroy(): void {
    cancelAnimationFrame(this.raf);
    this.observer.disconnect();
    document.removeEventListener("visibilitychange", this.kick);
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
    this.canvas.removeEventListener("click", this.onClick);
  }

  setModel(model: Model, extras: MoneyTrailExtra): void {
    this.model = model;
    this.extras = extras;
    this.layout();
  }

  setGeometry(topology: Topology): void {
    const objects = topology.objects as Record<string, GeometryCollection>;
    this.land = feature(topology, objects.land) as FeatureCollection;
    this.layout();
  }

  resize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.dpr = sizeCanvas(this.canvas, width, height, this.maxDpr);
    this.layout();
  }

  setView(view: MtView): void {
    const prev = this.view;
    this.view = view;
    if (!prev || prev.mode !== view.mode || prev.colors !== view.colors) this.renderBase();
    if (view.playing && !view.reducedMotion && view.hour !== null && view.hour !== this.lastHour) this.spawn(view.hour, view.tickMs);
    if (!view.playing || view.hour === null) this.particles = [];
    this.lastHour = view.hour;
    this.kick();
  }

  // ── layout ────────────────────────────────────────────────────────────────

  private layout(): void {
    const { model, extras, land } = this;
    if (!model || !extras || !land || this.width < 2) return;
    const pad = 14;
    const projection = geoEqualEarth();
    projection.fitExtent(
      [
        [pad, pad + 4],
        [this.width - pad, this.height - pad - 8],
      ],
      land,
    );
    this.projection = projection;
    this.k = Math.min(1.25, Math.max(0.55, this.width / 1150));
    const k = this.k;

    const byCountry = new Map<string, MtNode[]>();
    for (const node of model.nodes) byCountry.set(node.country, [...(byCountry.get(node.country) ?? []), node]);
    const geo = new Map(extras.countries.map((c) => [c.key, c]));

    type Anchor = SimulationNodeDatum & { key: string; gx: number; gy: number; radius: number };
    const anchors: Anchor[] = [];
    for (const [key, list] of byCountry) {
      const g = geo.get(key);
      const p = g ? projection([g.lon, g.lat]) : null;
      if (!p) continue;
      const radius = k * (12 + 3.3 * Math.sqrt(list.length));
      anchors.push({ key, gx: p[0], gy: p[1], x: p[0], y: p[1], radius });
    }
    // Nudge the clusters apart (they stay pulled to their real position).
    const sim = forceSimulation(anchors)
      .force("x", forceX<Anchor>((a) => a.gx).strength(0.12))
      .force("y", forceY<Anchor>((a) => a.gy).strength(0.12))
      .force("collide", forceCollide<Anchor>((a) => a.radius + 3 * k).strength(0.9))
      .stop();
    for (let i = 0; i < 160; i++) sim.tick();

    this.anchors.clear();
    this.grid.clear();
    for (const a of anchors) {
      const x = Math.min(this.width - a.radius * 0.6, Math.max(a.radius * 0.6, a.x ?? a.gx));
      const y = Math.min(this.height - a.radius * 0.6, Math.max(a.radius * 0.6, a.y ?? a.gy));
      const list = byCountry.get(a.key)!;
      this.anchors.set(a.key, { x, y, radius: a.radius, count: list.length });
      const order = [...list].sort((p, q) => rank(p) - rank(q) || q.volume - p.volume || (p.id < q.id ? -1 : 1));
      let i = 0;
      for (const node of order) {
        if (node.kind === "entity") {
          node.x = x;
          node.y = y;
          node.r = 6 * k;
          continue;
        }
        const angle = i * 2.399963 + hash01(a.key) * 6.28;
        const dist = k * 11 + k * 3.3 * Math.sqrt(i + 0.5);
        node.x = x + Math.cos(angle) * dist;
        node.y = y + Math.sin(angle) * dist;
        node.r = node.kind === "context" ? 1.5 * k : k * (1.3 + 0.95 * Math.log10(1 + (node.holder?.size ?? 0) / 10_000));
        i += 1;
      }
    }
    for (const node of model.nodes) {
      const key = this.cellKey(node.x, node.y);
      this.grid.set(key, [...(this.grid.get(key) ?? []), node]);
    }
    this.renderBase();
    this.kick();
  }

  private cellKey(x: number, y: number): number {
    return Math.floor(x / CELL) * 10_000 + Math.floor(y / CELL);
  }

  private renderBase(): void {
    const view = this.view;
    if (!view || !this.projection || !this.land || this.width < 2) return;
    const { colors } = view;
    this.base.width = this.canvas.width;
    this.base.height = this.canvas.height;
    const ctx = this.base.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const bg = ctx.createLinearGradient(0, 0, 0, this.height);
    bg.addColorStop(0, colors.oceanTop);
    bg.addColorStop(1, colors.oceanBottom);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, this.width, this.height);
    const path = geoPath(this.projection, ctx);
    ctx.beginPath();
    path(geoGraticule10());
    ctx.strokeStyle = colors.graticule;
    ctx.lineWidth = 0.6;
    ctx.stroke();
    ctx.beginPath();
    path(this.land);
    ctx.fillStyle = colors.land;
    ctx.fill();
    ctx.strokeStyle = colors.border;
    ctx.lineWidth = 0.8;
    ctx.stroke();

    // Soft halos under each country's cluster, and its label.
    ctx.textAlign = "center";
    ctx.font = `600 ${Math.round(10.5 * Math.min(1.15, this.k + 0.1))}px system-ui, sans-serif`;
    for (const [country, a] of this.anchors) {
      const g = ctx.createRadialGradient(a.x, a.y, 0, a.x, a.y, a.radius * 1.25);
      g.addColorStop(0, rgba(colors.dim, view.mode === "dark" ? 0.16 : 0.13));
      g.addColorStop(1, rgba(colors.dim, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(a.x, a.y, a.radius * 1.25, 0, Math.PI * 2);
      ctx.fill();
      if (a.count >= 6) {
        const y = a.y + a.radius + 11 * this.k;
        ctx.lineWidth = 3.4;
        ctx.lineJoin = "round";
        ctx.strokeStyle = colors.halo;
        ctx.strokeText(country, a.x, y);
        ctx.fillStyle = colors.dim;
        ctx.fillText(country, a.x, y);
      }
    }
  }

  // ── animation ─────────────────────────────────────────────────────────────

  private spawn(hour: number, tickMs: number): void {
    const model = this.model;
    if (!model) return;
    const now = performance.now();
    const list = model.flowsByHour[hour] ?? [];
    // A payment always takes long enough to follow, even at 4x (hours then overlap on the map).
    const life = Math.max(tickMs * 1.6, 900);
    for (const index of list.slice(0, 90)) {
      const flow = model.flows[index];
      this.particles.push({ flow, hour, born: now + Math.random() * tickMs * 0.7, life: life * (1 + Math.random() * 0.3), amount: flow.hours[hour] });
    }
    if (this.particles.length > MAX_PARTICLES) this.particles = this.particles.slice(-MAX_PARTICLES);
  }

  private kick = (): void => {
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  };

  private loop = (now: number): void => {
    this.raf = 0;
    const started = performance.now();
    this.draw(now);
    const took = performance.now() - started;
    if (this.maxDpr > 1 && took > 55) {
      this.slowFrames += 1;
      if (this.slowFrames > 8) {
        this.maxDpr = 1;
        this.dpr = sizeCanvas(this.canvas, this.width, this.height, 1);
        this.renderBase();
        this.slowFrames = 0;
      }
    } else this.slowFrames = Math.max(0, this.slowFrames - 1);
    const view = this.view;
    const animate = !!view && !view.reducedMotion && this.visible && !document.hidden && (view.playing || view.selectedId !== null || this.particles.length > 0);
    if (animate) this.raf = requestAnimationFrame(this.loop);
  };

  private control(a: MtNode, b: MtNode): [number, number] {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    return [(a.x + b.x) / 2 - dy * 0.2, (a.y + b.y) / 2 + dx * 0.2 - Math.min(40, Math.hypot(dx, dy) * 0.06)];
  }

  private arcPoint(a: MtNode, b: MtNode, t: number): [number, number] {
    const [cx, cy] = this.control(a, b);
    const u = 1 - t;
    return [u * u * a.x + 2 * u * t * cx + t * t * b.x, u * u * a.y + 2 * u * t * cy + t * t * b.y];
  }

  private strokeArc(a: MtNode, b: MtNode, width: number, color: string, alpha: number, dash?: number[]): void {
    const ctx = this.ctx;
    const [cx, cy] = this.control(a, b);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.quadraticCurveTo(cx, cy, b.x, b.y);
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash ?? []);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  private glowSprite(color: string): HTMLCanvasElement {
    let sprite = this.glow.get(color);
    if (!sprite) {
      sprite = document.createElement("canvas");
      sprite.width = sprite.height = 64;
      const g = sprite.getContext("2d")!;
      const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      grad.addColorStop(0, rgba(color, 0.85));
      grad.addColorStop(0.35, rgba(color, 0.32));
      grad.addColorStop(1, rgba(color, 0));
      g.fillStyle = grad;
      g.fillRect(0, 0, 64, 64);
      this.glow.set(color, sprite);
    }
    return sprite;
  }

  private dot(node: MtNode, color: string, alpha: number): void {
    const ctx = this.ctx;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = color;
    ctx.beginPath();
    if (node.person) {
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
    } else {
      const s = node.r * 1.25;
      ctx.moveTo(node.x, node.y - s);
      ctx.lineTo(node.x + s, node.y);
      ctx.lineTo(node.x, node.y + s);
      ctx.lineTo(node.x - s, node.y);
      ctx.closePath();
    }
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  private draw(now: number): void {
    const { view, model, ctx } = this;
    if (!view || !model || this.width < 2) return;
    const { colors } = view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.drawImage(this.base, 0, 0);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.lineCap = "round";
    const selected = view.selectedId ? model.byId.get(view.selectedId) ?? null : null;
    const bright = (n: MtNode) => !view.highlight || view.highlight.has(n.id) || n.id === view.selectedId;
    const dimAll = selected !== null || view.highlight !== null;

    // 1. the current hour's flows as static arcs (paused / reduced motion), or the ten days' biggest.
    const animatedNow = view.playing && !view.reducedMotion && view.hour !== null;
    if (view.hour === null) {
      const biggest = [...model.flows].sort((a, b) => b.weight - a.weight).slice(0, MAX_ALL_ARCS);
      for (const f of biggest) this.strokeArc(f.source, f.target, 0.5 + Math.min(1.6, Math.log10(1 + f.weight) * 0.22), colors.money, dimAll ? 0.08 : 0.2);
    } else if (!animatedNow) {
      for (const index of (model.flowsByHour[view.hour] ?? []).slice(0, MAX_ARCS)) {
        const f = model.flows[index];
        const usd = f.hours[view.hour];
        this.strokeArc(f.source, f.target, 0.7 + Math.min(2, Math.log10(1 + usd) * 0.28), colors.money, dimAll ? 0.18 : 0.5);
      }
    }

    // 2. holders and banks.
    for (const node of model.nodes) {
      if (node.kind === "entity") continue;
      const on = bright(node);
      if (node.kind === "context") {
        ctx.globalAlpha = on ? 0.75 : 0.25;
        ctx.strokeStyle = colors.context;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
        continue;
      }
      const h = node.holder!;
      const color = colors.tiers[h.tier];
      if (h.tier >= view.flagTier && on) {
        const size = node.r * 7;
        ctx.globalAlpha = dimAll && node.id !== view.selectedId ? 0.4 : 0.9;
        ctx.drawImage(this.glowSprite(color), node.x - size / 2, node.y - size / 2, size, size);
        ctx.globalAlpha = 1;
      }
      this.dot(node, color, on ? (dimAll && node.id !== view.selectedId && !view.highlight ? 0.55 : 1) : 0.22);
      if (view.revealed && h.actual === 1) {
        const caught = h.tier >= view.flagTier;
        ctx.globalAlpha = on ? 1 : 0.3;
        ctx.strokeStyle = colors.ring;
        ctx.lineWidth = 1.3;
        ctx.setLineDash(caught ? [] : [2.2, 2]);
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.r + 2.6 * this.k, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      }
    }
    for (const node of model.nodes) {
      if (node.kind !== "entity") continue;
      const s = node.r;
      ctx.fillStyle = colors.halo;
      ctx.fillRect(node.x - s - 2, node.y - s - 2, 2 * s + 4, 2 * s + 4);
      ctx.fillStyle = colors.bank;
      ctx.fillRect(node.x - s, node.y - s, 2 * s, 2 * s);
      ctx.fillStyle = colors.halo;
      ctx.fillRect(node.x - s * 0.35, node.y - s * 0.35, s * 0.7, s * 0.7);
    }

    // 3. particles: each payment hopping payer -> payee, shaped by its format.
    if (animatedNow) {
      this.particles = this.particles.filter((p) => now - p.born < p.life);
      ctx.fillStyle = colors.money;
      ctx.strokeStyle = colors.money;
      for (const p of this.particles) {
        const t = (now - p.born) / p.life;
        if (t < 0) continue;
        const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // ease in-out
        const [x, y] = this.arcPoint(p.flow.source, p.flow.target, e);
        const [tx, ty] = this.arcPoint(p.flow.source, p.flow.target, Math.max(0, e - 0.12));
        ctx.globalAlpha = 0.55 * (1 - Math.abs(t - 0.5));
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(x, y);
        ctx.stroke();
        ctx.globalAlpha = Math.min(1, 0.4 + Math.sin(Math.PI * t) * 0.9);
        const s = (1.6 + Math.min(2.4, Math.log10(1 + p.amount) * 0.42)) * this.k;
        ctx.lineWidth = Math.max(1.4, s * 0.55);
        drawMark(ctx, FORMAT_SHAPES[p.flow.kind] ?? "circle", x, y, s);
      }
      ctx.globalAlpha = 1;
    }

    // 4. the selected holder: every flow (all ten days), arrowheaded, its counterparts ringed.
    if (selected) {
      const pulse = view.reducedMotion ? 0.5 : 0.5 + 0.5 * Math.sin(now / 380);
      const flows = model.adjacency.get(selected.id) ?? [];
      const caseId = selected.holder?.caseId;
      const caseSet = view.revealed && caseId ? new Set(model.caseMembers.get(caseId) ?? []) : null;
      if (caseSet) {
        for (const id of caseSet) {
          const n = model.byId.get(id);
          if (!n) continue;
          ctx.strokeStyle = colors.scheme;
          ctx.lineWidth = 1.6;
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 4.5 * this.k, 0, Math.PI * 2);
          ctx.stroke();
        }
        for (const f of model.flows) {
          if (caseSet.has(f.source.id) && caseSet.has(f.target.id)) this.strokeArc(f.source, f.target, 1.8, colors.scheme, 0.85, [5, 3]);
        }
      }
      const maxUsd = Math.max(1, ...flows.map((f) => f.weight));
      for (const f of flows) {
        const outgoing = f.source.id === selected.id;
        const other = outgoing ? f.target : f.source;
        const w = 1 + 2.4 * Math.sqrt(f.weight / maxUsd);
        this.strokeArc(f.source, f.target, w, outgoing ? colors.out : colors.in, 0.85);
        // arrowhead near the payee
        const [hx, hy] = this.arcPoint(f.source, f.target, 0.9);
        const [tx, ty] = this.arcPoint(f.source, f.target, 0.97);
        const ang = Math.atan2(ty - hy, tx - hx);
        ctx.fillStyle = outgoing ? colors.out : colors.in;
        ctx.beginPath();
        ctx.moveTo(tx + Math.cos(ang) * 3, ty + Math.sin(ang) * 3);
        ctx.lineTo(tx - Math.cos(ang - 0.5) * 5, ty - Math.sin(ang - 0.5) * 5);
        ctx.lineTo(tx - Math.cos(ang + 0.5) * 5, ty - Math.sin(ang + 0.5) * 5);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = colors.ink;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(other.x, other.y, other.r + 2, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.strokeStyle = colors.ink;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(selected.x, selected.y, selected.r + 4 + pulse * 4, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (this.hover && this.hover !== selected) {
      ctx.strokeStyle = colors.ink;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(this.hover.x, this.hover.y, this.hover.r + 3, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // ── pointer ───────────────────────────────────────────────────────────────

  private nodeAt(px: number, py: number): MtNode | null {
    let best: MtNode | null = null;
    let bestD = Infinity;
    const cx = Math.floor(px / CELL);
    const cy = Math.floor(py / CELL);
    for (let ix = cx - 1; ix <= cx + 1; ix++) {
      for (let iy = cy - 1; iy <= cy + 1; iy++) {
        for (const n of this.grid.get(ix * 10_000 + iy) ?? []) {
          const d = Math.hypot(n.x - px, n.y - py);
          const reach = Math.max(n.r + 3.5, 6);
          const score = d - (n.kind === "entity" ? 3 : 0);
          if (d <= reach && score < bestD) {
            best = n;
            bestD = score;
          }
        }
      }
    }
    return best;
  }

  private onMove = (e: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const node = this.nodeAt(e.clientX - rect.left, e.clientY - rect.top);
    if (node !== this.hover) {
      this.hover = node;
      this.canvas.style.cursor = node ? "pointer" : "default";
      this.kick();
    }
    this.cb.onHover(node, e.clientX - rect.left, e.clientY - rect.top);
  };

  private onLeave = (): void => {
    this.hover = null;
    this.cb.onHover(null, 0, 0);
    this.kick();
  };

  private onClick = (e: MouseEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const node = this.nodeAt(e.clientX - rect.left, e.clientY - rect.top);
    this.cb.onSelect(node && node.kind === "row" ? node.id : null);
  };
}
