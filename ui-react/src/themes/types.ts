/**
 * A theme supplies colors + a logo only — never layout or component structure (see
 * Settings.tsx's "Appearance" picker, the only place a user chooses between these).
 */
export type Theme = {
  id: string;
  label: string;
  logo: string;
  cssVars: Record<string, string>;
  categoryPalette: string[];
  // Loosely typed on purpose: keeps this module free of a plotly.js dependency.
  // PlotlyChart.tsx merges this into its own typed Partial<Layout>.
  plotlyLayout: Record<string, unknown>;
};
