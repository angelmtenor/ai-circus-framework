/**
 * The shipment globe's stage (see ShipmentGlobeView.tsx): an orthographic globe on a
 * canvas (d3-geo for land and graticule, world-atlas geometry) with every shipment in
 * view drawn as a great-circle arc lifted off the surface — higher for longer routes —
 * and a vehicle glyph travelling along it. Arcs are projected in 3-D by hand (d3's
 * rotation, then an orthographic view of the lifted point), so an arc disappears
 * behind the globe exactly where it should and still shows above the limb.
 *
 * Interaction: drag to rotate, buttons to zoom, click an arc or a destination; when a
 * shipment is selected the globe turns to face it. It idles with a slow spin, and
 * stops animating when hidden or under reduced motion.
 */
import { geoGraticule10, geoInterpolate, geoOrthographic, geoPath, geoRotation, type GeoPermissibleObjects } from "d3-geo";
import type { FeatureCollection, MultiLineString } from "geojson";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import { drawGlyph } from "./dispatchScene";
import { type Glyph, rgba, sizeCanvas } from "./logisticsViz";
import type { SurfaceMode } from "./riskPalette";

export type GlobeColors = {
  ocean: string;
  oceanEdge: string;
  land: string;
  border: string;
  graticule: string;
  ink: string;
  dim: string;
  accent: string;
  halo: string;
  ring: string;
};

export type GlobeArc = {
  id: string;
  origin: [number, number]; // lon, lat
  destination: [number, number];
  color: string;
  emphasis: number; // 0 (recessive) .. 1 (the story)
  width: number;
  glyph: Glyph;
  speed: number; // trips per second
  late?: boolean; // revealed outcome
};

export type GlobePlace = {
  id: string;
  lon: number;
  lat: number;
  label: string;
  kind: "origin" | "destination";
  size: number;
  color: string;
  showLabel: boolean;
};

export type GlobeView = {
  mode: SurfaceMode;
  colors: GlobeColors;
  arcs: GlobeArc[];
  places: GlobePlace[];
  selectedId: string | null;
  revealed: boolean;
  reducedMotion: boolean;
};

export type GlobeCallbacks = {
  onSelectArc: (id: string | null) => void;
  onHoverPlace: (place: GlobePlace | null, x: number, y: number) => void;
  onClickPlace: (place: GlobePlace) => void;
};

type Projected = { x: number; y: number; visible: boolean };

const ARC_SAMPLES = 36;
const SPIN_DEG_PER_S = 3.2;
const IDLE_BEFORE_SPIN_MS = 5000;

export function globeGeometry(land: Topology, countries: Topology): { land: GeoPermissibleObjects; borders: GeoPermissibleObjects } {
  const landObjects = land.objects as Record<string, GeometryCollection>;
  const countryObjects = countries.objects as Record<string, GeometryCollection>;
  return {
    land: feature(land, landObjects.land) as FeatureCollection,
    borders: mesh(countries, countryObjects.countries, (a, b) => a !== b) as MultiLineString,
  };
}

function hash01(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10007) / 10007;
}

function easeInOut(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

export class GlobeScene {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private callbacks: GlobeCallbacks;
  private geometry: { land: GeoPermissibleObjects; borders: GeoPermissibleObjects } | null = null;
  private view: GlobeView | null = null;
  private width = 0;
  private height = 0;
  private dpr = 1;
  private rotate: [number, number] = [-38, -6];
  private zoom = 1;
  private raf = 0;
  private visible = true;
  private lastFrame = 0;
  private lastInteraction = -Infinity;
  private drag: { x: number; y: number; rotate: [number, number]; moved: boolean } | null = null;
  private tween: { from: [number, number]; to: [number, number]; start: number; ms: number } | null = null;
  private arcPaths = new Map<string, { points: [number, number][]; lift: number[] }>();
  private projectedArcs = new Map<string, Projected[]>();
  private projectedPlaces: { place: GlobePlace; x: number; y: number }[] = [];
  private hoverPlace: GlobePlace | null = null;
  private observer: IntersectionObserver;

  constructor(canvas: HTMLCanvasElement, callbacks: GlobeCallbacks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
    this.callbacks = callbacks;
    canvas.addEventListener("pointerdown", this.onDown);
    canvas.addEventListener("pointermove", this.onMove);
    canvas.addEventListener("pointerup", this.onUp);
    canvas.addEventListener("pointerleave", this.onLeave);
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
    this.canvas.removeEventListener("pointerdown", this.onDown);
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerup", this.onUp);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
  }

  setGeometry(geometry: { land: GeoPermissibleObjects; borders: GeoPermissibleObjects }): void {
    this.geometry = geometry;
    this.kick();
  }

  setView(view: GlobeView): void {
    this.view = view;
    for (const arc of view.arcs) {
      if (this.arcPaths.has(arc.id)) continue;
      const interpolate = geoInterpolate(arc.origin, arc.destination);
      const distance = Math.acos(
        Math.min(1, Math.max(-1, Math.sin(this.rad(arc.origin[1])) * Math.sin(this.rad(arc.destination[1])) + Math.cos(this.rad(arc.origin[1])) * Math.cos(this.rad(arc.destination[1])) * Math.cos(this.rad(arc.destination[0] - arc.origin[0])))),
      );
      const peak = 0.04 + 0.2 * Math.min(1, distance / Math.PI);
      const points: [number, number][] = [];
      const lift: number[] = [];
      for (let i = 0; i <= ARC_SAMPLES; i++) {
        const t = i / ARC_SAMPLES;
        points.push(interpolate(t) as [number, number]);
        lift.push(1 + peak * Math.sin(Math.PI * t));
      }
      this.arcPaths.set(arc.id, { points, lift });
    }
    if (this.arcPaths.size > 4000) {
      const keep = new Set(view.arcs.map((a) => a.id));
      for (const id of this.arcPaths.keys()) if (!keep.has(id)) this.arcPaths.delete(id);
    }
    this.kick();
  }

  resize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.dpr = sizeCanvas(this.canvas, width, height);
    this.kick();
  }

  zoomBy(factor: number): void {
    this.zoom = Math.min(2.4, Math.max(0.8, this.zoom * factor));
    this.lastInteraction = performance.now();
    this.kick();
  }

  /** Turn the globe to face a point (e.g. a selected arc's midpoint). */
  focus(lon: number, lat: number): void {
    const to: [number, number] = [-lon, Math.max(-50, Math.min(50, -lat))];
    const from = this.rotate;
    let dLon = ((to[0] - from[0] + 540) % 360) - 180;
    if (Math.abs(dLon) < 0.5 && Math.abs(to[1] - from[1]) < 0.5) return;
    if (this.view?.reducedMotion) {
      this.rotate = [from[0] + dLon, to[1]];
    } else {
      dLon = ((to[0] - from[0] + 540) % 360) - 180;
      this.tween = { from, to: [from[0] + dLon, to[1]], start: performance.now(), ms: 950 };
    }
    this.lastInteraction = performance.now();
    this.kick();
  }

  private rad(deg: number): number {
    return (deg * Math.PI) / 180;
  }

  private get radius(): number {
    return Math.min(this.width, this.height) * 0.43 * this.zoom;
  }

  private kick = (): void => {
    if (!this.raf) this.raf = requestAnimationFrame(this.loop);
  };

  private loop = (now: number): void => {
    this.raf = 0;
    const dt = this.lastFrame ? Math.min(0.1, (now - this.lastFrame) / 1000) : 0;
    this.lastFrame = now;
    const view = this.view;
    if (this.tween) {
      const t = Math.min(1, (now - this.tween.start) / this.tween.ms);
      const e = easeInOut(t);
      this.rotate = [this.tween.from[0] + (this.tween.to[0] - this.tween.from[0]) * e, this.tween.from[1] + (this.tween.to[1] - this.tween.from[1]) * e];
      if (t >= 1) this.tween = null;
    } else if (view && !view.reducedMotion && !this.drag && now - this.lastInteraction > IDLE_BEFORE_SPIN_MS && view.selectedId === null) {
      this.rotate = [this.rotate[0] + SPIN_DEG_PER_S * dt, this.rotate[1]];
    }
    this.draw(now);
    const animate = view && !view.reducedMotion && this.visible && !document.hidden;
    if (animate || this.tween) this.raf = requestAnimationFrame(this.loop);
    else this.lastFrame = 0;
  };

  /** Project a (lon, lat) lifted `lift` × radius off the surface. */
  private project(rotation: (p: [number, number]) => [number, number], lonlat: [number, number], lift: number, cx: number, cy: number, r: number): Projected {
    const [lambda, phi] = rotation(lonlat);
    const l = this.rad(lambda);
    const p = this.rad(phi);
    const x = Math.cos(p) * Math.sin(l) * lift;
    const y = Math.sin(p) * lift;
    const z = Math.cos(p) * Math.cos(l) * lift;
    return { x: cx + r * x, y: cy - r * y, visible: z > 0 || x * x + y * y > 1 };
  }

  private draw(now: number): void {
    const view = this.view;
    const ctx = this.ctx;
    if (!view || this.width < 2) return;
    const { colors } = view;
    const time = now / 1000;
    const cx = this.width / 2;
    const cy = this.height / 2;
    const r = this.radius;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);

    // Atmosphere, ocean with a soft terminator, graticule, land, borders.
    const atmosphere = ctx.createRadialGradient(cx, cy, r * 0.96, cx, cy, r * 1.22);
    atmosphere.addColorStop(0, rgba(colors.accent, view.mode === "dark" ? 0.32 : 0.22));
    atmosphere.addColorStop(1, rgba(colors.accent, 0));
    ctx.fillStyle = atmosphere;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 1.22, 0, Math.PI * 2);
    ctx.fill();
    const ocean = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.1, cx, cy, r);
    ocean.addColorStop(0, colors.ocean);
    ocean.addColorStop(1, colors.oceanEdge);
    ctx.fillStyle = ocean;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    const projection = geoOrthographic().scale(r).translate([cx, cy]).rotate([this.rotate[0], this.rotate[1], 0]).clipAngle(90).precision(0.6);
    const path = geoPath(projection, ctx);
    ctx.beginPath();
    path(geoGraticule10());
    ctx.strokeStyle = colors.graticule;
    ctx.lineWidth = 0.6;
    ctx.stroke();
    if (this.geometry) {
      ctx.beginPath();
      path(this.geometry.land);
      ctx.fillStyle = colors.land;
      ctx.fill();
      ctx.beginPath();
      path(this.geometry.borders);
      ctx.strokeStyle = colors.border;
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }
    const shade = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.2, cx, cy, r * 1.02);
    shade.addColorStop(0, "rgba(255,255,255,0)");
    shade.addColorStop(1, view.mode === "dark" ? "rgba(0,0,0,0.42)" : "rgba(30,50,80,0.16)");
    ctx.fillStyle = shade;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();

    // Arcs: recessive first, the story on top, the selection last.
    const rotation = geoRotation([this.rotate[0], this.rotate[1], 0]) as unknown as (p: [number, number]) => [number, number];
    const selected = view.selectedId;
    const ordered = [...view.arcs].sort((a, b) => (a.id === selected ? 1 : b.id === selected ? -1 : a.emphasis - b.emphasis));
    this.projectedArcs.clear();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const arc of ordered) {
      const geo = this.arcPaths.get(arc.id);
      if (!geo) continue;
      const pts = geo.points.map((p, i) => this.project(rotation, p, geo.lift[i], cx, cy, r));
      this.projectedArcs.set(arc.id, pts);
      const isSelected = arc.id === selected;
      const dim = selected !== null && !isSelected;
      const alpha = (0.28 + 0.62 * arc.emphasis) * (dim ? 0.3 : 1);
      ctx.strokeStyle = isSelected ? colors.accent : rgba(arc.color, alpha);
      ctx.lineWidth = isSelected ? 3.2 : arc.width;
      this.strokeVisible(ctx, pts);
      if (view.revealed && arc.late) {
        ctx.setLineDash([2, 4]);
        ctx.strokeStyle = rgba(colors.ring, dim ? 0.35 : 0.95);
        ctx.lineWidth = 1.2;
        this.strokeVisible(ctx, pts);
        ctx.setLineDash([]);
      }
    }

    // Travelling vehicles.
    if (!view.reducedMotion) {
      for (const arc of ordered) {
        const pts = this.projectedArcs.get(arc.id);
        if (!pts) continue;
        const isSelected = arc.id === selected;
        if (selected !== null && !isSelected && arc.emphasis < 0.5) continue;
        const t = (time * arc.speed + hash01(arc.id)) % 1;
        const f = t * ARC_SAMPLES;
        const i = Math.min(ARC_SAMPLES - 1, Math.floor(f));
        const a = pts[i];
        const b = pts[i + 1];
        if (!a.visible || !b.visible) continue;
        const x = a.x + (b.x - a.x) * (f - i);
        const y = a.y + (b.y - a.y) * (f - i);
        const angle = Math.atan2(b.y - a.y, b.x - a.x);
        const size = isSelected ? 17 : 8 + 5 * arc.emphasis;
        const color = isSelected ? colors.accent : arc.color;
        if (arc.emphasis >= 0.5 || isSelected) {
          ctx.beginPath();
          ctx.arc(x, y, size * 0.62, 0, Math.PI * 2);
          ctx.fillStyle = rgba(colors.halo, 0.78);
          ctx.fill();
        }
        // Planes point along the route; ground and sea vehicles stay upright, facing it.
        if (arc.glyph === "plane") drawGlyph(ctx, "plane", x, y, size, angle + Math.PI / 2, color);
        else {
          ctx.save();
          ctx.translate(x, y);
          if (Math.cos(angle) < 0) ctx.scale(-1, 1);
          drawGlyph(ctx, arc.glyph, 0, 0, size, 0, color);
          ctx.restore();
        }
      }
    }

    // Places: origins as ink diamonds, destinations as rings sized by volume.
    this.projectedPlaces = [];
    ctx.font = "600 11px Inter, system-ui, sans-serif";
    ctx.textBaseline = "middle";
    for (const place of view.places) {
      const p = this.project(rotation, [place.lon, place.lat], 1, cx, cy, r);
      if (!p.visible || Math.hypot(p.x - cx, p.y - cy) > r) continue;
      this.projectedPlaces.push({ place, x: p.x, y: p.y });
      const hovered = this.hoverPlace?.id === place.id;
      if (place.kind === "origin") {
        const s = 3 + place.size;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y - s);
        ctx.lineTo(p.x + s, p.y);
        ctx.lineTo(p.x, p.y + s);
        ctx.lineTo(p.x - s, p.y);
        ctx.closePath();
        ctx.fillStyle = colors.ink;
        ctx.fill();
        ctx.strokeStyle = colors.halo;
        ctx.lineWidth = 1;
        ctx.stroke();
      } else {
        const s = 3 + place.size;
        ctx.beginPath();
        ctx.arc(p.x, p.y, s, 0, Math.PI * 2);
        ctx.fillStyle = rgba(place.color, 0.28);
        ctx.fill();
        ctx.strokeStyle = place.color;
        ctx.lineWidth = hovered ? 2.6 : 1.6;
        ctx.stroke();
      }
      if (place.showLabel || hovered) {
        ctx.textAlign = "left";
        ctx.lineWidth = 3;
        ctx.strokeStyle = rgba(colors.halo, 0.9);
        ctx.strokeText(place.label, p.x + 7 + place.size, p.y);
        ctx.fillStyle = place.kind === "origin" ? colors.dim : colors.ink;
        ctx.fillText(place.label, p.x + 7 + place.size, p.y);
      }
    }
  }

  private strokeVisible(ctx: CanvasRenderingContext2D, pts: Projected[]): void {
    ctx.beginPath();
    let open = false;
    for (const p of pts) {
      if (!p.visible) {
        open = false;
        continue;
      }
      if (!open) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
      open = true;
    }
    ctx.stroke();
  }

  private local(event: PointerEvent): [number, number] {
    const rect = this.canvas.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  }

  private placeAt(x: number, y: number): GlobePlace | null {
    let best: GlobePlace | null = null;
    let bestDistance = 12;
    for (const { place, x: px, y: py } of this.projectedPlaces) {
      const d = Math.hypot(px - x, py - y) - place.size;
      if (d < bestDistance) {
        bestDistance = d;
        best = place;
      }
    }
    return best;
  }

  private arcAt(x: number, y: number): string | null {
    let best: string | null = null;
    let bestDistance = 7;
    for (const [id, pts] of this.projectedArcs) {
      for (let i = 0; i < pts.length - 1; i++) {
        const a = pts[i];
        const b = pts[i + 1];
        if (!a.visible || !b.visible) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = dx * dx + dy * dy || 1;
        const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / len));
        const d = Math.hypot(a.x + t * dx - x, a.y + t * dy - y);
        if (d < bestDistance) {
          bestDistance = d;
          best = id;
        }
      }
    }
    return best;
  }

  private onDown = (event: PointerEvent): void => {
    const [x, y] = this.local(event);
    this.drag = { x, y, rotate: [...this.rotate] as [number, number], moved: false };
    this.tween = null;
    this.canvas.setPointerCapture(event.pointerId);
    this.lastInteraction = performance.now();
  };

  private onMove = (event: PointerEvent): void => {
    const [x, y] = this.local(event);
    if (this.drag) {
      const dx = x - this.drag.x;
      const dy = y - this.drag.y;
      if (Math.hypot(dx, dy) > 3) this.drag.moved = true;
      const k = 0.28 / this.zoom;
      this.rotate = [this.drag.rotate[0] + dx * k, Math.max(-70, Math.min(70, this.drag.rotate[1] - dy * k))];
      this.lastInteraction = performance.now();
      this.kick();
      return;
    }
    const place = this.placeAt(x, y);
    if (place?.id !== this.hoverPlace?.id) {
      this.hoverPlace = place;
      this.kick();
    }
    this.canvas.style.cursor = place || this.arcAt(x, y) ? "pointer" : "grab";
    this.callbacks.onHoverPlace(place, x, y);
  };

  private onUp = (event: PointerEvent): void => {
    const drag = this.drag;
    this.drag = null;
    if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId);
    if (!drag || drag.moved) return;
    const [x, y] = this.local(event);
    const place = this.placeAt(x, y);
    if (place) {
      this.callbacks.onClickPlace(place);
      return;
    }
    this.callbacks.onSelectArc(this.arcAt(x, y));
  };

  private onLeave = (): void => {
    this.hoverPlace = null;
    this.callbacks.onHoverPlace(null, 0, 0);
    this.kick();
  };
}
