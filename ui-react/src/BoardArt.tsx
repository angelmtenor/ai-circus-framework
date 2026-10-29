import { useId } from "react";

/**
 * Procedural product drawings for the surf_retail web shop and catalogue: every board is drawn
 * from its own attributes (shape, colour, length) — no product photos, so nothing real and
 * nothing copyrighted. Boards are drawn nose-up; accessories get a simple, recognisable icon.
 */

type Outline = { width: number; nose: number; tail: number; widest: number; tailKind: "squash" | "swallow" | "pin" | "square" | "round" };

// width = max width / length; nose and tail = width there as a share of the max width.
const OUTLINES: Record<string, Outline> = {
  shortboard: { width: 0.27, nose: 0.04, tail: 0.5, widest: 0.48, tailKind: "squash" },
  fish: { width: 0.33, nose: 0.2, tail: 0.72, widest: 0.52, tailKind: "swallow" },
  hybrid: { width: 0.31, nose: 0.28, tail: 0.6, widest: 0.5, tailKind: "squash" },
  midlength: { width: 0.27, nose: 0.22, tail: 0.38, widest: 0.47, tailKind: "pin" },
  longboard: { width: 0.24, nose: 0.62, tail: 0.6, widest: 0.48, tailKind: "square" },
  softtop: { width: 0.29, nose: 0.6, tail: 0.7, widest: 0.5, tailKind: "round" },
  sup: { width: 0.24, nose: 0.42, tail: 0.66, widest: 0.5, tailKind: "square" },
  isup: { width: 0.26, nose: 0.55, tail: 0.75, widest: 0.5, tailKind: "square" },
  kitesurf: { width: 0.3, nose: 0.12, tail: 0.55, widest: 0.5, tailKind: "squash" },
};
const FINS: Record<string, number[]> = {
  shortboard: [-0.32, 0, 0.32],
  fish: [-0.38, 0.38],
  hybrid: [-0.32, 0, 0.32],
  midlength: [0],
  longboard: [0],
  softtop: [-0.3, 0, 0.3],
  sup: [0],
  isup: [0],
  kitesurf: [-0.32, 0, 0.32],
};

function shade(hex: string, amount: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  if (Number.isNaN(n)) return hex;
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v + amount * 255)));
  return `#${[(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => c(v).toString(16).padStart(2, "0")).join("")}`;
}

/** Half-width (as a share of the max) at t ∈ [0, 1] from nose to tail. */
function profile(o: Outline, t: number): number {
  if (t <= o.widest) {
    const u = t / o.widest;
    return o.nose + (1 - o.nose) * Math.pow(Math.sin((Math.PI / 2) * u), o.nose > 0.4 ? 0.45 : 0.75);
  }
  const u = (t - o.widest) / (1 - o.widest);
  return o.tail + (1 - o.tail) * Math.pow(Math.cos((Math.PI / 2) * u), 1.2);
}

function boardPath(o: Outline, cx: number, top: number, length: number): string {
  const half = (o.width * length) / 2;
  const steps = 48;
  const right: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    right.push([cx + half * profile(o, t), top + t * length]);
  }
  const tailY = top + length;
  const tailHalf = half * o.tail;
  let tail: string;
  if (o.tailKind === "swallow") {
    tail = `L ${cx + tailHalf * 0.95} ${tailY} L ${cx} ${tailY - length * 0.07} L ${cx - tailHalf * 0.95} ${tailY}`;
  } else if (o.tailKind === "pin") {
    tail = `Q ${cx} ${tailY + length * 0.03} ${cx - tailHalf} ${tailY}`;
  } else if (o.tailKind === "round") {
    tail = `Q ${cx} ${tailY + length * 0.05} ${cx - tailHalf} ${tailY}`;
  } else {
    tail = `L ${cx - tailHalf} ${tailY}`;
  }
  const r = right.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L ");
  const l = [...right]
    .reverse()
    .map(([x, y]) => `${(2 * cx - x).toFixed(1)} ${y.toFixed(1)}`)
    .join(" L ");
  return `M ${cx} ${top} L ${r} ${tail} L ${l} Z`;
}

function Board({ shape, colour, lengthIn, brand, uid }: { shape: string; colour: string; lengthIn: number | null; brand: string; uid: string }) {
  const o = OUTLINES[shape] ?? OUTLINES.hybrid;
  // Longer boards are drawn longer (a 5'6" fish next to a 9'6" log reads right in a grid).
  const span = shape === "sup" || shape === "isup" ? [100, 170] : [60, 120];
  const length = 170 + 120 * Math.max(0, Math.min(1, ((lengthIn ?? 80) - span[0]) / (span[1] - span[0])));
  const top = (320 - length) / 2;
  const d = boardPath(o, 60, top, length);
  const soft = shape === "softtop" || shape === "isup";
  const rail = shade(colour, -0.22);
  return (
    <>
      <defs>
        <linearGradient id={`deck-${uid}`} x1="0" x2="1">
          <stop offset="0" stopColor={shade(colour, -0.12)} />
          <stop offset="0.45" stopColor={shade(colour, 0.12)} />
          <stop offset="1" stopColor={shade(colour, -0.08)} />
        </linearGradient>
        <clipPath id={`clip-${uid}`}>
          <path d={d} />
        </clipPath>
      </defs>
      <ellipse cx={62} cy={top + length + 10} rx={o.width * length * 0.42} ry={4} className="board-art-shadow" />
      <path d={d} fill={`url(#deck-${uid})`} stroke={rail} strokeWidth={2} />
      <g clipPath={`url(#clip-${uid})`}>
        {soft ? (
          Array.from({ length: 9 }, (_, i) => (
            <line key={i} x1={0} x2={120} y1={top + (length * (i + 1)) / 10} y2={top + (length * (i + 1)) / 10} stroke={shade(colour, -0.1)} strokeWidth={1} opacity={0.5} />
          ))
        ) : (
          <line x1={60} x2={60} y1={top} y2={top + length} stroke={shade(colour, -0.35)} strokeWidth={1} opacity={0.7} />
        )}
        <rect x={0} y={top + length * 0.62} width={120} height={length * 0.05} fill={shade(colour, 0.25)} opacity={0.55} />
        {shape === "sup" || shape === "isup" ? (
          <rect x={60 - o.width * length * 0.28} y={top + length * 0.38} width={o.width * length * 0.56} height={length * 0.36} rx={4} fill="#1b263b" opacity={0.35} />
        ) : (
          (shape === "shortboard" || shape === "fish" || shape === "kitesurf" || shape === "hybrid") && (
            <rect x={60 - o.width * length * 0.32} y={top + length * 0.8} width={o.width * length * 0.64} height={length * 0.16} rx={5} fill="#1b263b" opacity={0.4} />
          )
        )}
      </g>
      {(FINS[shape] ?? []).map((f, i) => (
        <path key={i} d={`M ${60 + f * o.width * length * 0.5 * o.tail * 1.6} ${top + length * 0.9} l -3 9 l 6 0 z`} fill={rail} opacity={0.8} />
      ))}
      <text x={60} y={top + length * 0.2} className="board-art-brand" transform={`rotate(-90 60 ${top + length * 0.2})`}>
        {brand.split(" ")[0].toUpperCase()}
      </text>
    </>
  );
}

function TwinTip({ colour }: { colour: string }) {
  return (
    <>
      <rect x={24} y={60} width={72} height={200} rx={34} fill={colour} stroke={shade(colour, -0.25)} strokeWidth={2} />
      <rect x={34} y={95} width={52} height={22} rx={6} fill="#1b263b" opacity={0.75} />
      <rect x={34} y={203} width={52} height={22} rx={6} fill="#1b263b" opacity={0.75} />
      <path d="M 60 70 L 60 250" stroke={shade(colour, 0.3)} strokeWidth={6} opacity={0.5} />
    </>
  );
}

function Kite({ colour }: { colour: string }) {
  return (
    <>
      <path d="M 10 150 Q 60 40 110 150 Q 60 105 10 150 Z" fill={colour} stroke={shade(colour, -0.3)} strokeWidth={2} />
      {[28, 44, 60, 76, 92].map((x) => (
        <line key={x} x1={x} y1={x === 60 ? 76 : 95} x2={60 + (x - 60) * 0.55} y2={x === 60 ? 121 : 128} stroke={shade(colour, -0.35)} strokeWidth={1.5} />
      ))}
      {[14, 40, 80, 106].map((x) => (
        <line key={x} x1={x} y1={148} x2={60} y2={290} stroke="currentColor" strokeWidth={0.6} opacity={0.5} />
      ))}
      <rect x={44} y={286} width={32} height={5} rx={2} fill="#1b263b" />
    </>
  );
}

function Accessory({ shape, colour }: { shape: string; colour: string }) {
  const dark = "#1b263b";
  switch (shape) {
    case "leash":
      return (
        <>
          <path d="M 60 70 C 105 90, 15 120, 60 140 C 105 160, 15 190, 60 210" fill="none" stroke={dark} strokeWidth={5} strokeLinecap="round" />
          <rect x={38} y={208} width={44} height={26} rx={10} fill={colour} stroke={dark} strokeWidth={2} />
          <circle cx={60} cy={64} r={8} fill="none" stroke={dark} strokeWidth={3} />
        </>
      );
    case "fins":
      return (
        <>
          {[-34, 0, 34].map((dx) => (
            <path key={dx} d={`M ${60 + dx - 14} 210 Q ${60 + dx - 8} 140 ${60 + dx + 16} 110 Q ${60 + dx + 8} 160 ${60 + dx + 14} 210 Z`} fill={colour} stroke={shade(colour, -0.3)} strokeWidth={2} />
          ))}
        </>
      );
    case "pad":
      return (
        <>
          <path d="M 22 120 L 98 120 L 98 214 Q 98 236 80 236 L 40 236 Q 22 236 22 214 Z" fill={dark} />
          <path d="M 22 120 L 98 120 L 96 104 Q 60 92 24 104 Z" fill={colour} />
          {[140, 160, 180, 200, 220].map((y) => (
            <line key={y} x1={30} x2={90} y1={y} y2={y} stroke={colour} strokeWidth={2} opacity={0.6} />
          ))}
        </>
      );
    case "wax":
      return (
        <>
          <ellipse cx={60} cy={186} rx={42} ry={14} fill={shade(colour, -0.2)} />
          <rect x={18} y={150} width={84} height={36} fill={colour} />
          <ellipse cx={60} cy={150} rx={42} ry={14} fill={shade(colour, 0.15)} />
          <text x={60} y={174} className="board-art-brand">WAX</text>
        </>
      );
    case "bag":
      return (
        <>
          <rect x={30} y={30} width={60} height={260} rx={30} fill={dark} />
          <path d="M 40 50 L 40 270" stroke={colour} strokeWidth={4} strokeDasharray="6 4" />
          <rect x={48} y={130} width={24} height={60} rx={6} fill={colour} opacity={0.85} />
        </>
      );
    case "paddle":
      return (
        <>
          <rect x={57} y={30} width={6} height={210} rx={3} fill={dark} />
          <rect x={44} y={26} width={32} height={10} rx={5} fill={dark} />
          <path d="M 46 236 L 74 236 L 78 294 Q 60 302 42 294 Z" fill={colour} stroke={shade(colour, -0.3)} strokeWidth={2} />
        </>
      );
    case "pump":
      return (
        <>
          <rect x={44} y={110} width={32} height={140} rx={8} fill={colour} stroke={shade(colour, -0.3)} strokeWidth={2} />
          <rect x={57} y={70} width={6} height={42} fill={dark} />
          <rect x={30} y={62} width={60} height={12} rx={6} fill={dark} />
          <rect x={34} y={248} width={52} height={10} rx={3} fill={dark} />
        </>
      );
    case "rack":
      return (
        <>
          <rect x={14} y={120} width={92} height={18} rx={9} fill={dark} />
          <rect x={14} y={190} width={92} height={18} rx={9} fill={dark} />
          <path d="M 30 100 L 30 230 M 90 100 L 90 230" stroke={colour} strokeWidth={6} strokeLinecap="round" />
        </>
      );
    case "bar":
      return (
        <>
          <rect x={14} y={140} width={92} height={12} rx={6} fill={colour} stroke={dark} strokeWidth={2} />
          <line x1={60} y1={40} x2={60} y2={250} stroke={dark} strokeWidth={2} />
          <line x1={16} y1={146} x2={50} y2={40} stroke={dark} strokeWidth={1} opacity={0.6} />
          <line x1={104} y1={146} x2={70} y2={40} stroke={dark} strokeWidth={1} opacity={0.6} />
          <path d="M 52 250 Q 60 270 68 250" fill="none" stroke={colour} strokeWidth={5} />
        </>
      );
    case "harness":
      return (
        <>
          <path d="M 16 140 Q 60 100 104 140 L 104 200 Q 60 170 16 200 Z" fill={dark} />
          <path d="M 22 150 Q 60 120 98 150" fill="none" stroke={colour} strokeWidth={4} />
          <path d="M 48 168 L 72 168 L 72 186 Q 60 196 48 186 Z" fill={colour} />
        </>
      );
    default:
      return <circle cx={60} cy={160} r={40} fill={colour} />;
  }
}

/** One product's drawing; `shape` comes from the catalogue (shortboard, fish, sup, kite, leash…). */
export function BoardArt({
  shape,
  colour,
  lengthIn,
  brand = "",
  className,
  title,
}: {
  shape: string;
  colour: string;
  lengthIn?: number | null;
  brand?: string;
  className?: string;
  title?: string;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const safeColour = /^#[0-9a-fA-F]{6}$/.test(colour) ? colour : "#2bb3c0";
  let body;
  if (shape in OUTLINES) body = <Board shape={shape} colour={safeColour} lengthIn={lengthIn ?? null} brand={brand} uid={uid} />;
  else if (shape === "twintip") body = <TwinTip colour={safeColour} />;
  else if (shape === "kite") body = <Kite colour={safeColour} />;
  else body = <Accessory shape={shape} colour={safeColour} />;
  return (
    <svg viewBox="0 0 120 320" className={`board-art ${className ?? ""}`} role="img" aria-label={title ?? shape}>
      {title && <title>{title}</title>}
      {body}
    </svg>
  );
}
