/**
 * Framing for the SVG maps (GeoBubbleMap, DataStoreMap): which projection shows a set of places,
 * and which countries fall inside it. Pure d3-geo, no drawing and no frame loop.
 */
import { interpolateRgbBasis } from "d3-interpolate";
import { geoBounds, geoConicConformal, geoContains, geoNaturalEarth1, type GeoPermissibleObjects, type GeoProjection } from "d3-geo";
import type { Feature } from "geojson";
import type { SurfaceMode } from "./riskPalette";

export type LatLon = { lat: number; lon: number };

const MIN_HALF_LON = 6;
const MIN_HALF_LAT = 4.2;
// Past this span a conic projection distorts badly, so a world-wide set takes a world projection.
const CONIC_MAX_LON_SPAN = 60;
// Enough to find the countries that hold the places, without testing every point of a large set.
const FOCUS_SAMPLE = 200;

export function locatedOnly<T extends LatLon>(places: T[]): T[] {
  return places.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon));
}

/**
 * A projection that shows every place, and at least a country's span so that a single place still
 * gets its surroundings. `fallback` frames the map before the places are known.
 */
export function fitPlaces(places: LatLon[], width: number, height: number, margin = 16, fallback: [[number, number], [number, number]] = [[-9.4, 3.3], [36, 43.8]]): GeoProjection {
  const located = locatedOnly(places);
  const lons = located.length ? located.map((p) => p.lon) : fallback[0];
  const lats = located.length ? located.map((p) => p.lat) : fallback[1];
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const extent: [[number, number], [number, number]] = [
    [margin, margin],
    [width - margin, height - margin],
  ];
  const centre = [(minLon + maxLon) / 2, (minLat + maxLat) / 2];
  const halfLon = Math.max(MIN_HALF_LON, (maxLon - minLon) / 2 + 1.1);
  const halfLat = Math.max(MIN_HALF_LAT, (maxLat - minLat) / 2 + 1.1);
  // Every corner and every place: a conic projection bends the frame, so two corners are not enough.
  const frame: GeoPermissibleObjects = {
    type: "MultiPoint",
    coordinates: [
      [centre[0] - halfLon, centre[1] - halfLat],
      [centre[0] + halfLon, centre[1] - halfLat],
      [centre[0] - halfLon, centre[1] + halfLat],
      [centre[0] + halfLon, centre[1] + halfLat],
      ...located.map((p) => [p.lon, p.lat]),
    ],
  };
  if (maxLon - minLon > CONIC_MAX_LON_SPAN) return geoNaturalEarth1().fitExtent(extent, frame);
  return geoConicConformal()
    .parallels([centre[1] - 4, centre[1] + 4])
    .rotate([-centre[0], 0])
    .fitExtent(extent, frame);
}

/** The countries inside the frame; those holding a place are the focus, the rest context. */
export function landInFrame(world: Feature[], places: LatLon[], projection: GeoProjection, width: number, height: number): { focus: Feature[]; context: Feature[] } {
  const located = locatedOnly(places).slice(0, FOCUS_SAMPLE);
  const [[lon0, lat0], [lon1, lat1]] = [projection.invert!([0, height])!, projection.invert!([width, 0])!];
  const visible = world.filter((f) => {
    const [[x0, y0], [x1, y1]] = geoBounds(f);
    return x1 >= lon0 - 5 && x0 <= lon1 + 5 && y1 >= lat0 - 5 && y0 <= lat1 + 5;
  });
  const focus = visible.filter((f) => located.some((p) => geoContains(f, [p.lon, p.lat])));
  return { focus, context: visible.filter((f) => !focus.includes(f)) };
}

// One hue from recessive to emphatic, its own steps per surface, so high values stand out on both.
const SEQUENTIAL_RAMP: Record<SurfaceMode, string[]> = {
  dark: ["#7c2d12", "#c2410c", "#f97316", "#fdba74", "#fed7aa"],
  light: ["#fed7aa", "#fb923c", "#ea580c", "#9a3412", "#431407"],
};

export function sequentialColour(mode: SurfaceMode): (t: number) => string {
  return interpolateRgbBasis(SEQUENTIAL_RAMP[mode]);
}

/** The CSS gradient of the same ramp, for a legend. */
export function sequentialGradient(mode: SurfaceMode): string {
  return `linear-gradient(to right, ${SEQUENTIAL_RAMP[mode].join(", ")})`;
}
