import { getronicsTheme } from "./getronics";
import { tronTheme } from "./tron";
import type { Theme } from "./types";

export type { Theme };

/**
 * Every available theme, in picker order. Adding another is just another entry here
 * (plus its own themes/<id>.ts) — no other file changes.
 */
export const THEMES: Theme[] = [tronTheme, getronicsTheme];

export const DEFAULT_THEME_ID = tronTheme.id;

export function getTheme(id: string): Theme {
  return THEMES.find((t) => t.id === id) ?? tronTheme;
}
