/**
 * The Investigate view's stage — a 2D canvas that draws one subject's ego network
 * (networkInvestigator.ts `layoutEgo`): the subject at the centre, contacts around them
 * with mail flowing along the ties as particles (cyan = sent by the subject, ink =
 * received), documented relations as dashed hexagon links and the routes to the nearest
 * targets fanning out. Re-centring eases every node from where it was; the monthly
 * replay re-weights the ties. No zoom/pan: the whole ego fits the stage.
 *
 * Performance note (see project memory): glows are pre-rendered sprites blitted with
 * drawImage — never `fill()` under a composite mode, which crawls in software Chrome.
 */
import { windowWeight } from "./networkModel";
import type { EgoEdge, EgoLayout, EgoNode, Subject } from "./networkInvestigator";
import { NETWORK_PALETTE, RISK_RAMP, tierColor, type SurfaceMode } from "./riskPalette";

export type EgoView = {
  surface: SurfaceMode;
  period: number | null; // replay month index, null = the whole period
  hoveredId: string | null;
  selectedId: string | null; // a contact whose tie the side panel shows
  highlight: ReadonlySet<string>; // node ids of the route being traced
  targetIds: ReadonlySet<string>; // nodes drawn with a target ring
  reducedMotion: boolean;
};

export type EgoCallbacks = {
  onHover: (id: string | null, x: number, y: number) => void;
  onSelect: (id: string | null) => void;
};

type Particle = { edge: number; dir: 1 | -1; t: number; speed: number };
type Placed = { x: number; y: number; alpha: number };

const TRANSITION_MS = 650;
const DEFAULT_VIEW: EgoView = { surface: "dark", period: null, hoveredId: null, selectedId: null, highlight: new Set(), targetIds: new Set(), reducedMotion: false };

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

function hash01(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
}

export class EgoScene {
  private ctx: CanvasRenderingContext2D;
  private dpr = 1;
  private w = 0;
  private h = 0;
  private layout: EgoLayout | null = null;
  private subject: Subject | null = null;
  private view: EgoView = DEFAULT_VIEW;
  private from = new Map<string, Placed>();
  private started = 0;
  private particles: Particle[] = [];
  private windowMax = 1;
  private raf = 0;
  private last = 0;
  private sprites = new Map<string, HTMLCanvasElement>();
  private screen = new Map<string, { x: number; y: number; r: number }>();
  private dirty = true;
  private disposed = false;

  private canvas: HTMLCanvasElement;
  private cb: EgoCallbacks;

  constructor(canvas: HTMLCanvasElement, cb: EgoCallbacks) {
    this.canvas = canvas;
    this.cb = cb;
    this.ctx = canvas.getContext("2d")!;
    canvas.addEventListener("pointermove", this.onMove);
    canvas.addEventListener("pointerleave", this.onLeave);
    canvas.addEventListener("click", this.onClick);
    this.raf = requestAnimationFrame(this.frame);
  }

  destroy() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
    this.canvas.removeEventListener("click", this.onClick);
  }

  resize(w: number, h: number) {
    this.w = w;
    this.h = h;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.dirty = true;
  }

  setLayout(layout: EgoLayout, subject: Subject, periodCount: number) {
    // Nodes present before glide from where they are; new ones grow out of the centre.
    const now = performance.now();
    const previous = new Map<string, Placed>();
    if (this.layout) for (const n of this.layout.nodes) previous.set(n.id, this.place(n, now));
    this.from = previous;
    this.layout = layout;
    this.subject = subject;
    this.started = now;
    this.windowMax = 1;
    for (const c of subject.contacts) {
      if (!c.sentSeries || !c.receivedSeries) continue;
      for (let i = 0; i < periodCount; i++) this.windowMax = Math.max(this.windowMax, this.windowed(c.sentSeries, i) + this.windowed(c.receivedSeries, i));
    }
    this.particles = [];
    this.dirty = true;
  }

  setView(view: EgoView) {
    this.view = view;
    this.dirty = true;
  }

  // ── geometry ─────────────────────────────────────────────────────────────

  private windowed(series: Float32Array, period: number): number {
    return windowWeight({ series } as never, period);
  }

  private place(node: EgoNode, now: number): Placed {
    const t = this.view.reducedMotion ? 1 : Math.min(1, (now - this.started) / TRANSITION_MS);
    const e = easeInOut(t);
    const prev = this.from.get(node.id);
    const rx = this.w / 2 - 96;
    const ry = this.h / 2 - 46;
    const x = this.w / 2 + node.x * rx;
    const y = this.h / 2 + node.y * ry;
    if (!prev) return { x: this.w / 2 + (x - this.w / 2) * e, y: this.h / 2 + (y - this.h / 2) * e, alpha: e };
    return { x: prev.x + (x - prev.x) * e, y: prev.y + (y - prev.y) * e, alpha: 1 };
  }

  private radiusOf(n: EgoNode): number {
    if (n.subject) return 17;
    if (!n.node) return 6;
    if (n.node.kind === "entity") return 11;
    if (n.node.kind === "context") return 3.6;
    return Math.min(13, 4.6 + n.node.r * 0.85);
  }

  private hit(x: number, y: number): string | null {
    let best: string | null = null;
    let bestD = Infinity;
    for (const [id, s] of this.screen) {
      const d = Math.hypot(s.x - x, s.y - y);
      if (d < s.r + 7 && d < bestD) {
        best = id;
        bestD = d;
      }
    }
    return best;
  }

  private onMove = (e: PointerEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const id = this.hit(x, y);
    this.canvas.style.cursor = id ? "pointer" : "default";
    this.cb.onHover(id, x, y);
  };
  private onLeave = () => this.cb.onHover(null, 0, 0);
  private onClick = (e: MouseEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    this.cb.onSelect(this.hit(e.clientX - rect.left, e.clientY - rect.top));
  };

  // ── sprites ──────────────────────────────────────────────────────────────

  private glow(color: string): HTMLCanvasElement {
    let sprite = this.sprites.get(color);
    if (!sprite) {
      sprite = document.createElement("canvas");
      sprite.width = sprite.height = 96;
      const g = sprite.getContext("2d")!;
      const grad = g.createRadialGradient(48, 48, 2, 48, 48, 48);
      grad.addColorStop(0, color + "cc");
      grad.addColorStop(0.35, color + "44");
      grad.addColorStop(1, color + "00");
      g.fillStyle = grad;
      g.fillRect(0, 0, 96, 96);
      this.sprites.set(color, sprite);
    }
    return sprite;
  }

  // ── frame ────────────────────────────────────────────────────────────────

  private frame = (now: number) => {
    if (this.disposed) return;
    this.raf = requestAnimationFrame(this.frame);
    const dt = Math.min(64, now - (this.last || now));
    this.last = now;
    const animating = !this.view.reducedMotion && this.layout !== null;
    if (!animating && !this.dirty && now - this.started > TRANSITION_MS) return;
    this.dirty = false;
    this.draw(now, dt);
  };

  /** Flow weight of a tie in the current window, 0..1 of the strongest. */
  private tieWeight(edge: EgoEdge): { sent: number; received: number } {
    const c = edge.contact;
    if (!c || !this.layout) return { sent: 0, received: 0 };
    const period = this.view.period;
    if (period !== null && c.sentSeries && c.receivedSeries) {
      return { sent: this.windowed(c.sentSeries, period) / this.windowMax, received: this.windowed(c.receivedSeries, period) / this.windowMax };
    }
    const max = this.layout.maxTie;
    return { sent: c.sent / max, received: c.received / max };
  }

  private draw(now: number, dt: number) {
    const { ctx, layout, subject, w, h } = this;
    const view = this.view;
    const pal = NETWORK_PALETTE[view.surface];
    const ramp = RISK_RAMP[view.surface];
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const bg = ctx.createRadialGradient(w / 2, h / 2, 10, w / 2, h / 2, Math.max(w, h) * 0.65);
    bg.addColorStop(0, view.surface === "dark" ? "#101a33" : "#ffffff");
    bg.addColorStop(1, pal.stage);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    // concentric guides: direct contacts, then the routes beyond
    const rx = w / 2 - 96;
    const ry = h / 2 - 46;
    ctx.strokeStyle = pal.grid;
    ctx.lineWidth = 1;
    ctx.fillStyle = pal.dim;
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "center";
    for (const [r, label] of [
      [0.56, "direct ties"],
      [0.84, "2+ hops away"],
    ] as const) {
      ctx.beginPath();
      ctx.ellipse(w / 2, h / 2, rx * r, ry * r, 0, 0, Math.PI * 2);
      ctx.setLineDash([2, 6]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 0.7;
      ctx.fillText(label, w / 2, h / 2 - ry * r - 5);
      ctx.globalAlpha = 1;
    }
    if (!layout || !subject) return;

    const at = new Map<string, Placed>();
    this.screen.clear();
    for (const n of layout.nodes) {
      const p = this.place(n, now);
      at.set(n.id, p);
      this.screen.set(n.id, { x: p.x, y: p.y, r: this.radiusOf(n) });
    }
    const focus = view.hoveredId ?? view.selectedId;
    const focusNeighbours = new Set<string>();
    if (focus) {
      focusNeighbours.add(focus);
      for (const e of layout.edges) {
        if (e.a === focus) focusNeighbours.add(e.b);
        if (e.b === focus) focusNeighbours.add(e.a);
      }
    }
    const dim = (id: string) => (focus && !focusNeighbours.has(id) ? 0.3 : 1);
    const tracing = view.highlight.size > 0;

    // edges
    layout.edges.forEach((edge) => {
      const a = at.get(edge.a);
      const b = at.get(edge.b);
      if (!a || !b) return;
      const alpha = Math.min(a.alpha, b.alpha) * Math.min(dim(edge.a), dim(edge.b));
      const traced = view.highlight.has(edge.a) && view.highlight.has(edge.b) && edge.kind !== "mutual" && edge.kind !== "tie" ? true : tracing && edge.kind === "tie" && view.highlight.has(edge.b) && edge.a === subject.id;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      if (edge.kind === "mutual") {
        ctx.quadraticCurveTo(w / 2 + (a.x + b.x - w) * 0.3, h / 2 + (a.y + b.y - h) * 0.3, b.x, b.y);
        ctx.strokeStyle = pal.link;
        ctx.globalAlpha = 0.11 * alpha;
        ctx.lineWidth = 1;
      } else if (edge.kind === "relation") {
        ctx.lineTo(b.x, b.y);
        ctx.setLineDash([5, 5]);
        ctx.strokeStyle = pal.entity;
        ctx.globalAlpha = 0.85 * alpha;
        ctx.lineWidth = 1.6;
      } else if (edge.kind === "route") {
        ctx.lineTo(b.x, b.y);
        const on = view.highlight.has(edge.a) && view.highlight.has(edge.b);
        ctx.strokeStyle = on ? pal.path : pal.link;
        ctx.globalAlpha = (on ? 0.95 : 0.35) * alpha;
        ctx.lineWidth = on ? 2.6 : 1.2;
        if (!edge.weight) ctx.setLineDash([4, 5]);
      } else {
        ctx.lineTo(b.x, b.y);
        const { sent, received } = this.tieWeight(edge);
        const weight = Math.min(1, sent + received);
        ctx.strokeStyle = traced ? pal.path : pal.link;
        ctx.globalAlpha = (view.period === null ? 0.3 + 0.55 * Math.sqrt(weight) : 0.06 + 0.8 * Math.sqrt(weight)) * alpha;
        ctx.lineWidth = 0.8 + 3.6 * Math.sqrt(weight);
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    });

    // mail in flight
    if (!view.reducedMotion) this.flow(at, dt, pal.path, pal.ink, dim);
    else this.staticFlow(at, pal.path, pal.ink);

    // nodes (back to front: context, entities, people, subject)
    const order = [...layout.nodes].sort((a, b) => this.rankOf(a) - this.rankOf(b));
    for (const n of order) {
      const p = at.get(n.id)!;
      const r = this.radiusOf(n);
      const a = p.alpha * dim(n.id);
      const target = view.targetIds.has(n.id);
      const traced = view.highlight.has(n.id);
      ctx.globalAlpha = a;
      if (n.subject) this.drawSubject(n, p, r, now, pal.ink, ramp.ring);
      else if (n.node?.kind === "entity") this.hex(p.x, p.y, r, pal.entity, view.surface === "dark" ? "#0a1020" : "#f6f8fb");
      else if (n.node?.kind === "context") {
        ctx.fillStyle = pal.context;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
      } else if (n.node?.row) {
        const color = tierColor(view.surface, n.node.row.tier);
        if (n.node.row.tier >= 1) ctx.drawImage(this.glow(color), p.x - r * 3, p.y - r * 3, r * 6, r * 6);
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = view.surface === "dark" ? "#0a1020" : "#ffffff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      if (target && !n.subject) {
        ctx.strokeStyle = ramp.ring;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 4.5, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (traced && !n.subject) {
        ctx.strokeStyle = pal.path;
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 8, 0, Math.PI * 2);
        ctx.stroke();
      }
      if ((n.id === view.selectedId || n.id === view.hoveredId) && !n.subject) {
        ctx.strokeStyle = pal.ink;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 10, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // labels
    const labelled = new Set<string>();
    [...layout.nodes]
      .filter((n) => n.ring === 1 && n.contact && n.node?.kind === "row")
      .sort((a, b) => b.contact!.total - a.contact!.total)
      .slice(0, 9)
      .forEach((n) => labelled.add(n.id));
    ctx.font = "600 11.5px system-ui, sans-serif";
    for (const n of layout.nodes) {
      const p = at.get(n.id)!;
      const isKey = n.subject || n.ring === 2 || n.node?.kind === "entity" || view.targetIds.has(n.id) || labelled.has(n.id) || n.id === view.hoveredId || n.id === view.selectedId || view.highlight.has(n.id);
      if (!isKey || n.node?.kind === "context") continue;
      const r = this.radiusOf(n);
      const text = n.subject ? subject.label : n.node!.label;
      const right = n.subject ? true : n.x >= -0.02;
      ctx.globalAlpha = p.alpha * dim(n.id);
      ctx.textAlign = n.subject ? "center" : right ? "left" : "right";
      const tx = n.subject ? p.x : p.x + (right ? 1 : -1) * (r + 7);
      const ty = n.subject ? p.y + r + 17 : p.y + 4;
      ctx.lineJoin = "round";
      ctx.lineWidth = 4;
      ctx.strokeStyle = pal.halo;
      ctx.strokeText(text, tx, ty);
      ctx.fillStyle = n.subject ? pal.ink : view.highlight.has(n.id) ? pal.path : pal.ink;
      ctx.fillText(text, tx, ty);
      ctx.globalAlpha = 1;
    }
  }

  private rankOf(n: EgoNode): number {
    if (n.subject) return 5;
    if (n.node?.kind === "context") return 0;
    if (n.node?.kind === "entity") return 1;
    return 2 + (n.node?.row?.tier ?? 0) * 0.1;
  }

  private hex(x: number, y: number, r: number, color: string, fill: string) {
    const ctx = this.ctx;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = Math.PI / 6 + (i * Math.PI) / 3;
      const px = x + Math.cos(angle) * r;
      const py = y + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  private drawSubject(n: EgoNode, p: Placed, r: number, now: number, ink: string, ring: string) {
    const { ctx, view, subject } = this;
    const tier = subject?.tier ?? 0;
    const color = tier !== null && subject?.probability !== null ? tierColor(view.surface, tier) : ink;
    const pulse = view.reducedMotion ? 0.5 : 0.5 + 0.5 * Math.sin(now / 520);
    ctx.drawImage(this.glow(color), p.x - r * 4.2, p.y - r * 4.2, r * 8.4, r * 8.4);
    ctx.strokeStyle = color;
    ctx.globalAlpha *= 0.55 - 0.3 * pulse;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r + 7 + pulse * 9, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = Math.min(1, p.alpha);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = ring;
    ctx.lineWidth = 2.4;
    if (n.node === null) ctx.setLineDash([4, 3]); // hypothetical
    ctx.beginPath();
    ctx.arc(p.x, p.y, r + 2.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // ── mail in flight ───────────────────────────────────────────────────────

  private targetCount(weight: number): number {
    return weight <= 0.004 ? 0 : Math.min(9, Math.max(1, Math.round(1 + 7 * Math.sqrt(weight))));
  }

  private flow(at: Map<string, Placed>, dt: number, sentColor: string, receivedColor: string, dim: (id: string) => number) {
    const { layout, ctx } = this;
    if (!layout) return;
    const wanted: Record<string, number> = {};
    layout.edges.forEach((edge, i) => {
      if (edge.kind !== "tie") return;
      const { sent, received } = this.tieWeight(edge);
      wanted[`${i}|1`] = this.targetCount(sent);
      wanted[`${i}|-1`] = this.targetCount(received);
    });
    const counts: Record<string, number> = {};
    this.particles = this.particles.filter((p) => {
      const key = `${p.edge}|${p.dir}`;
      counts[key] = (counts[key] ?? 0) + 1;
      return counts[key] <= (wanted[key] ?? 0);
    });
    for (const [key, n] of Object.entries(wanted)) {
      const [edge, dir] = key.split("|");
      for (let c = counts[key] ?? 0; c < n; c++) {
        this.particles.push({ edge: Number(edge), dir: Number(dir) as 1 | -1, t: Math.random(), speed: 0.00022 + hash01(`${edge}${c}`) * 0.00018 });
      }
    }
    for (const p of this.particles) {
      p.t += p.speed * dt;
      if (p.t > 1) p.t -= 1;
      const edge = layout.edges[p.edge];
      const a = at.get(edge.a);
      const b = at.get(edge.b);
      if (!a || !b) continue;
      const t = p.dir === 1 ? p.t : 1 - p.t;
      const x = a.x + (b.x - a.x) * t;
      const y = a.y + (b.y - a.y) * t;
      ctx.globalAlpha = Math.min(a.alpha, b.alpha) * dim(edge.b) * (0.35 + 0.65 * Math.sin(Math.PI * p.t));
      ctx.fillStyle = p.dir === 1 ? sentColor : receivedColor;
      ctx.beginPath();
      ctx.arc(x, y, 1.9, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  /** Reduced motion: a few still dots per tie, so direction and volume remain readable. */
  private staticFlow(at: Map<string, Placed>, sentColor: string, receivedColor: string) {
    const { layout, ctx } = this;
    if (!layout) return;
    layout.edges.forEach((edge) => {
      if (edge.kind !== "tie") return;
      const a = at.get(edge.a);
      const b = at.get(edge.b);
      if (!a || !b) return;
      const { sent, received } = this.tieWeight(edge);
      for (const [weight, color, offset] of [
        [sent, sentColor, 0.3],
        [received, receivedColor, 0.7],
      ] as const) {
        const n = this.targetCount(weight);
        for (let i = 0; i < Math.min(n, 4); i++) {
          const t = offset + (i - 1.5) * 0.12;
          ctx.fillStyle = color;
          ctx.globalAlpha = 0.8;
          ctx.beginPath();
          ctx.arc(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, 1.9, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    });
    ctx.globalAlpha = 1;
  }
}
