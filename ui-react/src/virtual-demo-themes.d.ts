// See vite-plugins/demo-themes.ts — resolves to whatever theme manifests were
// found under demo/themes/ at dev-server/build time, or an empty array.
declare module "virtual:demo-themes" {
  import type { Theme } from "./themes/types";
  export const DEMO_THEMES: Theme[];
}
