/**
 * The Dispatch Tower's two canvases (see DispatchTowerView.tsx), imperative like
 * networkScene.ts — the React view owns the data and pushes it in:
 *
 * - `HubMapScene`: the hubs on a map (us-atlas / world-atlas geometry, d3-geo) with the
 *   scenario's real shipments in flight around them. The dataset only knows a
 *   destination as an offset from its hub, so shipments fly to `hub + offset` drawn
 *   at one fixed scale — a schematic, never a geographic claim (the view says so).
 * - `LandscapeScene`: the predicted duration over a grid of destinations around one
 *   hub, drawn as filled isochrones (d3-contour) with the real shipments on top; hover
 *   reads the grid, click pins a destination.
 *
 * Both render on demand; the map's flight animation runs only while visible and never
 * under reduced motion.
 */
import { contours } from "d3-contour";
import { geoAlbersUsa, geoEqualEarth, geoPath, type GeoProjection, type GeoPermissibleObjects } from "d3-geo";
import type { FeatureCollection, MultiLineString, MultiPolygon } from "geojson";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import { glyphPath, type Glyph, rgba, sequentialColor, sizeCanvas } from "./logisticsViz";
import type { SurfaceMode } from "./riskPalette";

export type SceneColors = {
  land: string;
  border: string;
  outline: string;
  ink: string;
  dim: string;
  accent: string;
  surface: string;
};

export type Offset = { dx: number; dy: number };

export type HubPoint = { key: string; label: string; lat: number; lon: number; count: number; medianDays: number };
export type Flight = { hub: string; dx: number; dy: number; days: number };

export type MapView = {
  mode: SurfaceMode;
  colors: SceneColors;
  selected: string;
  pin: Offset | null;
  pinDays: number | null;
  pinGlyph: Glyph;
  daysDomain: [number, number];
  offsetMax: number;
  reducedMotion: boolean;
};

type Geometry = { land: GeoPermissibleObjects; borders: GeoPermissibleObjects | null; scope: "usa" | "world" };

const HUB_HIT_PX = 22;
const SECONDS_PER_DAY = 0.42;
const FLIGHTS_PER_HUB = 70;

function hash01(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10007) / 10007;
}

/** Map geometry from a us-atlas `states-10m` or world-atlas `land-110m` topology. */
export function mapGeometry(topology: Topology, scope: "usa" | "world"): Geometry {
  if (scope === "usa") {
    const objects = topology.objects as Record<string, GeometryCollection>;
    return {
      land: feature(topology, objects.nation) as FeatureCollection,
      borders: mesh(topology, objects.states, (a, b) => a !== b) as MultiLineString,
      scope,
    };
  }
  const objects = topology.objects as Record<string, GeometryCollection>;
  return { land: feature(topology, objects.land) as FeatureCollection, borders: null, scope };
}

function haloText(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, ink: string, halo: string): void {
  ctx.lineWidth = 3.5;
  ctx.lineJoin = "round";
  ctx.strokeStyle = halo;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = ink;
  ctx.fillText(text, x, y);
}

/** Draw a 24×24 glyph centred at (x, y), `size` px wide, rotated by `angle` radians. */
export function drawGlyph(ctx: CanvasRenderingContext2D, glyph: Glyph, x: number, y: number, size: number, angle: number, color: string): void {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.scale(size / 24, size / 24);
  ctx.translate(-12, -12);
  ctx.fillStyle = color;
  ctx.fill(glyphPath(glyph), "evenodd");
  ctx.restore();
}

export class HubMapScene {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private base = document.createElement("canvas");
  private geometry: Geometry | null = null;
  private hubs: HubPoint[] = [];
  private flights: Flight[] = [];
  private view: MapView | null = null;
  private projection: GeoProjection | null = null;
  private width = 0;
  private height = 0;
  private dpr = 1;
  private raf = 0;
  private visible = true;
  private hubPx = new Map<string, [number, number]>();
  private onHub: (key: string) => void;
  private hoverHub: string | null = null;
  private observer: IntersectionObserver;

  constructor(canvas: HTMLCanvasElement, onHub: (key: string) => void) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.onHub = onHub;
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

  setData(geometry: Geometry, hubs: HubPoint[], flights: Flight[]): void {
    this.geometry = geometry;
    this.hubs = hubs;
    // A stable, evenly spread subset per hub keeps the animation light and legible.
    const byHub = new Map<string, Flight[]>();
    for (const f of flights) byHub.set(f.hub, [...(byHub.get(f.hub) ?? []), f]);
    this.flights = [...byHub.values()].flatMap((list) => {
      const step = Math.max(1, Math.floor(list.length / FLIGHTS_PER_HUB));
      return list.filter((_, i) => i % step === 0).slice(0, FLIGHTS_PER_HUB);
    });
    this.layout();
  }

  setView(view: MapView): void {
    const themeChanged = this.view?.mode !== view.mode || this.view?.colors.land !== view.colors.land;
    this.view = view;
    if (themeChanged) this.renderBase();
    this.kick();
  }

  resize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.dpr = sizeCanvas(this.canvas, width, height);
    this.layout();
  }

  private layout(): void {
    if (!this.geometry || this.width < 2) return;
    const pad = 18;
    const projection = (this.geometry.scope === "usa" ? geoAlbersUsa() : geoEqualEarth()) as GeoProjection;
    projection.fitExtent(
      [
        [pad, pad + 6],
        [this.width - pad, this.height - pad - 10],
      ],
      this.geometry.land,
    );
    this.projection = projection;
    this.hubPx.clear();
    for (const hub of this.hubs) {
      const p = projection([hub.lon, hub.lat]);
      if (p) this.hubPx.set(hub.key, [p[0], p[1]]);
    }
    this.renderBase();
    this.kick();
  }

  private renderBase(): void {
    if (!this.geometry || !this.projection || !this.view || this.width < 2) return;
    const { colors } = this.view;
    this.base.width = this.canvas.width;
    this.base.height = this.canvas.height;
    const ctx = this.base.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    const path = geoPath(this.projection, ctx);
    ctx.beginPath();
    path(this.geometry.land);
    ctx.fillStyle = colors.land;
    ctx.fill();
    if (this.geometry.borders) {
      ctx.beginPath();
      path(this.geometry.borders);
      ctx.strokeStyle = rgba(colors.border, 0.85);
      ctx.lineWidth = 0.7;
      ctx.stroke();
    }
    ctx.beginPath();
    path(this.geometry.land);
    ctx.strokeStyle = colors.outline;
    ctx.lineWidth = 1.1;
    ctx.stroke();
  }

  private kick = (): void => {
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  };

  private loop = (now: number): void => {
    this.raf = 0;
    this.draw(now);
    const animate = this.view && !this.view.reducedMotion && this.visible && !document.hidden;
    if (animate) this.raf = requestAnimationFrame(this.loop);
  };

  /** A point along the gentle arc from `a` to `b` at progress t. */
  private arcPoint(a: [number, number], b: [number, number], t: number): [number, number] {
    const mx = (a[0] + b[0]) / 2;
    const my = (a[1] + b[1]) / 2;
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const cx = mx - dy * 0.18;
    const cy = my + dx * 0.18;
    const u = 1 - t;
    return [u * u * a[0] + 2 * u * t * cx + t * t * b[0], u * u * a[1] + 2 * u * t * cy + t * t * b[1]];
  }

  private destination(hub: [number, number], offset: Offset): [number, number] {
    const view = this.view!;
    const k = (Math.min(this.width, this.height) * 0.32) / Math.max(1, view.offsetMax);
    return [hub[0] + offset.dx * k, hub[1] - offset.dy * k];
  }

  private draw(now: number): void {
    const view = this.view;
    const ctx = this.ctx;
    if (!view || this.width < 2) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.drawImage(this.base, 0, 0);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const { colors } = view;
    const time = now / 1000;
    const [lo, hi] = view.daysDomain;
    const dayT = (d: number) => (d - lo) / Math.max(1e-6, hi - lo);

    // Flights: every hub's real shipments, the selected hub's in full colour.
    ctx.lineCap = "round";
    for (const f of this.flights) {
      const origin = this.hubPx.get(f.hub);
      if (!origin) continue;
      const selected = f.hub === view.selected;
      const dest = this.destination(origin, f);
      const color = sequentialColor(dayT(f.days), view.mode);
      if (view.reducedMotion) {
        ctx.fillStyle = selected ? color : rgba(colors.dim, 0.25);
        ctx.beginPath();
        ctx.arc(dest[0], dest[1], selected ? 2.2 : 1.4, 0, Math.PI * 2);
        ctx.fill();
        continue;
      }
      const duration = Math.max(0.6, f.days * SECONDS_PER_DAY);
      const t = (time / duration + hash01(`${f.hub}${f.dx}${f.dy}`)) % 1;
      const alpha = selected ? 0.95 : 0.22;
      if (selected) {
        ctx.fillStyle = rgba(colors.ink, 0.16);
        ctx.beginPath();
        ctx.arc(dest[0], dest[1], 1.3, 0, Math.PI * 2);
        ctx.fill();
      }
      const tail = Math.max(0, t - 0.1);
      ctx.beginPath();
      const p0 = this.arcPoint(origin, dest, tail);
      ctx.moveTo(p0[0], p0[1]);
      for (let s = 1; s <= 4; s++) {
        const p = this.arcPoint(origin, dest, tail + ((t - tail) * s) / 4);
        ctx.lineTo(p[0], p[1]);
      }
      ctx.strokeStyle = selected ? color : rgba(colors.dim, alpha);
      ctx.globalAlpha = selected ? 0.55 : 1;
      ctx.lineWidth = selected ? 1.6 : 1;
      ctx.stroke();
      ctx.globalAlpha = 1;
      const head = this.arcPoint(origin, dest, t);
      ctx.fillStyle = selected ? color : rgba(colors.dim, alpha + 0.1);
      ctx.beginPath();
      ctx.arc(head[0], head[1], selected ? 2.3 : 1.5, 0, Math.PI * 2);
      ctx.fill();
    }

    // The pinned route from the selected hub.
    const selectedPx = this.hubPx.get(view.selected);
    if (selectedPx && view.pin) {
      const dest = this.destination(selectedPx, view.pin);
      ctx.setLineDash([5, 5]);
      ctx.lineDashOffset = view.reducedMotion ? 0 : -time * 18;
      ctx.beginPath();
      for (let s = 0; s <= 24; s++) {
        const p = this.arcPoint(selectedPx, dest, s / 24);
        if (s === 0) ctx.moveTo(p[0], p[1]);
        else ctx.lineTo(p[0], p[1]);
      }
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(dest[0], dest[1], 6, 0, Math.PI * 2);
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 2;
      ctx.stroke();
      const duration = Math.max(1, (view.pinDays ?? 10) * SECONDS_PER_DAY);
      const t = view.reducedMotion ? 0.55 : (time / duration) % 1;
      const p = this.arcPoint(selectedPx, dest, t);
      const q = this.arcPoint(selectedPx, dest, Math.min(1, t + 0.01));
      const angle = view.pinGlyph === "plane" ? Math.atan2(q[1] - p[1], q[0] - p[0]) + Math.PI / 2 : 0;
      ctx.beginPath();
      ctx.arc(p[0], p[1], 11, 0, Math.PI * 2);
      ctx.fillStyle = rgba(colors.surface, 0.9);
      ctx.fill();
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      drawGlyph(ctx, view.pinGlyph, p[0], p[1], 14, angle, colors.accent);
    }

    // Hubs on top.
    ctx.font = "600 11.5px Inter, system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (const hub of this.hubs) {
      const p = this.hubPx.get(hub.key);
      if (!p) continue;
      const selected = hub.key === view.selected;
      const hovered = hub.key === this.hoverHub;
      const r = 4.5 + Math.sqrt(hub.count) / 7;
      const glow = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], r * 3.2);
      glow.addColorStop(0, rgba(colors.accent, selected ? 0.55 : 0.28));
      glow.addColorStop(1, rgba(colors.accent, 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(p[0], p[1], r * 3.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
      ctx.fillStyle = selected ? colors.accent : colors.surface;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = colors.accent;
      ctx.stroke();
      if (selected && !view.reducedMotion) {
        const pulse = (time % 1.8) / 1.8;
        ctx.beginPath();
        ctx.arc(p[0], p[1], r + 4 + pulse * 16, 0, Math.PI * 2);
        ctx.strokeStyle = rgba(colors.accent, 0.6 * (1 - pulse));
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      const labelRight = p[0] < this.width - 90;
      ctx.textAlign = labelRight ? "left" : "right";
      haloText(ctx, hub.label, p[0] + (labelRight ? r + 6 : -r - 6), p[1] - 1, selected || hovered ? colors.ink : colors.dim, rgba(colors.surface, 0.85));
    }
  }

  private hubAt(x: number, y: number): string | null {
    let best: string | null = null;
    let bestDistance = HUB_HIT_PX;
    for (const [key, p] of this.hubPx) {
      const d = Math.hypot(p[0] - x, p[1] - y);
      if (d < bestDistance) {
        bestDistance = d;
        best = key;
      }
    }
    return best;
  }

  private onMove = (event: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const hub = this.hubAt(event.clientX - rect.left, event.clientY - rect.top);
    if (hub !== this.hoverHub) {
      this.hoverHub = hub;
      this.canvas.style.cursor = hub ? "pointer" : "default";
      this.kick();
    }
  };

  private onLeave = (): void => {
    this.hoverHub = null;
    this.kick();
  };

  private onClick = (event: MouseEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const hub = this.hubAt(event.clientX - rect.left, event.clientY - rect.top);
    if (hub) this.onHub(hub);
  };
}

// ── ETA landscape ─────────────────────────────────────────────────────────────

export type Landscape = {
  n: number;
  xs: number[]; // left → right
  ys: number[]; // top → bottom (descending: north first)
  values: number[];
  lower: number[] | null;
  upper: number[] | null;
};

export type LandscapeView = {
  mode: SurfaceMode;
  colors: SceneColors;
  daysDomain: [number, number];
  dots: (Offset & { days: number })[];
  pin: Offset | null;
  hubLabel: string;
  units: string;
  reducedMotion: boolean;
};

export type LandscapeReading = Offset & { days: number; lower: number | null; upper: number | null; x: number; y: number };

const PLOT = { left: 46, right: 18, top: 24, bottom: 34 };

export class LandscapeScene {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private layer = document.createElement("canvas");
  private previous = document.createElement("canvas");
  private landscape: Landscape | null = null;
  private view: LandscapeView | null = null;
  private width = 0;
  private height = 0;
  private dpr = 1;
  private transitionStart = 0;
  private raf = 0;
  private hover: LandscapeReading | null = null;
  private onHover: (reading: LandscapeReading | null) => void;
  private onPick: (offset: Offset) => void;

  constructor(canvas: HTMLCanvasElement, onHover: (reading: LandscapeReading | null) => void, onPick: (offset: Offset) => void) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.onHover = onHover;
    this.onPick = onPick;
    canvas.addEventListener("pointermove", this.onMove);
    canvas.addEventListener("pointerleave", this.onLeave);
    canvas.addEventListener("click", this.onClick);
  }

  destroy(): void {
    cancelAnimationFrame(this.raf);
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
    this.canvas.removeEventListener("click", this.onClick);
  }

  resize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.dpr = sizeCanvas(this.canvas, width, height);
    this.renderLayer();
    this.draw(performance.now());
  }

  setLandscape(landscape: Landscape): void {
    // Keep what is on screen to cross-fade from, then sweep the new one in.
    this.previous.width = this.layer.width;
    this.previous.height = this.layer.height;
    this.previous.getContext("2d")!.drawImage(this.layer, 0, 0);
    const hadOne = this.landscape !== null;
    this.landscape = landscape;
    this.renderLayer();
    this.transitionStart = hadOne && !this.view?.reducedMotion ? performance.now() : 0;
    this.kick();
  }

  setView(view: LandscapeView): void {
    const restyle = !this.view || this.view.mode !== view.mode || this.view.colors.ink !== view.colors.ink || this.view.daysDomain !== view.daysDomain;
    this.view = view;
    if (restyle) this.renderLayer();
    this.kick();
  }

  private get plot() {
    return { x: PLOT.left, y: PLOT.top, w: Math.max(10, this.width - PLOT.left - PLOT.right), h: Math.max(10, this.height - PLOT.top - PLOT.bottom) };
  }

  private toPx(offset: Offset): [number, number] {
    const ls = this.landscape!;
    const p = this.plot;
    const [x0, x1] = [ls.xs[0], ls.xs[ls.n - 1]];
    const [y0, y1] = [ls.ys[0], ls.ys[ls.n - 1]];
    return [p.x + ((offset.dx - x0) / (x1 - x0)) * p.w, p.y + ((offset.dy - y0) / (y1 - y0)) * p.h];
  }

  private fromPx(x: number, y: number): Offset | null {
    const ls = this.landscape;
    if (!ls) return null;
    const p = this.plot;
    const u = (x - p.x) / p.w;
    const v = (y - p.y) / p.h;
    if (u < 0 || u > 1 || v < 0 || v > 1) return null;
    return { dx: ls.xs[0] + u * (ls.xs[ls.n - 1] - ls.xs[0]), dy: ls.ys[0] + v * (ls.ys[ls.n - 1] - ls.ys[0]) };
  }

  /** Bilinear read of a grid (row-major, north row first) at an offset. */
  private sample(grid: number[], offset: Offset): number {
    const ls = this.landscape!;
    const gx = ((offset.dx - ls.xs[0]) / (ls.xs[ls.n - 1] - ls.xs[0])) * (ls.n - 1);
    const gy = ((offset.dy - ls.ys[0]) / (ls.ys[ls.n - 1] - ls.ys[0])) * (ls.n - 1);
    const i = Math.min(ls.n - 2, Math.max(0, Math.floor(gx)));
    const j = Math.min(ls.n - 2, Math.max(0, Math.floor(gy)));
    const fx = Math.min(1, Math.max(0, gx - i));
    const fy = Math.min(1, Math.max(0, gy - j));
    const at = (a: number, b: number) => grid[b * ls.n + a];
    return (at(i, j) * (1 - fx) + at(i + 1, j) * fx) * (1 - fy) + (at(i, j + 1) * (1 - fx) + at(i + 1, j + 1) * fx) * fy;
  }

  read(offset: Offset): LandscapeReading | null {
    const ls = this.landscape;
    if (!ls) return null;
    const [x, y] = this.toPx(offset);
    return {
      ...offset,
      days: this.sample(ls.values, offset),
      lower: ls.lower ? this.sample(ls.lower, offset) : null,
      upper: ls.upper ? this.sample(ls.upper, offset) : null,
      x,
      y,
    };
  }

  /** The static layer: isochrone bands, isolines + labels, frame and axes. */
  private renderLayer(): void {
    const ls = this.landscape;
    const view = this.view;
    if (!ls || !view || this.width < 2) return;
    this.layer.width = this.canvas.width;
    this.layer.height = this.canvas.height;
    const ctx = this.layer.getContext("2d")!;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    const p = this.plot;
    const { colors } = view;
    const [lo, hi] = view.daysDomain;
    const min = Math.floor(Math.min(...ls.values));
    const max = Math.ceil(Math.max(...ls.values));
    const thresholds: number[] = [];
    for (let t = min; t <= max; t += 0.5) thresholds.push(t);
    const bands = contours().size([ls.n, ls.n]).thresholds(thresholds)(ls.values);

    ctx.save();
    ctx.beginPath();
    ctx.rect(p.x, p.y, p.w, p.h);
    ctx.clip();
    const sx = p.w / (ls.n - 1);
    const sy = p.h / (ls.n - 1);
    const trace = (geometry: MultiPolygon) => {
      ctx.beginPath();
      for (const polygon of geometry.coordinates) {
        for (const ring of polygon) {
          ring.forEach(([gx, gy], k) => {
            const x = p.x + (gx - 0.5) * sx;
            const y = p.y + (gy - 0.5) * sy;
            if (k === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          });
          ctx.closePath();
        }
      }
    };
    ctx.fillStyle = sequentialColor((min - lo) / (hi - lo), view.mode);
    ctx.fillRect(p.x, p.y, p.w, p.h);
    for (const band of bands) {
      trace(band);
      ctx.fillStyle = sequentialColor((band.value - lo) / (hi - lo), view.mode);
      ctx.fill("evenodd");
    }
    // Isolines every whole day, labelled every other day.
    const labelled: { text: string; x: number; y: number }[] = [];
    for (const band of bands) {
      if (!Number.isInteger(band.value) || band.value === min) continue;
      trace(band);
      const light = (band.value - lo) / (hi - lo) > 0.5 === (view.mode === "light");
      ctx.strokeStyle = light ? "rgba(255,255,255,0.55)" : "rgba(10,20,35,0.45)";
      ctx.lineWidth = band.value % 2 === 0 ? 1.3 : 0.6;
      ctx.stroke();
      if (band.value % 2 !== 0) continue;
      // Label at the ring point nearest the top-right third, clear of earlier labels.
      let best: [number, number] | null = null;
      let bestScore = -Infinity;
      for (const polygon of band.coordinates) {
        const ring = polygon[0];
        for (let k = 0; k < ring.length; k += 3) {
          const x = p.x + (ring[k][0] - 0.5) * sx;
          const y = p.y + (ring[k][1] - 0.5) * sy;
          if (x < p.x + 20 || x > p.x + p.w - 20 || y < p.y + 12 || y > p.y + p.h - 12) continue;
          if (labelled.some((l) => Math.hypot(l.x - x, l.y - y) < 46)) continue;
          const score = x * 0.6 - y;
          if (score > bestScore) {
            bestScore = score;
            best = [x, y];
          }
        }
      }
      if (best) labelled.push({ text: `${band.value} d`, x: best[0], y: best[1] });
    }
    ctx.font = "600 10.5px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const label of labelled) {
      const w = ctx.measureText(label.text).width + 8;
      ctx.fillStyle = rgba(colors.surface, 0.82);
      ctx.beginPath();
      ctx.roundRect(label.x - w / 2, label.y - 8, w, 16, 4);
      ctx.fill();
      ctx.fillStyle = colors.ink;
      ctx.fillText(label.text, label.x, label.y + 0.5);
    }
    ctx.restore();

    // Frame, zero lines through the hub, axis ticks and compass.
    ctx.strokeStyle = rgba(colors.border, 0.9);
    ctx.lineWidth = 1;
    ctx.strokeRect(p.x + 0.5, p.y + 0.5, p.w - 1, p.h - 1);
    const [hx, hy] = this.toPx({ dx: 0, dy: 0 });
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = rgba(colors.ink, 0.35);
    ctx.beginPath();
    ctx.moveTo(hx, p.y);
    ctx.lineTo(hx, p.y + p.h);
    ctx.moveTo(p.x, hy);
    ctx.lineTo(p.x + p.w, hy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = colors.dim;
    ctx.font = "10.5px Inter, system-ui, sans-serif";
    const ticks = (from: number, to: number) => {
      const step = Math.abs(to - from) > 600 ? 200 : 100;
      const out: number[] = [];
      for (let v = Math.ceil(Math.min(from, to) / step) * step; v <= Math.max(from, to); v += step) out.push(v);
      return out;
    };
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const v of ticks(ls.xs[0], ls.xs[ls.n - 1])) {
      const [x] = this.toPx({ dx: v, dy: 0 });
      ctx.fillText(String(v), x, p.y + p.h + 5);
    }
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const v of ticks(ls.ys[ls.n - 1], ls.ys[0])) {
      const [, y] = this.toPx({ dx: 0, dy: v });
      ctx.fillText(String(v), p.x - 6, y);
    }
    ctx.font = "700 11px Inter, system-ui, sans-serif";
    ctx.fillStyle = colors.ink;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText("N", hx, p.y - 6);
    ctx.textBaseline = "top";
    ctx.fillText(`S · east-west offset (${view.units})`, hx, p.y + p.h + 18);
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText("E", p.x + p.w + 5, hy);
    ctx.textAlign = "right";
    ctx.fillText("W", p.x - 30, hy);
  }

  private kick = (): void => {
    if (!this.raf) this.raf = requestAnimationFrame(this.loopFrame);
  };

  private loopFrame = (now: number): void => {
    this.raf = 0;
    const running = this.draw(now);
    if (running) this.raf = requestAnimationFrame(this.loopFrame);
  };

  /** Returns whether an animation is still running. */
  private draw(now: number): boolean {
    const view = this.view;
    const ls = this.landscape;
    const ctx = this.ctx;
    if (!view || !ls || this.width < 2) return false;
    const { colors } = view;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    const elapsed = this.transitionStart ? now - this.transitionStart : Infinity;
    const fade = Math.min(1, elapsed / 520);
    if (fade < 1) {
      ctx.globalAlpha = 1 - fade;
      ctx.drawImage(this.previous, 0, 0);
      ctx.globalAlpha = fade;
    }
    ctx.drawImage(this.layer, 0, 0);
    ctx.globalAlpha = 1;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const p = this.plot;
    const [hx, hy] = this.toPx({ dx: 0, dy: 0 });

    // Radar sweep over the new landscape.
    const sweepMs = 950;
    if (elapsed < sweepMs && typeof ctx.createConicGradient === "function") {
      const angle = -Math.PI / 2 + (elapsed / sweepMs) * Math.PI * 2;
      const gradient = ctx.createConicGradient(angle - 0.9, hx, hy);
      gradient.addColorStop(0, rgba(colors.accent, 0));
      gradient.addColorStop(0.14, rgba(colors.accent, 0.3));
      gradient.addColorStop(0.145, rgba(colors.accent, 0));
      ctx.save();
      ctx.beginPath();
      ctx.rect(p.x, p.y, p.w, p.h);
      ctx.clip();
      ctx.fillStyle = gradient;
      ctx.fillRect(p.x, p.y, p.w, p.h);
      ctx.restore();
    }

    // Real shipments from this hub.
    const ring = view.mode === "dark" ? "rgba(5,10,20,0.75)" : "rgba(255,255,255,0.85)";
    for (const dot of view.dots) {
      const [x, y] = this.toPx(dot);
      if (x < p.x || x > p.x + p.w || y < p.y || y > p.y + p.h) continue;
      ctx.beginPath();
      ctx.arc(x, y, 1.7, 0, Math.PI * 2);
      ctx.fillStyle = view.mode === "dark" ? "rgba(240,246,255,0.55)" : "rgba(12,24,40,0.5)";
      ctx.fill();
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = ring;
      ctx.stroke();
    }

    // The hub itself.
    ctx.beginPath();
    ctx.arc(hx, hy, 7, 0, Math.PI * 2);
    ctx.fillStyle = colors.surface;
    ctx.fill();
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = colors.accent;
    ctx.stroke();
    ctx.font = "700 11.5px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    haloText(ctx, view.hubLabel, hx + 11, hy - 11, colors.ink, rgba(colors.surface, 0.9));

    // Pinned destination.
    if (view.pin) {
      const [x, y] = this.toPx(view.pin);
      const pulse = view.reducedMotion ? 0 : (now % 1600) / 1600;
      ctx.beginPath();
      ctx.arc(x, y, 9 + pulse * 10, 0, Math.PI * 2);
      ctx.strokeStyle = rgba(colors.accent, 0.7 * (1 - pulse));
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, 7, 0, Math.PI * 2);
      ctx.fillStyle = colors.accent;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = colors.surface;
      ctx.stroke();
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(hx, hy);
      ctx.lineTo(x, y);
      ctx.strokeStyle = rgba(colors.accent, 0.8);
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Hover crosshair.
    if (this.hover) {
      const { x, y } = this.hover;
      ctx.strokeStyle = rgba(colors.ink, 0.55);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x - 9, y);
      ctx.lineTo(x + 9, y);
      ctx.moveTo(x, y - 9);
      ctx.lineTo(x, y + 9);
      ctx.stroke();
    }
    return fade < 1 || elapsed < sweepMs || (!!view.pin && !view.reducedMotion);
  }

  private onMove = (event: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const offset = this.fromPx(event.clientX - rect.left, event.clientY - rect.top);
    this.hover = offset ? this.read(offset) : null;
    this.canvas.style.cursor = offset ? "crosshair" : "default";
    this.onHover(this.hover);
    this.kick();
  };

  private onLeave = (): void => {
    this.hover = null;
    this.onHover(null);
    this.kick();
  };

  private onClick = (event: MouseEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    const offset = this.fromPx(event.clientX - rect.left, event.clientY - rect.top);
    if (offset) this.onPick({ dx: Math.round(offset.dx), dy: Math.round(offset.dy) });
  };
}

