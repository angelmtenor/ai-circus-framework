import logo from "../assets/logo-white-green.svg";
import type { Theme } from "./types";

/**
 * A light, corporate-green counterpart to tron-light.ts: white surfaces, a deep
 * green primary accent instead of blue/cyan, same flat (no-glow) treatment.
 */
export const whiteGreenTheme: Theme = {
  id: "white-green",
  label: "White Green",
  logo,
  cssVars: {
    "--bg": "#ffffff",
    "--panel": "#ffffff",
    "--panel-2": "#f4faf6",
    "--panel-3": "#e9f7ef",
    "--border": "#cdead9",
    "--border-strong": "#9dcbb0",
    "--text": "#122318",
    "--text-h": "#0a160d",
    "--dim": "#587465",
    "--code-bg": "#eef8f1",
    "--accent": "#178a4c",
    "--accent-2": "#39c774",
    "--accent-soft": "rgba(23, 138, 76, 0.1)",
    "--accent-bg": "rgba(23, 138, 76, 0.08)",
    "--accent-border": "rgba(23, 138, 76, 0.45)",
    "--purple": "#7c5cff",
    "--purple-soft": "rgba(124, 92, 255, 0.12)",
    "--green": "#0fb06a",
    "--blue": "#2f7bc4",
    "--amber": "#c98a00",
    "--red": "#d9455f",
    "--teal": "#0fa3a3",
    "--pink": "#c2437b",
    "--lime": "#a9d139",
    "--social-bg": "rgba(18, 35, 24, 0.05)",
    // Flat corporate look, on purpose — no neon glow (mirrors tron-light.ts).
    "--glow-accent": "none",
    "--glow-accent-soft": "none",
    "--shadow": "rgba(16, 40, 28, 0.08) 0 10px 20px -3px, rgba(16, 40, 28, 0.04) 0 0 0 1px",
    "--shadow-sm": "0 1px 2px rgba(16, 40, 28, 0.06)",
    "--shadow-md": "0 8px 24px rgba(16, 40, 28, 0.1), 0 0 0 1px rgba(16, 40, 28, 0.04)",
    // See tron-light.ts's comment on this key: not a custom property, but
    // useTheme.ts's applyTheme loop sets it the same way, flipping native
    // form-control rendering to its light variant.
    "color-scheme": "light",
  },
  categoryPalette: ["#178a4c", "#2f7bc4", "#7c5cff", "#d9455f", "#0fa3a3", "#c98a00", "#c2437b", "#a9d139"],
  plotlyLayout: {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#122318", family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif" },
    colorway: ["#178a4c", "#2f7bc4", "#7c5cff", "#d9455f", "#0fa3a3", "#c98a00", "#c2437b", "#a9d139"],
    xaxis: { gridcolor: "#e3efe7", zerolinecolor: "#c6c6c6", linecolor: "#cdead9", color: "#587465", automargin: true },
    yaxis: { gridcolor: "#e3efe7", zerolinecolor: "#c6c6c6", linecolor: "#cdead9", color: "#587465", automargin: true },
    scene: {
      xaxis: { gridcolor: "#e3efe7", backgroundcolor: "rgba(0,0,0,0)", color: "#587465" },
      yaxis: { gridcolor: "#e3efe7", backgroundcolor: "rgba(0,0,0,0)", color: "#587465" },
      zaxis: { gridcolor: "#e3efe7", backgroundcolor: "rgba(0,0,0,0)", color: "#587465" },
    },
    hoverlabel: { bgcolor: "#ffffff", bordercolor: "#178a4c", font: { color: "#0a160d" } },
    margin: { t: 30, r: 20, b: 40, l: 50 },
  },
};
