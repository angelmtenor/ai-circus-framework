import { DEMO_THEMES } from "virtual:demo-themes";
import { tronLightTheme } from "./tron-light";
import { tronTheme } from "./tron";
import type { Theme } from "./types";
import { whiteGreenTheme } from "./white-green";

export type { Theme };

/**
 * Every available theme, in picker order. Adding a built-in is just another entry
 * here (plus its own themes/<id>.ts) — no other file changes. DEMO_THEMES is an
 * optional overlay resolved at build/dev time from demo/themes/ (untracked, may not
 * exist) — see vite-plugins/demo-themes.ts. Empty there means no change here.
 */
export const THEMES: Theme[] = [tronTheme, tronLightTheme, whiteGreenTheme, ...DEMO_THEMES];

export const DEFAULT_THEME_ID = tronTheme.id;

export function getTheme(id: string): Theme {
  return THEMES.find((t) => t.id === id) ?? tronTheme;
}
