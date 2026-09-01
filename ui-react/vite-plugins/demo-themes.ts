import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

const VIRTUAL_ID = "virtual:demo-themes";
const RESOLVED_VIRTUAL_ID = "\0" + VIRTUAL_ID;

/**
 * Optional local theme overlay for forks: drop extra theme manifests into
 * <repo-root>/demo/themes/<any-name>/theme.json (untracked — the demo/ folder is
 * gitignored and may not exist at all). Each valid manifest found there is merged
 * into the theme picker at dev-server/build time; a missing folder, a missing
 * manifest, a malformed one, or a missing logo file is silently skipped so the
 * built-in themes are never affected. See src/themes/index.ts for the merge point.
 */
const DEMO_THEMES_DIR = path.resolve(fileURLToPath(import.meta.url), "..", "..", "..", "demo", "themes");

type DemoThemeManifest = {
  id: string;
  label: string;
  logo: string;
  cssVars: Record<string, string>;
  categoryPalette: string[];
  plotlyLayout?: Record<string, unknown>;
};

type ResolvedDemoTheme = Omit<DemoThemeManifest, "plotlyLayout"> & { plotlyLayout: Record<string, unknown> };

function isValidManifest(raw: unknown): raw is DemoThemeManifest {
  if (!raw || typeof raw !== "object") return false;
  const m = raw as Record<string, unknown>;
  return (
    typeof m.id === "string" &&
    m.id.length > 0 &&
    typeof m.label === "string" &&
    m.label.length > 0 &&
    typeof m.logo === "string" &&
    m.logo.length > 0 &&
    typeof m.cssVars === "object" &&
    m.cssVars !== null &&
    Array.isArray(m.categoryPalette) &&
    m.categoryPalette.every((c) => typeof c === "string")
  );
}

function readManifest(dir: string): DemoThemeManifest | null {
  const manifestPath = path.join(dir, "theme.json");
  if (!fs.existsSync(manifestPath)) return null;
  try {
    const raw: unknown = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    return isValidManifest(raw) ? raw : null;
  } catch {
    return null;
  }
}

function logoAsDataUri(dir: string, logoRelPath: string): string | null {
  try {
    const logoPath = path.resolve(dir, logoRelPath);
    if (!logoPath.startsWith(dir) || !fs.existsSync(logoPath)) return null;
    const ext = path.extname(logoPath).toLowerCase();
    const data = fs.readFileSync(logoPath);
    if (ext === ".svg") return `data:image/svg+xml,${encodeURIComponent(data.toString("utf-8"))}`;
    if (ext === ".png") return `data:image/png;base64,${data.toString("base64")}`;
    if (ext === ".jpg" || ext === ".jpeg") return `data:image/jpeg;base64,${data.toString("base64")}`;
    return null;
  } catch {
    return null;
  }
}

/**
 * Fills in a plausible plotlyLayout from cssVars when a manifest doesn't supply its
 * own — mirrors the shape every built-in theme (src/themes/*.ts) already uses.
 */
function buildDefaultPlotlyLayout(cssVars: Record<string, string>, categoryPalette: string[]): Record<string, unknown> {
  const text = cssVars["--text"] ?? "#1d242b";
  const textH = cssVars["--text-h"] ?? text;
  const grid = cssVars["--border"] ?? "#cccccc";
  const zeroline = cssVars["--border-strong"] ?? grid;
  const dim = cssVars["--dim"] ?? text;
  const panel2 = cssVars["--panel-2"] ?? cssVars["--panel"] ?? "#ffffff";
  const accent = cssVars["--accent"] ?? categoryPalette[0] ?? "#33c7ff";
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: text, family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif" },
    colorway: categoryPalette,
    xaxis: { gridcolor: grid, zerolinecolor: zeroline, linecolor: grid, color: dim, automargin: true },
    yaxis: { gridcolor: grid, zerolinecolor: zeroline, linecolor: grid, color: dim, automargin: true },
    scene: {
      xaxis: { gridcolor: grid, backgroundcolor: "rgba(0,0,0,0)", color: dim },
      yaxis: { gridcolor: grid, backgroundcolor: "rgba(0,0,0,0)", color: dim },
      zaxis: { gridcolor: grid, backgroundcolor: "rgba(0,0,0,0)", color: dim },
    },
    hoverlabel: { bgcolor: panel2, bordercolor: accent, font: { color: textH } },
    margin: { t: 30, r: 20, b: 40, l: 50 },
  };
}

function loadDemoThemes(): ResolvedDemoTheme[] {
  if (!fs.existsSync(DEMO_THEMES_DIR)) return [];
  const seenIds = new Set<string>();
  const themes: ResolvedDemoTheme[] = [];
  for (const entry of fs.readdirSync(DEMO_THEMES_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const dir = path.join(DEMO_THEMES_DIR, entry.name);
    const manifest = readManifest(dir);
    if (!manifest || seenIds.has(manifest.id)) continue;
    const logo = logoAsDataUri(dir, manifest.logo);
    if (!logo) continue;
    seenIds.add(manifest.id);
    themes.push({
      ...manifest,
      logo,
      plotlyLayout: manifest.plotlyLayout ?? buildDefaultPlotlyLayout(manifest.cssVars, manifest.categoryPalette),
    });
  }
  return themes;
}

/**
 * Resolves `import "virtual:demo-themes"` to a generated module exporting whatever
 * was found under demo/themes/ at that moment — an empty array when there was
 * nothing usable there.
 */
export function demoThemesPlugin(): Plugin {
  return {
    name: "demo-themes",
    resolveId(id) {
      if (id === VIRTUAL_ID) return RESOLVED_VIRTUAL_ID;
    },
    load(id) {
      if (id !== RESOLVED_VIRTUAL_ID) return;
      let themes: ResolvedDemoTheme[] = [];
      try {
        themes = loadDemoThemes();
      } catch {
        themes = [];
      }
      return `export const DEMO_THEMES = ${JSON.stringify(themes)};\n`;
    },
  };
}
