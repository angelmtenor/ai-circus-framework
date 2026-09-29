/**
 * Shared by the two logistics showpiece tabs (DispatchTowerView, ShipmentGlobeView):
 * vehicle glyphs (Path2D for canvas; VehicleIcon.tsx draws them in the DOM), the validated colour sets, and
 * small helpers.
 *
 * Colour (dataviz reference palette, see riskPalette.ts for the risk tiers):
 * - `SEQUENTIAL_BLUE`: magnitude (an ETA in days) — one hue, light→dark on a light
 *   surface; on a dark surface the anchor flips (the low end recedes into the surface).
 * - `CATEGORICAL`: identity (a carrier) — the first slots of the fixed categorical
 *   order, per surface; every lane that uses one is also direct-labelled.
 */
import { useEffect, useState } from "react";
import type { VehicleGlyph } from "./apiClient";
import type { SurfaceMode } from "./riskPalette";

export type Glyph = VehicleGlyph | "parcel";

// 24×24, filled, pointing right/up as drawn (callers rotate for a heading).
export const GLYPH_PATHS: Record<Glyph, string> = {
  plane: "M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5Z",
  truck: "M2 5.5h12.5v10H2ZM15.5 8.5h3.6l2.9 3.4v3.6h-6.5ZM6 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm11.5 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  van: "M2 7.5h12.5l4 4H22v4.5H2ZM4.5 9v2.5h4V9Zm5.5 0v2.5h3.5L11.2 9ZM6.5 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm11 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  ship: "M2.5 14.5h19l-3.2 5.5H5.7ZM5.5 13.5V9h13v4.5ZM10 8V4.5h4V8Z",
  bolt: "M13.5 2 4 14h7l-1.5 8L20 10h-7Z",
  rail: "M7 3h10a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3Zm-1 3.5V11h12V6.5ZM8 21.5l2-3h4l2 3Z",
  parcel: "M12 2l9 5-9 5-9-5Zm-9 6.7 8.3 4.6V22L3 17.4Zm18 0v8.7L12.7 22v-8.7Z",
};

const PATH_CACHE = new Map<Glyph, Path2D>();

/** The glyph as a canvas Path2D in its 24×24 box (cached). */
export function glyphPath(glyph: Glyph): Path2D {
  let path = PATH_CACHE.get(glyph);
  if (!path) {
    path = new Path2D(GLYPH_PATHS[glyph]);
    PATH_CACHE.set(glyph, path);
  }
  return path;
}

// Reference sequential blue, steps 100 → 700.
const SEQUENTIAL_BLUE = [
  "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
  "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]; // fmt: skip

function mix(a: string, b: string, t: number): string {
  const pa = parseInt(a.slice(1), 16);
  const pb = parseInt(b.slice(1), 16);
  const ch = (shift: number) => Math.round(((pa >> shift) & 255) * (1 - t) + ((pb >> shift) & 255) * t);
  return `rgb(${ch(16)}, ${ch(8)}, ${ch(0)})`;
}

/** Sequential colour for t ∈ [0, 1] (0 = least). Light: step 100 → 700. Dark: the
 * anchor flips — 700 (near the dark surface) → 100. */
export function sequentialColor(t: number, mode: SurfaceMode): string {
  const steps = mode === "light" ? SEQUENTIAL_BLUE : [...SEQUENTIAL_BLUE].reverse();
  const x = Math.min(1, Math.max(0, t)) * (steps.length - 1);
  const i = Math.min(steps.length - 2, Math.floor(x));
  return mix(steps[i], steps[i + 1], x - i);
}

/** The first four categorical slots (blue, orange, aqua, yellow) per surface. */
export const CATEGORICAL = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500"],
} as const;

export function rgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, (c) => c + c) : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function usePrefersReducedMotion(): boolean {
  const query = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [reduced, setReduced] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [query]);
  return reduced;
}

/** An element's content size, tracked with a ResizeObserver. Returns a callback ref, so
 * it follows the element even when it mounts after the first render (e.g. after a
 * loading state). */
export function useElementSize<T extends HTMLElement>(): [(el: T | null) => void, { width: number; height: number }] {
  const [element, setElement] = useState<T | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    if (!element) return;
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    measure();
    return () => observer.disconnect();
  }, [element]);
  return [setElement, size];
}

/** Fetch a JSON asset once per session (topology files shipped as Vite `?url` assets). */
const JSON_CACHE = new Map<string, Promise<unknown>>();
export function fetchJsonOnce<T>(url: string): Promise<T> {
  if (!JSON_CACHE.has(url)) {
    JSON_CACHE.set(
      url,
      fetch(url).then((r) => {
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
      }),
    );
  }
  return JSON_CACHE.get(url) as Promise<T>;
}

/** A pixel-ratio-aware canvas resize; returns the device pixel ratio used. */
export function sizeCanvas(canvas: HTMLCanvasElement, width: number, height: number, maxDpr = 2): number {
  const dpr = Math.min(maxDpr, window.devicePixelRatio || 1);
  const w = Math.max(1, Math.round(width * dpr));
  const h = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  return dpr;
}
