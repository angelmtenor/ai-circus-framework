import logo from "../assets/logo.svg";
import type { Theme } from "./types";

/**
 * Default theme: dark navy surfaces, glowing cyan/purple accents — matches
 * assets/logo.svg. Every other theme (see themes/index.ts) supplies the same
 * cssVars/categoryPalette/plotlyLayout shape; this file has no special status
 * beyond being first/default.
 */
export const tronTheme: Theme = {
  id: "tron",
  label: "Tron",
  logo,
  cssVars: {
    "--bg": "#05070f",
    "--panel": "#0b1224",
    "--panel-2": "#101b36",
    "--panel-3": "#152244",
    "--border": "#1c3a5e",
    "--border-strong": "#2c5a8a",
    "--text": "#dcecf7",
    "--text-h": "#f3fbff",
    "--dim": "#7fa3c9",
    "--code-bg": "#0f1830",
    "--accent": "#33c7ff",
    "--accent-2": "#7cf9ff",
    "--accent-soft": "rgba(51, 199, 255, 0.12)",
    "--accent-bg": "rgba(51, 199, 255, 0.1)",
    "--accent-border": "rgba(51, 199, 255, 0.5)",
    "--purple": "#aa3bff",
    "--purple-soft": "rgba(170, 59, 255, 0.14)",
    "--green": "#2de8a0",
    "--blue": "#33c7ff",
    "--amber": "#ffcf4d",
    "--red": "#ff5f7a",
    "--teal": "#22d3c8",
    "--pink": "#ff8fd6",
    "--lime": "#b6ff5c",
    "--social-bg": "rgba(15, 24, 48, 0.6)",
    "--glow-accent": "0 0 12px rgba(51, 199, 255, 0.55)",
    "--glow-accent-soft": "0 0 6px rgba(51, 199, 255, 0.35)",
    "--shadow": "rgba(0, 0, 0, 0.55) 0 10px 20px -3px, rgba(51, 199, 255, 0.06) 0 0 0 1px",
    "--shadow-sm": "0 1px 2px rgba(0, 0, 0, 0.45)",
    "--shadow-md": "0 8px 24px rgba(0, 0, 0, 0.55), 0 0 0 1px rgba(51, 199, 255, 0.08)",
  },
  categoryPalette: ["#33c7ff", "#aa3bff", "#ffcf4d", "#2de8a0", "#ff5f7a", "#22d3c8", "#ff8fd6", "#b6ff5c"],
  plotlyLayout: {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#dcecf7", family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif" },
    colorway: ["#33c7ff", "#aa3bff", "#ffcf4d", "#2de8a0", "#ff5f7a", "#22d3c8", "#ff8fd6", "#b6ff5c"],
    xaxis: { gridcolor: "#1c3a5e", zerolinecolor: "#2c5a8a", linecolor: "#1c3a5e", color: "#7fa3c9" },
    yaxis: { gridcolor: "#1c3a5e", zerolinecolor: "#2c5a8a", linecolor: "#1c3a5e", color: "#7fa3c9" },
    scene: {
      xaxis: { gridcolor: "#1c3a5e", backgroundcolor: "rgba(0,0,0,0)", color: "#7fa3c9" },
      yaxis: { gridcolor: "#1c3a5e", backgroundcolor: "rgba(0,0,0,0)", color: "#7fa3c9" },
      zaxis: { gridcolor: "#1c3a5e", backgroundcolor: "rgba(0,0,0,0)", color: "#7fa3c9" },
    },
    hoverlabel: { bgcolor: "#101b36", bordercolor: "#33c7ff", font: { color: "#f3fbff" } },
    margin: { t: 30, r: 20, b: 40, l: 50 },
  },
};
