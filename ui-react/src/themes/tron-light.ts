import logo from "../assets/logo-tron-light.svg";
import type { Theme } from "./types";

/**
 * White-background counterpart to tron.ts: same blue/cyan hue family, flat corporate
 * surfaces instead of neon glow — a light option for the same default branding rather
 * than a separate skin.
 */
export const tronLightTheme: Theme = {
  id: "tron-light",
  label: "White Tron",
  logo,
  cssVars: {
    "--bg": "#ffffff",
    "--panel": "#ffffff",
    "--panel-2": "#f4f8fc",
    "--panel-3": "#eaf5ff",
    "--border": "#d7e3ee",
    "--border-strong": "#a9c3db",
    "--text": "#16202b",
    "--text-h": "#0a1520",
    "--dim": "#5b7086",
    "--code-bg": "#eef3f8",
    "--accent": "#0072e0",
    "--accent-2": "#33c7ff",
    "--accent-soft": "rgba(0, 114, 224, 0.1)",
    "--accent-bg": "rgba(0, 114, 224, 0.08)",
    "--accent-border": "rgba(0, 114, 224, 0.45)",
    "--purple": "#6c5ce7",
    "--purple-soft": "rgba(108, 92, 231, 0.12)",
    "--green": "#17b06b",
    "--blue": "#0072e0",
    "--amber": "#c98a00",
    "--red": "#e0355f",
    "--teal": "#0fa3a3",
    "--pink": "#c2437b",
    "--lime": "#6fcf3d",
    "--social-bg": "rgba(22, 32, 43, 0.05)",
    // Flat corporate look, on purpose — no neon glow (unlike Tron's dark default theme).
    "--glow-accent": "none",
    "--glow-accent-soft": "none",
    "--shadow": "rgba(16, 24, 40, 0.08) 0 10px 20px -3px, rgba(16, 24, 40, 0.04) 0 0 0 1px",
    "--shadow-sm": "0 1px 2px rgba(16, 24, 40, 0.06)",
    "--shadow-md": "0 8px 24px rgba(16, 24, 40, 0.1), 0 0 0 1px rgba(16, 24, 40, 0.04)",
    // Not a custom property — CSSStyleDeclaration.setProperty accepts any valid CSS
    // property name, and useTheme.ts's applyTheme loops over every key here the same
    // way — so this flips native form-control rendering (selects, checkboxes,
    // scrollbars) to their light variant instead of the dark one index.css defaults to.
    "color-scheme": "light",
  },
  categoryPalette: ["#0072e0", "#33c7ff", "#6c5ce7", "#e0355f", "#0fa3a3", "#c98a00", "#c2437b", "#17b06b"],
  plotlyLayout: {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#16202b", family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif" },
    colorway: ["#0072e0", "#33c7ff", "#6c5ce7", "#e0355f", "#0fa3a3", "#c98a00", "#c2437b", "#17b06b"],
    xaxis: { gridcolor: "#e3e7ea", zerolinecolor: "#c6c6c6", linecolor: "#d7e3ee", color: "#5b7086", automargin: true },
    yaxis: { gridcolor: "#e3e7ea", zerolinecolor: "#c6c6c6", linecolor: "#d7e3ee", color: "#5b7086", automargin: true },
    scene: {
      xaxis: { gridcolor: "#e3e7ea", backgroundcolor: "rgba(0,0,0,0)", color: "#5b7086" },
      yaxis: { gridcolor: "#e3e7ea", backgroundcolor: "rgba(0,0,0,0)", color: "#5b7086" },
      zaxis: { gridcolor: "#e3e7ea", backgroundcolor: "rgba(0,0,0,0)", color: "#5b7086" },
    },
    hoverlabel: { bgcolor: "#ffffff", bordercolor: "#0072e0", font: { color: "#0a1520" } },
    margin: { t: 30, r: 20, b: 40, l: 50 },
  },
};
