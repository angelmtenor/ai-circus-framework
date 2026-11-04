import { useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { geoAlbersUsa, geoPath, type GeoProjection } from "d3-geo";
import { select } from "d3-selection";
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from "d3-zoom";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { Feature, FeatureCollection } from "geojson";
import countriesUrl from "world-atlas/countries-50m.json?url";
import usStatesUrl from "us-atlas/states-10m.json?url";
import { fitPlaces, landInFrame, type LatLon } from "./geoMapFrame";
import { fetchJsonOnce } from "./logisticsViz";
import "./geoBubbleMap.css";

/** One mark, in paint order. The same id may appear twice: a dot and a ring around it. */
export type GeoMark = LatLon & {
  id: string;
  r: number;
  fill: string;
  fillOpacity?: number;
  stroke?: string;
  strokeWidth?: number;
};

export type GeoTip = { title: string; lines: string[] };

/** `usa` includes the Alaska and Hawaii insets; `places` frames the given places on the world map. */
export type GeoFrame = { kind: "usa" } | { kind: "places"; places: LatLon[] };

type Placed = GeoMark & { x: number; y: number };
type Layer = { key: string; d: string; fill: string; fillOpacity: number; stroke: string; strokeWidth: number };

const MAX_ZOOM = 16;
// Extra room around a mark that still counts as pointing at it, in screen pixels.
const HIT_SLOP = 3;

function circlePath(x: number, y: number, r: number): string {
  const d = 2 * r;
  return `M${(x - r).toFixed(2)} ${y.toFixed(2)}a${r.toFixed(2)} ${r.toFixed(2)} 0 1 0 ${d.toFixed(2)} 0a${r.toFixed(2)} ${r.toFixed(2)} 0 1 0 ${(-d).toFixed(2)} 0`;
}

/** Consecutive marks of one style become one path, so thousands of dots stay a few DOM nodes. */
function toLayers(marks: Placed[], k: number): Layer[] {
  const layers: Layer[] = [];
  let parts: string[] = [];
  let style = "";
  let head: Placed | null = null;
  const flush = () => {
    if (!head) return;
    layers.push({
      key: `${layers.length}`,
      d: parts.join(""),
      fill: head.fill,
      fillOpacity: head.fillOpacity ?? 1,
      stroke: head.stroke ?? "none",
      strokeWidth: head.strokeWidth ?? 0,
    });
  };
  for (const m of marks) {
    const next = `${m.fill}|${m.fillOpacity}|${m.stroke}|${m.strokeWidth}`;
    if (next !== style) {
      flush();
      parts = [];
      style = next;
      head = m;
    }
    parts.push(circlePath(m.x, m.y, m.r / k));
  }
  flush();
  return layers;
}

/** The mark under a point: the nearest centre within reach. Map units, so `k` scales the reach. */
function pick(marks: Placed[], x: number, y: number, k: number): Placed | null {
  let best: Placed | null = null;
  let bestDistance = Infinity;
  for (const m of marks) {
    const reach = (m.r + HIT_SLOP) / k;
    const dx = m.x - x;
    const dy = m.y - y;
    const distance = dx * dx + dy * dy;
    if (distance <= reach * reach && distance <= bestDistance) {
      best = m;
      bestDistance = distance;
    }
  }
  return best;
}

/**
 * A bubble map in plain SVG: land from the world or US atlas, marks as a few grouped paths, one
 * tooltip. Static between inputs: no frame loop, and pan and zoom move a transform rather than
 * redrawing, so an idle map costs nothing. The rows beside it are the accessible alternative.
 */
export function GeoBubbleMap({
  marks,
  frame,
  tip,
  onSelect,
  label,
  width = 960,
  height = 560,
  children,
}: {
  marks: GeoMark[];
  frame: GeoFrame;
  tip: (id: string) => GeoTip | null;
  onSelect?: (id: string) => void;
  label: string;
  width?: number;
  height?: number;
  children?: ReactNode;
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const viewRef = useRef<SVGGElement | null>(null);
  const transformRef = useRef<ZoomTransform>(zoomIdentity);
  const zoomRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const [k, setK] = useState(1);
  const [hover, setHover] = useState<(Placed & { screen: [number, number] }) | null>(null);
  const [world, setWorld] = useState<Feature[] | null>(null);
  const [states, setStates] = useState<{ land: FeatureCollection; borders: GeoJSON.MultiLineString } | null>(null);
  const isUsa = frame.kind === "usa";

  useEffect(() => {
    let cancelled = false;
    if (isUsa) {
      fetchJsonOnce<Topology>(usStatesUrl)
        .then((t) => {
          if (cancelled) return;
          const objects = t.objects.states as GeometryCollection;
          setStates({ land: feature(t, objects) as FeatureCollection, borders: mesh(t, objects, (a, b) => a !== b) });
        })
        .catch(() => undefined);
    } else {
      fetchJsonOnce<Topology>(countriesUrl)
        .then((t) => !cancelled && setWorld((feature(t, t.objects.countries as GeometryCollection) as FeatureCollection).features))
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [isUsa]);

  const projection = useMemo<GeoProjection>(() => {
    if (frame.kind === "usa") {
      // d3's own framing of the insets is 1070 scale on a 960×500 canvas.
      const scale = 1070 * Math.min(width / 960, height / 500);
      return geoAlbersUsa().scale(scale).translate([width / 2, height / 2]) as unknown as GeoProjection;
    }
    return fitPlaces(frame.places, width, height);
  }, [frame, width, height]);

  const landPaths = useMemo(() => {
    const path = geoPath(projection);
    if (frame.kind === "usa") {
      return states ? { focus: path(states.land) ?? "", context: "", borders: path(states.borders) ?? "" } : null;
    }
    if (!world) return null;
    const { focus, context } = landInFrame(world, frame.places, projection, width, height);
    return {
      focus: path({ type: "FeatureCollection", features: focus }) ?? "",
      context: path({ type: "FeatureCollection", features: context }) ?? "",
      borders: "",
    };
  }, [frame, projection, world, states, width, height]);

  const placed = useMemo(() => {
    const out: Placed[] = [];
    for (const m of marks) {
      const xy = Number.isFinite(m.lat) && Number.isFinite(m.lon) ? projection([m.lon, m.lat]) : null;
      if (xy) out.push({ ...m, x: xy[0], y: xy[1] });
    }
    return out;
  }, [marks, projection]);
  const layers = useMemo(() => toLayers(placed, k), [placed, k]);
  const offMap = new Set(marks.map((m) => m.id)).size - new Set(placed.map((m) => m.id)).size;

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const behaviour = zoom<SVGSVGElement, unknown>()
      .scaleExtent([1, MAX_ZOOM])
      .translateExtent([
        [0, 0],
        [width, height],
      ])
      .on("zoom", (event: { transform: ZoomTransform }) => {
        transformRef.current = event.transform;
        viewRef.current?.setAttribute("transform", event.transform.toString());
        setHover(null);
      })
      // Dots grow with the gesture and take their true size when it ends: one redraw, not one per frame.
      .on("end", (event: { transform: ZoomTransform }) => setK(event.transform.k));
    zoomRef.current = behaviour;
    select(svg).call(behaviour);
    return () => {
      select(svg).on(".zoom", null);
    };
  }, [width, height]);

  // A new frame starts from the whole map.
  useEffect(() => {
    if (svgRef.current && zoomRef.current) select(svgRef.current).call(zoomRef.current.transform, zoomIdentity);
  }, [projection]);

  const markAt = (clientX: number, clientY: number): Placed | null => {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    if (!svg || !ctm) return null;
    const point = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
    const [x, y] = transformRef.current.invert([point.x, point.y]);
    return pick(placed, x, y, transformRef.current.k);
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (event.buttons) return;
    const found = markAt(event.clientX, event.clientY);
    // A zoom clears the hover, so the position taken here stays right for as long as it shows.
    const next = found && { ...found, screen: transformRef.current.apply([found.x, found.y]) };
    setHover((current) => (current?.id === next?.id ? current : next));
  };

  const tipContent = hover ? tip(hover.id) : null;
  const screen = hover?.screen;

  return (
    <div className="geo-map" data-points={placed.length}>
      <div className="geo-map__stage">
        <svg
          ref={svgRef}
          className={`geo-map__surface${hover && onSelect ? " geo-map__surface--pointing" : ""}`}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={label}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHover(null)}
          onClick={(event) => {
            const found = markAt(event.clientX, event.clientY);
            if (found) onSelect?.(found.id);
          }}
        >
          <g ref={viewRef}>
            {landPaths && (
              <>
                {landPaths.context && <path d={landPaths.context} className="geo-map__land geo-map__land--context" />}
                <path d={landPaths.focus} className="geo-map__land" />
                {landPaths.borders && <path d={landPaths.borders} className="geo-map__borders" />}
              </>
            )}
            <g className="geo-map__marks">
              {layers.map((l) => (
                <path key={l.key} d={l.d} fill={l.fill} fillOpacity={l.fillOpacity} stroke={l.stroke} strokeWidth={l.strokeWidth} />
              ))}
              {hover && <circle cx={hover.x} cy={hover.y} r={(hover.r + 2) / k} className="geo-map__hover" />}
            </g>
          </g>
        </svg>
        {tipContent && screen && (
          <div
            className="geo-map__tip"
            style={{ left: `${(100 * screen[0]) / width}%`, top: `${(100 * screen[1]) / height}%` }}
            data-side={screen[0] > width * 0.6 ? "left" : "right"}
          >
            <b>{tipContent.title}</b>
            {tipContent.lines.map((line) => (
              <span key={line}>{line}</span>
            ))}
          </div>
        )}
        {k > 1 && (
          <button
            type="button"
            className="geo-map__reset"
            onClick={() => {
              if (svgRef.current && zoomRef.current) select(svgRef.current).call(zoomRef.current.transform, zoomIdentity);
              setK(1);
            }}
          >
            Reset view
          </button>
        )}
      </div>
      {children}
      {offMap > 0 && <p className="panel-hint geo-map__note">{offMap.toLocaleString()} outside this map.</p>}
    </div>
  );
}
