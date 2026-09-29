/**
 * Colours shared by the risk views (RiskWatchlistView, NetworkExplorerView) — every
 * set validated with the dataviz palette validator against the surface it renders on.
 *
 * - `RISK_RAMP.tiers`: risk tiers are ordered, so a one-hue ordinal red ramp (stepped
 *   per light/dark surface, `--ordinal`); the lowest tier is the recessive `base` grey
 *   (most entities are context, not the story). A tier never relies on colour alone —
 *   marks and rows also carry its glyph (`TIER_GLYPHS`) and label.
 * - `up`/`down`: the blue↔red diverging pair for SHAP pushes.
 * - `NETWORK_PALETTE`: the network stage — its own surfaces, the entity hue (violet:
 *   passes the all-pairs CVD checks against every red tier in both modes), and three
 *   community hues (the reference palette's first three slots — the most a spatial,
 *   all-pairs form validates; smaller communities stay grey, and every community hull
 *   carries a direct label, so colour is never alone). In community mode entities are
 *   drawn in ink: violet sits too close to the community blue on the dark stage.
 */

export type SurfaceMode = "dark" | "light";

export const RISK_RAMP = {
  dark: { base: "#465266", tiers: ["#9b3039", "#dd4a52", "#ff9a8f"], up: "#e66767", down: "#3987e5", ring: "#f3fbff" },
  light: { base: "#aab4c3", tiers: ["#f08c8c", "#d9434a", "#9e1b24"], up: "#e34948", down: "#2a78d6", ring: "#0b1f33" },
} as const;

export const TIER_GLYPHS = ["●", "▲", "◆", "✖"];

export const NETWORK_PALETTE = {
  dark: {
    stage: "#0a1020",
    stageEdge: "#04060d",
    grid: "rgba(127, 163, 201, 0.10)",
    ink: "#e8f1fb",
    dim: "#8ea4c0",
    halo: "rgba(10, 16, 32, 0.92)",
    link: "#6f93c4",
    context: "#7b879c",
    entity: "#9085e9",
    path: "#33c7ff",
    communities: ["#3987e5", "#d95926", "#199e70"],
    other: "#5b6578",
  },
  light: {
    stage: "#f6f8fb",
    stageEdge: "#e3e9f1",
    grid: "rgba(22, 32, 43, 0.07)",
    ink: "#0f1b2a",
    dim: "#5b7086",
    halo: "rgba(246, 248, 251, 0.94)",
    link: "#5d7390",
    context: "#9aa6b6",
    entity: "#4a3aa7",
    path: "#0072e0",
    communities: ["#2a78d6", "#eb6834", "#1baf7a"],
    other: "#b9c2cf",
  },
} as const;

/** Dark or light, from the active theme's `--bg` (the app has no other flag for it). */
export function surfaceMode(bg: string | undefined): SurfaceMode {
  const hex = (bg ?? "#05070f").replace("#", "");
  return parseInt(hex.slice(0, 2), 16) < 0x80 ? "dark" : "light";
}

/** The tier colour: the recessive base for tier 0, then the ordinal red ramp. */
export function tierColor(mode: SurfaceMode, tier: number): string {
  const ramp = RISK_RAMP[mode];
  return tier === 0 ? ramp.base : ramp.tiers[Math.min(tier - 1, ramp.tiers.length - 1)];
}
