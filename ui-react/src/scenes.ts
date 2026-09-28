/**
 * The illustrations VoyageView.tsx can draw people into (`ui_extras.scene`), each a
 * self-contained module: its own coordinate space, how it seats N people per zone,
 * its artwork behind and in front of the dots, and its own wording ("aboard" vs "in
 * the tower"). VoyageView itself stays scene-agnostic.
 */

import type { ComponentType } from "react";

export type Point = { x: number; y: number };
export type Rect = { x0: number; x1: number; y0: number; y1: number };

/** Where each zone's people sit (fill order; one spare slot per zone for a what-if
 * person), the shared dot radius, and — for scenes whose artwork follows the data —
 * each zone's bounding box. */
export type ZoneLayout = { slots: Point[][]; radius: number; zoneRects?: Rect[] };

export type SceneCopy = {
  loadingIcon: string;
  loading: (noun: string) => string;
  aboard: string; // "aboard" / "in the tower"
  everyone: string; // "Everyone aboard"
  reveal: string; // the real-outcome toggle
  star: string; // where the what-if person's ★ is drawn
  pickHint: (noun: string) => string;
};

export type SceneModule = {
  width: number;
  height: number;
  packZones: (counts: number[]) => ZoneLayout;
  Backdrop: ComponentType<{ layout: ZoneLayout; zoneLabels: string[] }>;
  Foreground: ComponentType<{ nameplate: string }>;
  /** Seconds to delay a dot's colour change when the real outcome is revealed. */
  revealDelay: (at: Point) => number;
  copy: SceneCopy;
};

/** "a passenger" / "an engineer". */
export function withArticle(noun: string): string {
  return `${/^[aeiou]/i.test(noun) ? "an" : "a"} ${noun}`;
}
