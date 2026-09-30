/**
 * The network explorer's stage: an imperative canvas renderer (d3-force layout,
 * d3-zoom camera) the React view drives through `setNetwork` / `setView`. Canvas, not
 * SVG: ~250 glowing nodes, ~3,000 curved links and moving particles at 60 fps.
 *
 * Layout: seeded force simulation, pre-warmed off-screen so the board opens settled;
 * a weak pull towards per-community anchors keeps communities readable; rows with no
 * link at all sit on an outer orbit. Modes recolour, they never move nodes (the mental
 * map stays put). Rendering is dirty-flag driven — the animation loop only runs while
 * something moves (camera, particles, pulse, reveal, drag) and pauses when the page
 * or the stage is hidden.
 */
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation } from "d3-force";
import { interpolateZoom } from "d3-interpolate";
import { select, type Selection } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { type Network, type NxLink, type NxNode, periodLabel, windowWeight } from "./networkModel";
import { NETWORK_PALETTE, type SurfaceMode, tierColor } from "./riskPalette";

export type SceneMode = "risk" | "communities";

export type SceneView = {
  mode: SceneMode;
  surface: SurfaceMode;
  selectedId: string | null;
  hoveredId: string | null;
  path: string[];
  period: number | null; // replay month; null = the whole period
  playing: boolean;
  revealAt: number | null; // performance.now() of the reveal; null = hidden
  flagTier: number;
  tierFilter: ReadonlySet<number>;
  reducedMotion: boolean;
  pinned: ReadonlySet<string>; // always labelled (e.g. the top scores)
};

export type SceneCallbacks = {
  onHover: (id: string | null, x: number, y: number) => void;
  onSelect: (id: string | null) => void;
  onReady?: () => void;
};

type SimLink = { source: NxNode; target: NxNode; weight: number; flow: boolean };
type LabelBox = { x: number; y: number; w: number; h: number };

const WARM_TICKS = 320;
const REVEAL_STEP_MS = 110;
const INTRO_MS = 1900;

function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (1664525 * s + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function hash01(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 1000) / 1000;
}

function rgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, (c) => c + c) : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function easeInOut(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/** Monotone-chain convex hull of 2-D points. */
function convexHull(points: [number, number][]): [number, number][] {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o: number[], a: number[], b: number[]) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

export class NetworkScene {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly callbacks: SceneCallbacks;
  private readonly selection: Selection<HTMLCanvasElement, unknown, null, undefined>;
  private readonly zoomBehavior: ZoomBehavior<HTMLCanvasElement, unknown>;
  private net: Network | null = null;
  private view: SceneView | null = null;
  private sim: Simulation<NxNode, SimLink> | null = null;
  private transform: ZoomTransform = zoomIdentity;
  private width = 0;
  private height = 0;
  private dpr = 1;
  private raf = 0;
  private dirty = true;
  private visible = true;
  private readyFired = false;
  private orbitRadius = 0;
  private maxWindow = 1;
  private revealOrder = new Map<string, number>();
  private focusIds = new Set<string>();
  private pathLinks = new Set<NxLink>();
  private hulls: { color: string; name: string; size: number; hull: [number, number][] }[] = [];
  private hullsStale = true;
  private camera: { start: number; duration: number; interp: ReturnType<typeof interpolateZoom> } | null = null;
  private drag: { node: NxNode; moved: boolean; x0: number; y0: number } | null = null;
  private press: { x: number; y: number } | null = null;
  private hoverId: string | null = null;
  private readonly sprites = new Map<string, HTMLCanvasElement>();
  private bg: { key: string; canvas: HTMLCanvasElement } | null = null;
  private grid: { key: string; pattern: CanvasPattern | null } | null = null;
  private drawMs = 8; // moving average of draw() time, for frame pacing
  private gapMs = 16; // moving average of the gap between animated frames
  private gapSamples = 0;
  private dprCap = 2; // lowered to 1 once if a (GPU-less) machine can't keep up
  private readonly textWidths = new Map<string, number>();
  private readonly observer: IntersectionObserver | null;
  private lastFrame = 0;
  // The opening: the settled layout spirals out from the centre (skipped with reduced motion).
  private targets = new Map<NxNode, [number, number]>();
  private intro: { start: number } | null = null;

  constructor(canvas: HTMLCanvasElement, callbacks: SceneCallbacks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.callbacks = callbacks;
    this.selection = select(canvas);
    this.zoomBehavior = zoom<HTMLCanvasElement, unknown>()
      .scaleExtent([0.25, 8])
      .filter((event: Event) => {
        if (event.type === "wheel") return true;
        const pointer = event as MouseEvent;
        if (pointer.button) return false;
        if (event.type === "mousedown" || event.type === "touchstart") return !this.nodeAtClient(event);
        return true;
      })
      .on("zoom", (event: { transform: ZoomTransform }) => {
        this.transform = event.transform;
        this.invalidate();
      });
    this.selection.call(this.zoomBehavior).on("dblclick.zoom", null);
    canvas.addEventListener("pointerdown", this.onPointerDown);
    canvas.addEventListener("pointermove", this.onPointerMove);
    canvas.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("pointerleave", this.onPointerLeave);
    document.addEventListener("visibilitychange", this.invalidate);
    this.observer =
      typeof IntersectionObserver !== "undefined"
        ? new IntersectionObserver((entries) => {
            this.visible = entries.some((e) => e.isIntersecting);
            this.invalidate();
          })
        : null;
    this.observer?.observe(canvas);
  }

  destroy(): void {
    cancelAnimationFrame(this.raf);
    this.sim?.stop();
    this.observer?.disconnect();
    this.selection.on(".zoom", null);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
    this.canvas.removeEventListener("pointerleave", this.onPointerLeave);
    document.removeEventListener("visibilitychange", this.invalidate);
  }

  // ── public API ────────────────────────────────────────────────────────────

  resize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.dpr = Math.min(this.dprCap, window.devicePixelRatio || 1);
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;
    this.zoomBehavior.extent([
      [0, 0],
      [this.width, this.height],
    ]);
    this.invalidate();
  }

  setNetwork(net: Network): void {
    this.net = net;
    this.sim?.stop();
    const active = net.nodes.filter((n) => !n.orbit);
    const pairs = new Map<string, SimLink>();
    for (const link of net.links) {
      if (link.source.orbit || link.target.orbit) continue;
      const key = link.source.id < link.target.id ? `${link.source.id}|${link.target.id}` : `${link.target.id}|${link.source.id}`;
      const pair = pairs.get(key);
      if (pair) pair.weight += link.weight;
      else pairs.set(key, { source: link.source, target: link.target, weight: link.flow ? link.weight : 12, flow: link.flow });
    }
    // Community anchors on a circle: a weak pull keeps each community a readable cluster.
    const anchors = net.groups.map((_, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, net.groups.length) - Math.PI / 2;
      const radius = 200 + 45 * Math.min(4, net.groups.length);
      return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
    });
    const simDegree = new Map<string, number>();
    for (const pair of pairs.values()) {
      simDegree.set(pair.source.id, (simDegree.get(pair.source.id) ?? 0) + 1);
      simDegree.set(pair.target.id, (simDegree.get(pair.target.id) ?? 0) + 1);
    }
    const hubness = (l: SimLink) => Math.max(1, Math.min(simDegree.get(l.source.id) ?? 1, simDegree.get(l.target.id) ?? 1));
    const random = lcg(42);
    for (const node of active) {
      const anchor = node.groupIndex >= 0 ? anchors[node.groupIndex] : { x: 0, y: 0 };
      node.x = anchor.x + (random() - 0.5) * 120;
      node.y = anchor.y + (random() - 0.5) * 120;
    }
    this.sim = forceSimulation<NxNode, SimLink>(active)
      .randomSource(lcg(7))
      .velocityDecay(0.42)
      .force(
        "link",
        // d3's degree-normalised strength, scaled by the tie's weight: hubs don't
        // collapse everything onto themselves.
        forceLink<NxNode, SimLink>([...pairs.values()])
          .distance((l) => (l.flow ? 46 + 90 / Math.sqrt(1 + l.weight) : 44))
          .strength((l) => (l.flow ? Math.min(1, (0.35 + Math.log1p(l.weight) / 5) / hubness(l)) : 0.5)),
      )
      .force(
        "charge",
        forceManyBody<NxNode>()
          .strength((n) => (n.kind === "context" ? -40 : n.kind === "entity" ? -90 : -80 - 7 * n.r))
          .distanceMax(900),
      )
      .force("collide", forceCollide<NxNode>((n) => n.r + 4).iterations(2))
      .force("x", forceX<NxNode>((n) => (n.groupIndex >= 0 ? anchors[n.groupIndex].x : 0)).strength((n) => (n.groupIndex >= 0 ? 0.035 : 0.015)))
      .force("y", forceY<NxNode>((n) => (n.groupIndex >= 0 ? anchors[n.groupIndex].y : 0)).strength((n) => (n.groupIndex >= 0 ? 0.035 : 0.015)))
      .stop();
    for (let i = 0; i < WARM_TICKS; i++) this.sim.tick();
    // Centre the settled layout on the origin (the orbit ring is drawn around it).
    let cx = 0;
    let cy = 0;
    for (const node of active) {
      cx += (node.x ?? 0) / active.length;
      cy += (node.y ?? 0) / active.length;
    }
    for (const node of active) {
      node.x = (node.x ?? 0) - cx;
      node.y = (node.y ?? 0) - cy;
    }
    this.sim.on("tick", () => {
      this.hullsStale = true;
      this.invalidate();
    });

    // Rows with no link at all: an outer orbit hugging the network (its 92nd-percentile
    // radius — a few far-flung leaves shouldn't push the ring out), highest score first.
    const radii = active.map((n) => Math.hypot(n.x ?? 0, n.y ?? 0) + n.r).sort((a, b) => a - b);
    this.orbitRadius = (radii[Math.floor(radii.length * 0.92)] ?? 200) + 60;
    const orbit = net.nodes.filter((n) => n.orbit).sort((a, b) => (b.row?.probability ?? 0) - (a.row?.probability ?? 0));
    orbit.forEach((node, i) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, orbit.length);
      node.x = Math.cos(angle) * this.orbitRadius;
      node.y = Math.sin(angle) * this.orbitRadius;
      node.fx = node.x;
      node.fy = node.y;
    });

    this.maxWindow = 1;
    for (const link of net.links) {
      if (!link.series) continue;
      for (let p = 0; p < link.series.length; p++) this.maxWindow = Math.max(this.maxWindow, windowWeight(link, p));
    }
    this.targets = new Map(net.nodes.map((n) => [n, [n.x ?? 0, n.y ?? 0] as [number, number]]));
    this.intro = { start: 0 };
    const positives = net.rows.filter((n) => n.row?.actual === 1).sort((a, b) => (b.row?.probability ?? 0) - (a.row?.probability ?? 0));
    this.revealOrder = new Map(positives.map((n, i) => [n.id, i]));
    this.hullsStale = true;
    this.fit(false);
  }

  setView(view: SceneView): void {
    const previous = this.view;
    this.view = view;
    if (!this.net) return;
    const focus = view.hoveredId ?? view.selectedId;
    this.focusIds = new Set(focus ? [focus, ...(this.net.adjacency.get(focus) ?? []).map((x) => x.node.id)] : []);
    this.pathLinks = new Set();
    for (let i = 1; i < view.path.length; i++) {
      for (const { node, link } of this.net.adjacency.get(view.path[i - 1]) ?? []) if (node.id === view.path[i]) this.pathLinks.add(link);
    }
    if (previous?.mode !== view.mode) this.hullsStale = true;
    this.invalidate();
  }

  /** Ease the camera onto `ids` (e.g. a node and its neighbours). */
  focusOn(ids: string[], animate = true): void {
    if (!this.net) return;
    const nodes = ids.map((id) => this.net!.byId.get(id)).filter((n): n is NxNode => !!n);
    if (!nodes.length) return;
    this.animateTo(this.frame(nodes, 90, 3.2), animate);
  }

  fit(animate = true): void {
    if (!this.net) return;
    this.animateTo(this.frame(this.net.nodes, 28, 1.6), animate);
  }

  zoomBy(factor: number): void {
    this.selection.call(this.zoomBehavior.scaleBy, factor);
  }

  /** Screen position of a node (for tooltips anchored to it). */
  screenOf(id: string): { x: number; y: number } | null {
    const node = this.net?.byId.get(id);
    if (!node) return null;
    return { x: this.transform.applyX(node.x ?? 0), y: this.transform.applyY(node.y ?? 0) };
  }

  // ── camera ────────────────────────────────────────────────────────────────

  private frame(nodes: NxNode[], padding: number, maxK: number): ZoomTransform {
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    for (const n of nodes) {
      x0 = Math.min(x0, (n.x ?? 0) - n.r);
      y0 = Math.min(y0, (n.y ?? 0) - n.r);
      x1 = Math.max(x1, (n.x ?? 0) + n.r);
      y1 = Math.max(y1, (n.y ?? 0) + n.r);
    }
    const w = Math.max(40, x1 - x0);
    const h = Math.max(40, y1 - y0);
    const k = Math.min(maxK, Math.max(0.25, Math.min((this.width - 2 * padding) / w, (this.height - 2 * padding) / h)));
    return zoomIdentity.translate(this.width / 2 - k * (x0 + w / 2), this.height / 2 - k * (y0 + h / 2)).scale(k);
  }

  private animateTo(target: ZoomTransform, animate: boolean): void {
    if (!animate || this.view?.reducedMotion || this.width < 2) {
      this.camera = null;
      this.selection.call(this.zoomBehavior.transform, target);
      return;
    }
    const t0 = this.transform;
    const view = (t: ZoomTransform): [number, number, number] => [(this.width / 2 - t.x) / t.k, (this.height / 2 - t.y) / t.k, this.width / t.k];
    const interp = interpolateZoom(view(t0), view(target));
    this.camera = { start: performance.now(), duration: Math.min(1300, Math.max(520, interp.duration * 0.7)), interp };
    this.invalidate();
  }

  // ── input ─────────────────────────────────────────────────────────────────

  private pointerWorld(event: { clientX: number; clientY: number }): [number, number, number, number] {
    const rect = this.canvas.getBoundingClientRect();
    const sx = event.clientX - rect.left;
    const sy = event.clientY - rect.top;
    const [wx, wy] = this.transform.invert([sx, sy]);
    return [wx, wy, sx, sy];
  }

  private nodeAt(wx: number, wy: number): NxNode | null {
    if (!this.net) return null;
    const slack = 5 / this.transform.k;
    let best: NxNode | null = null;
    let bestD = Infinity;
    for (const node of this.net.nodes) {
      const d = Math.hypot((node.x ?? 0) - wx, (node.y ?? 0) - wy);
      const reach = Math.max(node.r, 3 / this.transform.k) + slack;
      if (d <= reach && d < bestD) {
        best = node;
        bestD = d;
      }
    }
    return best;
  }

  private nodeAtClient(event: Event): NxNode | null {
    const touch = (event as TouchEvent).touches?.[0];
    const point = touch ?? (event as MouseEvent);
    if (point?.clientX === undefined) return null;
    const [wx, wy] = this.pointerWorld(point);
    return this.nodeAt(wx, wy);
  }

  private readonly onPointerDown = (event: PointerEvent) => {
    if (event.button) return;
    const [wx, wy, sx, sy] = this.pointerWorld(event);
    this.press = { x: sx, y: sy };
    const node = this.nodeAt(wx, wy);
    if (!node) return;
    this.drag = { node, moved: false, x0: sx, y0: sy };
    this.canvas.setPointerCapture(event.pointerId);
    if (!node.orbit) {
      node.fx = node.x;
      node.fy = node.y;
    }
  };

  private readonly onPointerMove = (event: PointerEvent) => {
    const [wx, wy, sx, sy] = this.pointerWorld(event);
    if (this.drag) {
      if (!this.drag.moved && Math.hypot(sx - this.drag.x0, sy - this.drag.y0) < 4) return;
      this.drag.moved = true;
      const node = this.drag.node;
      if (node.orbit) {
        node.x = wx;
        node.y = wy;
        node.fx = wx;
        node.fy = wy;
      } else {
        node.fx = wx;
        node.fy = wy;
        if (this.sim && this.sim.alpha() < 0.05) this.sim.alphaTarget(0.12).restart();
      }
      this.hullsStale = true;
      this.invalidate();
      return;
    }
    const node = this.nodeAt(wx, wy);
    const id = node?.id ?? null;
    this.canvas.style.cursor = node ? "pointer" : "grab";
    if (id !== this.hoverId) {
      this.hoverId = id;
      this.callbacks.onHover(id, sx, sy);
    } else if (id) {
      this.callbacks.onHover(id, sx, sy);
    }
  };

  private readonly onPointerUp = (event: PointerEvent) => {
    const [, , sx, sy] = this.pointerWorld(event);
    const drag = this.drag;
    this.drag = null;
    if (drag) {
      if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
      if (!drag.node.orbit) {
        drag.node.fx = null;
        drag.node.fy = null;
        this.sim?.alphaTarget(0);
      }
      if (!drag.moved) this.callbacks.onSelect(drag.node.id);
      return;
    }
    if (this.press && Math.hypot(sx - this.press.x, sy - this.press.y) < 4) this.callbacks.onSelect(null);
    this.press = null;
  };

  private readonly onPointerLeave = () => {
    if (this.hoverId !== null) {
      this.hoverId = null;
      this.callbacks.onHover(null, 0, 0);
    }
  };

  // ── loop ──────────────────────────────────────────────────────────────────

  private readonly invalidate = () => {
    this.dirty = true;
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  };

  private animating(now: number): boolean {
    const v = this.view;
    if (!v) return false;
    if (this.intro || this.camera || this.drag || (this.sim && this.sim.alpha() > this.sim.alphaMin())) return true;
    if (v.reducedMotion) return false;
    if (v.playing || v.selectedId || v.hoveredId || v.path.length) return true;
    if (v.revealAt !== null && now - v.revealAt < REVEAL_STEP_MS * (this.revealOrder.size + 4)) return true;
    return v.mode !== "communities"; // the top tier's glow breathes
  }

  private readonly loop = (now: number) => {
    this.raf = 0;
    if (document.visibilityState === "hidden" || !this.visible) return;
    const animating = this.animating(now);
    // Ambient-only motion (the breathing glow) runs at ~30 fps; any animation leaves the
    // main thread at least half idle on a slow (e.g. GPU-less) machine.
    const ambient = animating && !this.camera && !this.drag && !this.view?.playing && !this.view?.selectedId && !this.view?.hoveredId;
    const interval = Math.max(ambient ? 32 : 0, Math.min(250, 2 * this.drawMs));
    if (this.dirty || (animating && now - this.lastFrame >= interval)) {
      const gap = now - this.lastFrame;
      if (animating && this.lastFrame && gap < 800) {
        this.gapMs = 0.9 * this.gapMs + 0.1 * gap;
        this.gapSamples += 1;
        // Rasterising a HiDPI canvas in software (no GPU) can take hundreds of ms a frame:
        // trade sharpness for fluidity, once.
        if (this.gapSamples > 30 && this.gapMs > 55 && this.dprCap > 1 && this.dpr > 1) {
          this.dprCap = 1;
          this.bg = null;
          this.grid = null;
          this.resize(this.width, this.height);
        }
      }
      this.dirty = false;
      this.lastFrame = now;
      const started = performance.now();
      this.stepIntro(now);
      this.stepCamera(now);
      this.draw(now);
      this.drawMs = 0.8 * this.drawMs + 0.2 * (performance.now() - started);
      if (!this.readyFired && this.net && !this.intro) {
        this.readyFired = true;
        this.callbacks.onReady?.();
      }
    }
    if (animating || this.dirty) this.raf = requestAnimationFrame(this.loop);
  };

  private stepIntro(now: number): void {
    if (!this.intro) return;
    if (this.view?.reducedMotion || !this.view) {
      this.intro = null;
      return;
    }
    if (!this.intro.start) this.intro.start = now;
    const p = Math.min(1, (now - this.intro.start) / INTRO_MS);
    for (const [node, [tx, ty]] of this.targets) {
      // Inner nodes arrive first; everything turns a little as it unfolds.
      const lag = Math.min(0.35, Math.hypot(tx, ty) / 2400);
      const q = easeInOut(Math.max(0, Math.min(1, (p - lag) / (1 - lag))));
      const scale = 0.04 + 0.96 * q;
      const turn = (1 - q) * 1.1;
      const x = tx * scale;
      const y = ty * scale;
      node.x = x * Math.cos(turn) - y * Math.sin(turn);
      node.y = x * Math.sin(turn) + y * Math.cos(turn);
      if (node.orbit) {
        node.fx = node.x;
        node.fy = node.y;
      }
    }
    if (p >= 1) {
      this.intro = null;
      this.hullsStale = true;
    }
  }

  private stepCamera(now: number): void {
    if (!this.camera) return;
    const p = Math.min(1, (now - this.camera.start) / this.camera.duration);
    const [cx, cy, w] = this.camera.interp(easeInOut(p));
    const k = this.width / w;
    const t = zoomIdentity.translate(this.width / 2 - cx * k, this.height / 2 - cy * k).scale(k);
    if (p >= 1) this.camera = null;
    this.selection.call(this.zoomBehavior.transform, t);
  }

  // ── drawing ───────────────────────────────────────────────────────────────

  private sprite(color: string): HTMLCanvasElement {
    let sprite = this.sprites.get(color);
    if (!sprite) {
      sprite = document.createElement("canvas");
      sprite.width = sprite.height = 64;
      const g = sprite.getContext("2d")!;
      const gradient = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      gradient.addColorStop(0, rgba(color, 0.62));
      gradient.addColorStop(0.35, rgba(color, 0.22));
      gradient.addColorStop(1, rgba(color, 0));
      g.fillStyle = gradient;
      g.fillRect(0, 0, 64, 64);
      this.sprites.set(color, sprite);
    }
    return sprite;
  }

  private dot(color: string): HTMLCanvasElement {
    const key = `dot|${color}`;
    let dot = this.sprites.get(key);
    if (!dot) {
      dot = document.createElement("canvas");
      dot.width = dot.height = 16;
      const g = dot.getContext("2d")!;
      g.fillStyle = color;
      g.beginPath();
      g.arc(8, 8, 7.5, 0, 2 * Math.PI);
      g.fill();
      this.sprites.set(key, dot);
    }
    return dot;
  }

  /** The stage surface + vignette, rendered once per size and surface. */
  private background(palette: (typeof NETWORK_PALETTE)[SurfaceMode], dark: boolean): HTMLCanvasElement {
    const key = `${this.width}x${this.height}@${this.dpr}|${palette.stage}`;
    if (this.bg?.key !== key) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(this.width * this.dpr);
      canvas.height = Math.round(this.height * this.dpr);
      const g = canvas.getContext("2d")!;
      g.scale(this.dpr, this.dpr);
      g.fillStyle = palette.stage;
      g.fillRect(0, 0, this.width, this.height);
      const vignette = g.createRadialGradient(this.width / 2, this.height / 2, Math.min(this.width, this.height) * 0.25, this.width / 2, this.height / 2, Math.max(this.width, this.height) * 0.75);
      vignette.addColorStop(0, rgba(palette.stage, 0));
      vignette.addColorStop(1, rgba(palette.stageEdge, dark ? 0.9 : 0.7));
      g.fillStyle = vignette;
      g.fillRect(0, 0, this.width, this.height);
      this.bg = { key, canvas };
    }
    return this.bg.canvas;
  }

  /** The evidence-board dot grid as one pattern fill (a tile per grid step). */
  private gridPattern(step: number, color: string): CanvasPattern | null {
    const size = Math.round(step * 2) / 2;
    const key = `${size}|${color}|${this.dpr}`;
    if (this.grid?.key !== key) {
      const tile = document.createElement("canvas");
      tile.width = tile.height = Math.max(1, Math.round(size * this.dpr));
      const g = tile.getContext("2d")!;
      g.fillStyle = color;
      const d = 1.5 * this.dpr;
      g.fillRect(0, 0, d, d);
      this.grid = { key, pattern: this.ctx.createPattern(tile, "repeat") };
    }
    return this.grid.pattern;
  }

  private nodeColor(node: NxNode, palette: (typeof NETWORK_PALETTE)[SurfaceMode]): string {
    const view = this.view!;
    if (node.kind === "entity") return this.entityColor(palette);
    if (node.kind === "context") return palette.context;
    if (view.mode === "communities") {
      const group = node.groupIndex >= 0 ? this.net!.groups[node.groupIndex] : null;
      return group && group.colorIndex >= 0 ? palette.communities[group.colorIndex] : palette.other;
    }
    return tierColor(view.surface, node.row?.tier ?? 0);
  }

  private entityColor(palette: (typeof NETWORK_PALETTE)[SurfaceMode]): string {
    return this.view?.mode === "communities" ? palette.dim : palette.entity;
  }

  private filteredOut(node: NxNode): boolean {
    const filter = this.view!.tierFilter;
    return filter.size > 0 && (node.kind !== "row" || !filter.has(node.row?.tier ?? 0));
  }

  private linkControl(link: NxLink): [number, number] {
    const sx = link.source.x ?? 0;
    const sy = link.source.y ?? 0;
    const tx = link.target.x ?? 0;
    const ty = link.target.y ?? 0;
    const bend = link.flow ? 0.14 : 0.06;
    return [(sx + tx) / 2 - (ty - sy) * bend, (sy + ty) / 2 + (tx - sx) * bend];
  }

  private draw(now: number): void {
    const net = this.net;
    const view = this.view;
    const ctx = this.ctx;
    if (!net || !view) return;
    const palette = NETWORK_PALETTE[view.surface];
    const dark = view.surface === "dark";
    const t = this.transform;
    const k = t.k;
    const dpr = this.dpr;

    // Background: surface + vignette (cached), a faint evidence-board dot grid.
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.drawImage(this.background(palette, dark), 0, 0);
    const step = 26 * k;
    const pattern = step > 9 && step < 160 ? this.gridPattern(step, palette.grid) : null;
    if (pattern) {
      const size = Math.round(step * 2) / 2;
      pattern.setTransform(new DOMMatrix().translate(dpr * ((((t.x - 0.75) % size) + size) % size), dpr * ((((t.y - 0.75) % size) + size) % size)));
      ctx.fillStyle = pattern;
      ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    }

    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * t.x, dpr * t.y);

    // Orbit ring for rows without any link.
    if (net.nodes.some((n) => n.orbit)) {
      ctx.beginPath();
      ctx.arc(0, 0, this.orbitRadius, 0, 2 * Math.PI);
      ctx.setLineDash([3 / k, 7 / k]);
      ctx.lineWidth = 1 / k;
      ctx.strokeStyle = rgba(palette.dim, 0.35);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Community hulls.
    if (view.mode === "communities") {
      if (this.hullsStale) this.computeHulls(palette);
      ctx.lineJoin = "round";
      for (const hull of this.hulls) {
        if (hull.hull.length < 3) continue;
        ctx.beginPath();
        hull.hull.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fillStyle = rgba(hull.color, dark ? 0.09 : 0.08);
        ctx.strokeStyle = rgba(hull.color, dark ? 0.09 : 0.08);
        ctx.lineWidth = 34;
        ctx.fill();
        ctx.stroke();
      }
    }

    const focus = this.focusIds.size > 0;
    const focusId = view.hoveredId ?? view.selectedId;
    const pathOn = this.pathLinks.size > 0;
    const period = view.period;

    // Flow links, batched into (alpha, width) buckets — one stroke() per bucket.
    ctx.globalCompositeOperation = dark ? "lighter" : "source-over";
    const buckets = new Map<string, { path: Path2D; alpha: number; width: number; color: string }>();
    const logMax = Math.log1p(period === null ? net.maxWeight : this.maxWindow);
    const highlighted: NxLink[] = [];
    for (const link of net.links) {
      if (!link.flow) continue;
      const weight = period === null ? link.weight : windowWeight(link, period);
      if (weight <= 0) continue;
      const incident = focusId !== null && (link.source.id === focusId || link.target.id === focusId);
      if (incident || this.pathLinks.has(link)) {
        highlighted.push(link);
        continue;
      }
      const w = Math.log1p(weight) / logMax;
      let alpha = (dark ? 0.06 : 0.09) + (dark ? 0.26 : 0.3) * w;
      if (focus || pathOn) alpha *= 0.22;
      if (this.filteredOut(link.source) && this.filteredOut(link.target)) alpha *= 0.25;
      let color: string = palette.link;
      if (view.mode === "communities" && link.source.groupIndex === link.target.groupIndex && link.source.groupIndex >= 0) {
        const g = net.groups[link.source.groupIndex];
        if (g.colorIndex >= 0) color = palette.communities[g.colorIndex];
      }
      const ab = Math.min(7, Math.round(alpha * 20));
      const wb = Math.min(4, Math.floor(w * 5));
      const key = `${color}|${ab}|${wb}`;
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = { path: new Path2D(), alpha: ab / 20, width: (0.45 + wb * 0.45) / k, color };
        buckets.set(key, bucket);
      }
      const [cx, cy] = this.linkControl(link);
      bucket.path.moveTo(link.source.x ?? 0, link.source.y ?? 0);
      bucket.path.quadraticCurveTo(cx, cy, link.target.x ?? 0, link.target.y ?? 0);
    }
    ctx.lineCap = "round";
    for (const bucket of buckets.values()) {
      if (bucket.alpha <= 0) continue;
      ctx.strokeStyle = rgba(bucket.color, bucket.alpha);
      ctx.lineWidth = bucket.width;
      ctx.stroke(bucket.path);
    }

    // Relations (e.g. "general partner of"): dashed, in the entity hue.
    ctx.globalCompositeOperation = "source-over";
    ctx.setLineDash([4 / k, 3 / k]);
    for (const link of net.links) {
      if (link.flow) continue;
      const on = this.pathLinks.has(link) || (focusId !== null && (link.source.id === focusId || link.target.id === focusId));
      const [cx, cy] = this.linkControl(link);
      ctx.beginPath();
      ctx.moveTo(link.source.x ?? 0, link.source.y ?? 0);
      ctx.quadraticCurveTo(cx, cy, link.target.x ?? 0, link.target.y ?? 0);
      ctx.strokeStyle = rgba(this.pathLinks.has(link) ? palette.path : this.entityColor(palette), on ? 0.95 : focus || pathOn ? 0.18 : 0.62);
      ctx.lineWidth = (on ? 2 : 1.2) / k;
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Highlighted flow links (the focus node's ties, the traced path).
    ctx.globalCompositeOperation = dark ? "lighter" : "source-over";
    for (const link of highlighted) {
      const weight = period === null ? link.weight : windowWeight(link, period);
      const w = Math.log1p(weight) / logMax;
      const onPath = this.pathLinks.has(link);
      const [cx, cy] = this.linkControl(link);
      ctx.beginPath();
      ctx.moveTo(link.source.x ?? 0, link.source.y ?? 0);
      ctx.quadraticCurveTo(cx, cy, link.target.x ?? 0, link.target.y ?? 0);
      ctx.strokeStyle = rgba(onPath ? palette.path : palette.ink, onPath ? 0.95 : 0.35 + 0.5 * w);
      ctx.lineWidth = (onPath ? 2.6 : 0.9 + 2 * w) / k;
      ctx.stroke();
    }

    // Particles: messages flowing sender -> recipient (replay, or the focus node's ties).
    if (!view.reducedMotion) this.drawParticles(now, palette, highlighted);
    ctx.globalCompositeOperation = "source-over";

    // Nodes: context, then rows (low tiers first), then entities on top.
    const time = now / 1000;
    const order = [...net.nodes].sort((a, b) => this.drawRank(a) - this.drawRank(b));
    for (const node of order) this.drawNode(node, palette, time, focus || pathOn);

    this.drawOverlays(now, palette);
    if (!this.intro) this.drawLabels(palette, focus);

    // The replay month, large and quiet.
    if (period !== null) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = "700 44px system-ui, -apple-system, 'Segoe UI', sans-serif";
      ctx.fillStyle = rgba(palette.ink, dark ? 0.16 : 0.12);
      ctx.textBaseline = "alphabetic";
      ctx.fillText(this.periodCaption(period), 20, this.height - 22);
    }
  }

  private periodCaption(period: number): string {
    return periodLabel(this.net?.periods[period] ?? "");
  }

  private drawRank(node: NxNode): number {
    if (node.kind === "context") return 0;
    if (node.kind === "entity") return 10;
    return 1 + (node.row?.probability ?? 0) * 5;
  }

  private drawParticles(now: number, palette: (typeof NETWORK_PALETTE)[SurfaceMode], highlighted: NxLink[]): void {
    const view = this.view!;
    const net = this.net!;
    const ctx = this.ctx;
    const k = this.transform.k;
    let links: NxLink[] = [];
    if (view.playing && view.period !== null) {
      const p = view.period;
      links = net.links.filter((l) => l.flow && l.series && l.series[p] > 0).sort((a, b) => b.series![p] - a.series![p]).slice(0, 380);
    } else if (highlighted.length) {
      links = highlighted.filter((l) => l.flow);
    }
    if (!links.length) return;
    const time = now / 1000;
    for (const link of links) {
      const volume = view.playing && view.period !== null ? link.series![view.period] : link.weight;
      const count = Math.min(4, 1 + Math.floor(Math.log2(1 + volume) / 2));
      const phase = hash01(link.source.id + link.target.id);
      const speed = 0.28 + 0.2 * phase;
      const color = link.source.kind === "row" && (link.source.row?.tier ?? 0) > 0 && view.mode !== "communities" ? tierColor(view.surface, link.source.row!.tier) : palette.ink;
      const sprite = this.sprite(color);
      const dot = this.dot(color);
      const sx = link.source.x ?? 0;
      const sy = link.source.y ?? 0;
      const tx = link.target.x ?? 0;
      const ty = link.target.y ?? 0;
      const [cx, cy] = this.linkControl(link);
      for (let j = 0; j < count; j++) {
        const u = (time * speed + j / count + phase) % 1;
        const a = (1 - u) * (1 - u);
        const b = 2 * (1 - u) * u;
        const c = u * u;
        const x = a * sx + b * cx + c * tx;
        const y = a * sy + b * cy + c * ty;
        const size = 7 / k;
        const core = 1.6 / k;
        ctx.globalAlpha = 0.9 * Math.sin(Math.PI * u);
        // Sprites only: a path fill() under "lighter" falls off Chrome's fast path
        // (measured ~1.4 s per call in its software rasteriser).
        ctx.drawImage(sprite, x - size, y - size, 2 * size, 2 * size);
        ctx.drawImage(dot, x - core, y - core, 2 * core, 2 * core);
      }
    }
    ctx.globalAlpha = 1;
  }

  private drawNode(node: NxNode, palette: (typeof NETWORK_PALETTE)[SurfaceMode], time: number, dimOthers: boolean): void {
    const ctx = this.ctx;
    const view = this.view!;
    const k = this.transform.k;
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const color = this.nodeColor(node, palette);
    const dark = view.surface === "dark";
    let alpha = 1;
    if (this.filteredOut(node)) alpha = 0.14;
    if (dimOthers && !this.focusIds.has(node.id) && !view.path.includes(node.id)) alpha *= 0.3;
    if (view.period !== null && node.kind !== "entity" && !this.activeIn(node, view.period)) alpha *= 0.4;
    ctx.globalAlpha = alpha;

    if (node.kind === "context") {
      ctx.beginPath();
      ctx.arc(x, y, node.r, 0, 2 * Math.PI);
      ctx.fillStyle = rgba(color, 0.55);
      ctx.fill();
      ctx.globalAlpha = 1;
      return;
    }

    if (node.kind === "entity") {
      if (dark && alpha > 0.5) {
        const g = node.r * 3.2;
        ctx.globalCompositeOperation = "lighter";
        ctx.drawImage(this.sprite(color), x - g, y - g, 2 * g, 2 * g);
        ctx.globalCompositeOperation = "source-over";
      }
      ctx.beginPath();
      for (let i = 0; i < 6; i++) {
        const a = Math.PI / 6 + (i * Math.PI) / 3;
        const px = x + Math.cos(a) * node.r;
        const py = y + Math.sin(a) * node.r;
        if (i) ctx.lineTo(px, py);
        else ctx.moveTo(px, py);
      }
      ctx.closePath();
      ctx.fillStyle = palette.stage;
      ctx.fill();
      ctx.fillStyle = rgba(color, 0.3);
      ctx.fill();
      ctx.lineWidth = 2 / k;
      ctx.strokeStyle = color;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, 1.8, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.globalAlpha = 1;
      return;
    }

    const tier = node.row?.tier ?? 0;
    const p = node.row?.probability ?? 0;
    const dimmed = alpha < 0.5;
    if (view.mode !== "communities" && tier > 0 && !dimmed) {
      const breathe = tier >= view.flagTier && !view.reducedMotion ? 1 + 0.1 * Math.sin(time * 2.2 + hash01(node.id) * 6) : 1;
      const g = node.r * (1.9 + 2.6 * p) * breathe;
      ctx.globalCompositeOperation = dark ? "lighter" : "source-over";
      ctx.globalAlpha = alpha * (dark ? 1 : 0.55);
      ctx.drawImage(this.sprite(color), x - g, y - g, 2 * g, 2 * g);
      ctx.globalCompositeOperation = "source-over";
      ctx.globalAlpha = alpha;
    }
    ctx.beginPath();
    ctx.arc(x, y, node.r, 0, 2 * Math.PI);
    ctx.fillStyle = tier === 0 && view.mode !== "communities" ? rgba(color, 0.8) : color;
    ctx.fill();
    ctx.lineWidth = 1.2 / k;
    ctx.strokeStyle = palette.stage;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  private activeIn(node: NxNode, period: number): boolean {
    for (const { link } of this.net!.adjacency.get(node.id) ?? []) if (link.flow && windowWeight(link, period) > 0) return true;
    return false;
  }

  private drawOverlays(now: number, palette: (typeof NETWORK_PALETTE)[SurfaceMode]): void {
    const ctx = this.ctx;
    const view = this.view!;
    const net = this.net!;
    const k = this.transform.k;

    // Ground truth: rings pop in, highest score first — solid when flagged, dashed when missed.
    if (view.revealAt !== null) {
      for (const [id, rank] of this.revealOrder) {
        const node = net.byId.get(id);
        if (!node) continue;
        const elapsed = view.reducedMotion ? 1e9 : now - view.revealAt - rank * REVEAL_STEP_MS;
        if (elapsed < 0) continue;
        const pop = Math.min(1, elapsed / 320);
        const scale = 1 + 0.9 * (1 - easeInOut(pop));
        const flagged = (node.row?.tier ?? 0) >= view.flagTier;
        ctx.globalAlpha = pop;
        ctx.beginPath();
        ctx.arc(node.x ?? 0, node.y ?? 0, (node.r + 4.5 / k) * scale, 0, 2 * Math.PI);
        ctx.setLineDash(flagged ? [] : [3 / k, 2.5 / k]);
        ctx.lineWidth = 1.8 / k;
        ctx.strokeStyle = palette.ink;
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.globalAlpha = 1;
    }

    // The traced path: numbered hops.
    view.path.forEach((id, i) => {
      const node = net.byId.get(id);
      if (!node) return;
      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, node.r + 3.5 / k, 0, 2 * Math.PI);
      ctx.lineWidth = 2 / k;
      ctx.strokeStyle = palette.path;
      ctx.stroke();
      const bx = (node.x ?? 0) - node.r - 7 / k;
      const by = (node.y ?? 0) - node.r - 7 / k;
      ctx.beginPath();
      ctx.arc(bx, by, 6.5 / k, 0, 2 * Math.PI);
      ctx.fillStyle = palette.path;
      ctx.fill();
      ctx.font = `700 ${8.5 / k}px system-ui, sans-serif`;
      ctx.fillStyle = palette.stage;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(i + 1), bx, by + 0.3 / k);
      ctx.textAlign = "start";
    });

    // Hover and selection rings (the selection pulses).
    const ring = (id: string | null, width: number, extra: number, color: string) => {
      const node = id ? net.byId.get(id) : null;
      if (!node) return;
      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, node.r + extra / k, 0, 2 * Math.PI);
      ctx.lineWidth = width / k;
      ctx.strokeStyle = color;
      ctx.stroke();
    };
    ring(view.hoveredId, 1.4, 3, palette.ink);
    if (view.selectedId) {
      ring(view.selectedId, 2.2, 4, palette.path);
      if (!view.reducedMotion) {
        const phase = ((now / 1000) % 1.6) / 1.6;
        ctx.globalAlpha = 1 - phase;
        ring(view.selectedId, 1.5, 4 + 14 * phase, palette.path);
        ctx.globalAlpha = 1;
      }
    }
  }

  private measure(text: string, font: string): number {
    const key = `${font}|${text}`;
    let w = this.textWidths.get(key);
    if (w === undefined) {
      this.ctx.font = font;
      w = this.ctx.measureText(text).width;
      this.textWidths.set(key, w);
    }
    return w;
  }

  private drawLabels(palette: (typeof NETWORK_PALETTE)[SurfaceMode], focus: boolean): void {
    const ctx = this.ctx;
    const view = this.view!;
    const net = this.net!;
    const t = this.transform;
    const dpr = this.dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const placed: LabelBox[] = [];
    const candidates: { node: NxNode; force: boolean; strong: boolean }[] = [];
    const seen = new Set<string>();
    const add = (node: NxNode | undefined, force: boolean, strong: boolean) => {
      if (!node || seen.has(node.id)) return;
      seen.add(node.id);
      candidates.push({ node, force, strong });
    };
    add(net.byId.get(view.hoveredId ?? ""), true, true);
    add(net.byId.get(view.selectedId ?? ""), true, true);
    for (const id of view.path) add(net.byId.get(id), true, true);
    if (focus) for (const id of this.focusIds) if (net.byId.get(id)?.kind !== "context") add(net.byId.get(id), false, false);
    for (const node of net.nodes) if (node.kind === "entity") add(node, false, false);
    for (const id of view.pinned) add(net.byId.get(id), false, false);
    if (t.k > 1.9) for (const node of net.rows) add(node, false, false);

    // Community names at the top of each hull.
    if (view.mode === "communities") {
      for (const hull of this.hulls) {
        if (hull.hull.length < 3) continue;
        const top = hull.hull.reduce((a, b) => (b[1] < a[1] ? b : a));
        const sx = t.applyX(top[0]);
        const sy = t.applyY(top[1]) - 22;
        const text = `${hull.name} · ${hull.size}`;
        const font = "700 11px system-ui, -apple-system, 'Segoe UI', sans-serif";
        const w = this.measure(text, font);
        ctx.font = font;
        ctx.lineWidth = 3;
        ctx.strokeStyle = palette.halo;
        ctx.strokeText(text, sx - w / 2, sy);
        ctx.fillStyle = palette.ink;
        ctx.fillText(text, sx - w / 2, sy);
        placed.push({ x: sx - w / 2, y: sy - 11, w, h: 14 });
      }
    }

    ctx.textBaseline = "middle";
    for (const { node, force, strong } of candidates) {
      if (!force && (this.filteredOut(node) || (focus && !this.focusIds.has(node.id)))) continue;
      const sx = t.applyX(node.x ?? 0);
      const sy = t.applyY(node.y ?? 0);
      if (sx < -80 || sy < -20 || sx > this.width + 20 || sy > this.height + 20) continue;
      const font = `${strong || node.kind === "entity" ? 700 : 600} ${strong ? 12.5 : 11}px system-ui, -apple-system, 'Segoe UI', sans-serif`;
      const text = node.label;
      const w = this.measure(text, font);
      const x = sx + node.r * t.k + 5;
      const box = { x: x - 2, y: sy - 8, w: w + 4, h: 16 };
      if (!force && placed.some((b) => box.x < b.x + b.w && box.x + box.w > b.x && box.y < b.y + b.h && box.y + box.h > b.y)) continue;
      placed.push(box);
      ctx.font = font;
      ctx.lineWidth = 3.5;
      ctx.lineJoin = "round";
      ctx.strokeStyle = palette.halo;
      ctx.strokeText(text, x, sy);
      ctx.fillStyle = node.kind === "entity" ? this.entityColor(palette) : palette.ink;
      ctx.fillText(text, x, sy);
    }
    ctx.textBaseline = "alphabetic";

    // Orbit caption.
    if (net.nodes.some((n) => n.orbit)) {
      const sx = t.applyX(0);
      const sy = t.applyY(-this.orbitRadius) - 12;
      if (sy > 8 && sy < this.height) {
        const text = "No two-way e-mail tie in the corpus";
        const font = "600 10.5px system-ui, -apple-system, 'Segoe UI', sans-serif";
        const w = this.measure(text, font);
        ctx.font = font;
        ctx.fillStyle = palette.dim;
        ctx.fillText(text, sx - w / 2, sy);
      }
    }
  }

  private computeHulls(palette: (typeof NETWORK_PALETTE)[SurfaceMode]): void {
    const net = this.net!;
    this.hulls = net.groups
      .map((group, i) => {
        const points = net.nodes.filter((n) => n.groupIndex === i && !n.orbit).map((n): [number, number] => [n.x ?? 0, n.y ?? 0]);
        return {
          color: group.colorIndex >= 0 ? palette.communities[group.colorIndex] : palette.other,
          name: group.name,
          size: group.size,
          hull: points.length >= 3 ? convexHull(points) : [],
        };
      })
      .filter((h) => h.hull.length >= 3);
    this.hullsStale = false;
  }
}
