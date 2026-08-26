/**
 * Chrome-only icon set — nav tabs, dock controls, settings — as inline SVG paths on
 * `stroke="currentColor"`, so each icon inherits whatever the active theme's text/
 * accent color resolves to (see themes/) with zero icon-specific theming code.
 *
 * Deliberately NOT used for per-scenario icons (scenario.icon, from scenario.yaml —
 * see ScenarioPicker.tsx) or the chat hero icon: those are content, not app chrome,
 * and stay as their authored emoji regardless of theme.
 */

const PATHS: Record<string, string> = {
  data: "M4 6.5c0-1.38 3.58-2.5 8-2.5s8 1.12 8 2.5-3.58 2.5-8 2.5-8-1.12-8-2.5Zm0 0V17.5c0 1.38 3.58 2.5 8 2.5s8-1.12 8-2.5V6.5M4 12c0 1.38 3.58 2.5 8 2.5s8-1.12 8-2.5",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-4.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9ZM13.5 12A1.5 1.5 0 1 1 12 10.5",
  scan: "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16",
  chat: "M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9l-4 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 1-.1 1.2l2.1 1.6-2 3.4-2.5-1a7.6 7.6 0 0 1-2 1.2l-.4 2.6h-4l-.4-2.6a7.6 7.6 0 0 1-2-1.2l-2.5 1-2-3.4L3.7 13a7.4 7.4 0 0 1 0-2.4L1.6 9l2-3.4 2.5 1c.6-.5 1.3-.9 2-1.2L8.5 3h4l.4 2.6c.7.3 1.4.7 2 1.2l2.5-1 2 3.4-2.1 1.6c.1.4.1.8.1 1.2Z",
  close: "M6 6l12 12M18 6L6 18",
  maximize: "M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3m11-5v3a2 2 0 0 1-2 2h-3",
  restore: "M9 9V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-4M3 11a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  back: "M19 12H5m6-7-7 7 7 7",
  download: "M12 4v11m0 0-4-4m4 4 4-4M5 19h14",
  plus: "M12 5v14M5 12h14",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v.01M12 11v6",
};

export function Icon({ name, size = 16, className }: { name: keyof typeof PATHS; size?: number; className?: string }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path d={d} stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
