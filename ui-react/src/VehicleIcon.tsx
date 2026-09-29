import { GLYPH_PATHS, type Glyph } from "./logisticsViz";

/** A vehicle glyph (see logisticsViz.ts) as inline SVG, in the current text colour. */
export function VehicleIcon({ glyph, size = 16, className, title }: { glyph: Glyph; size?: number; className?: string; title?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      <path d={GLYPH_PATHS[glyph]} fillRule="evenodd" />
    </svg>
  );
}
