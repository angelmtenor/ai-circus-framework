import { tronLightTheme } from "./tron-light";
import { tronTheme } from "./tron";
import type { Theme } from "./types";
import { whiteGreenTheme } from "./white-green";

export type { Theme };

/**
 * Every available theme, in picker order. Adding another is just another entry here
 * (plus its own themes/<id>.ts) — no other file changes.
 */
export const THEMES: Theme[] = [tronTheme, tronLightTheme, whiteGreenTheme];

export const DEFAULT_THEME_ID = tronTheme.id;

export function getTheme(id: string): Theme {
  return THEMES.find((t) => t.id === id) ?? tronTheme;
}
