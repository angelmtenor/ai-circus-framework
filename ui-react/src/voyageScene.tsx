/**
 * The `ocean_liner` scene for VoyageView.tsx: a hand-drawn SVG cutaway of a four-funnel
 * liner at night (hull, superstructure, funnels, masts, lifeboats, decks A–G, boiler
 * rooms, sea, iceberg, stars) plus the geometry that berths N people into its decks.
 * Pure presentation + math — no data fetching, no scenario-specific column names.
 *
 * Coordinates are in the SVG's own viewBox (SCENE_W x SCENE_H); the scene has its own
 * fixed night palette (it is an illustration, like an image), so it reads the same in
 * every app theme.
 */

import { memo } from "react";
import type { Point, SceneModule } from "./scenes";

export const SCENE_W = 1200;
export const SCENE_H = 520;
export const WATERLINE = 384;

type Rect = { x0: number; x1: number; y0: number; y1: number };
export type { Point };

// Deck bands (top, bottom) — A/B in the superstructure, C–G inside the hull.
const DECKS: Record<string, [number, number]> = {
  A: [194, 220],
  B: [220, 246],
  C: [246, 272],
  D: [272, 298],
  E: [298, 324],
  F: [324, 350],
  G: [350, 376],
};

function berth(deck: string, x0: number, x1: number): Rect {
  const [top, bottom] = DECKS[deck];
  return { x0, x1, y0: top + 2, y1: bottom - 2 };
}

// Where each class slept, loosely after the real ship: 1st class high up amidships,
// 2nd class aft, 3rd class (steerage) low down at the bow and the stern.
const THREE_ZONE_BERTHS: Rect[][] = [
  [berth("A", 300, 897), berth("B", 300, 897), berth("C", 470, 860)],
  [berth("C", 96, 465), berth("D", 100, 520), berth("E", 106, 470)],
  [
    berth("C", 960, 1128),
    berth("D", 800, 1122),
    berth("E", 760, 1118),
    berth("F", 790, 1114),
    berth("G", 790, 1110),
    berth("F", 112, 470),
    berth("G", 122, 470),
    berth("E", 470, 760),
  ],
];

// Any other zone count: every full-length deck, top to bottom, split into contiguous runs.
const ALL_DECKS: Rect[] = [
  berth("A", 300, 897),
  berth("B", 300, 897),
  berth("C", 96, 1126),
  berth("D", 100, 1122),
  berth("E", 106, 1118),
  berth("F", 112, 1114),
  berth("G", 122, 1110),
];

function zoneRects(zoneCount: number): Rect[][] {
  if (zoneCount === 3) return THREE_ZONE_BERTHS;
  return Array.from({ length: zoneCount }, (_, z) => {
    const start = Math.floor((z * ALL_DECKS.length) / zoneCount);
    const end = Math.max(start + 1, Math.floor(((z + 1) * ALL_DECKS.length) / zoneCount));
    return ALL_DECKS.slice(start, end);
  });
}

function cells(rects: Rect[], pitch: number): Point[] {
  const out: Point[] = [];
  for (const r of rects) {
    const cols = Math.floor((r.x1 - r.x0) / pitch);
    const rows = Math.max(1, Math.floor((r.y1 - r.y0) / pitch));
    const offsetX = r.x0 + ((r.x1 - r.x0) - cols * pitch) / 2 + pitch / 2;
    const offsetY = r.y0 + ((r.y1 - r.y0) - rows * pitch) / 2 + pitch / 2;
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) out.push({ x: offsetX + col * pitch, y: offsetY + row * pitch });
    }
  }
  return out;
}

/**
 * Berth `counts[z]` people (+1 spare slot each, for a what-if passenger) into zone z,
 * all at ONE shared pitch — one dot is one person everywhere, so a roomy zone visibly
 * has space to spare. Returns each zone's slots in fill order and the dot radius.
 */
export function packZones(counts: number[]): { slots: Point[][]; radius: number } {
  const rects = zoneRects(counts.length);
  let pitch = 12;
  while (pitch > 3 && !rects.every((r, z) => cells(r, pitch).length >= counts[z] + 1)) pitch -= 0.25;
  return { slots: rects.map((r) => cells(r, pitch)), radius: pitch * 0.36 };
}

// Deterministic starfield (no Math.random: identical on every render).
const STARS: { x: number; y: number; r: number; twinkle: boolean }[] = (() => {
  let seed = 7;
  const next = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  return Array.from({ length: 140 }, () => ({
    x: next() * SCENE_W,
    y: next() * 330,
    r: 0.4 + next() * 1.1,
    twinkle: next() > 0.8,
  }));
})();

// Warm light streaks under the hull, thinning with depth (deterministic, like the stars).
const REFLECTIONS: { x: number; y: number; w: number; o: number }[] = (() => {
  let seed = 11;
  const next = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  return Array.from({ length: 70 }, () => {
    const depth = next();
    return { x: 140 + next() * 940, y: WATERLINE + 6 + depth * 90, w: 8 + next() * 40 * (1 - depth), o: 0.08 + (1 - depth) * 0.22 };
  });
})();

const HULL = "M 62 246 L 1136 246 L 1108 440 L 160 440 Q 110 436 96 400 Q 72 330 62 246 Z";
const FUNNEL_X = [418, 526, 634, 742];
const LIFEBOATS_X = [334, 356, 378, 400, 790, 812, 834, 856];

/** Everything behind the passengers: sky, stars, ship, deck lines, machinery. */
export const SceneBackdrop = memo(function SceneBackdrop() {
  return (
    <g aria-hidden="true">
      <defs>
        <linearGradient id="voy-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#02050d" />
          <stop offset="0.7" stopColor="#0b1626" />
          <stop offset="1" stopColor="#13243a" />
        </linearGradient>
        <linearGradient id="voy-sea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0d2742" stopOpacity="0.9" />
          <stop offset="1" stopColor="#01060d" stopOpacity="0.98" />
        </linearGradient>
        <linearGradient id="voy-ice" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#f4fbff" />
          <stop offset="1" stopColor="#8fc4de" />
        </linearGradient>
        <radialGradient id="voy-boiler" cx="0.5" cy="0.6" r="0.6">
          <stop offset="0" stopColor="#ff9b3d" stopOpacity="0.55" />
          <stop offset="1" stopColor="#ff9b3d" stopOpacity="0" />
        </radialGradient>
        <clipPath id="voy-hull-clip">
          <path d={HULL} />
        </clipPath>
        <radialGradient id="voy-galaxy" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#9fb4ff" stopOpacity="0.16" />
          <stop offset="1" stopColor="#9fb4ff" stopOpacity="0" />
        </radialGradient>
        <filter id="voy-soft" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="7" />
        </filter>
      </defs>

      <rect width={SCENE_W} height={SCENE_H} fill="url(#voy-sky)" />
      <ellipse cx={640} cy={120} rx={620} ry={70} fill="url(#voy-galaxy)" transform="rotate(-12 640 120)" />
      {STARS.map((s, i) => (
        <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#dfe9ff" className={s.twinkle ? "voy-star--twinkle" : undefined} opacity={0.75} />
      ))}

      {/* Masts and the wireless aerial strung between them. */}
      <line x1={196} y1={246} x2={196} y2={70} stroke="#8793a8" strokeWidth={2.2} />
      <line x1={1000} y1={246} x2={1000} y2={58} stroke="#8793a8" strokeWidth={2.2} />
      <path d="M 196 72 Q 598 92 1000 60" stroke="#6b778c" strokeWidth={0.8} fill="none" />
      <line x1={196} y1={90} x2={292} y2={194} stroke="#4c5669" strokeWidth={0.7} />
      <line x1={1000} y1={78} x2={1136} y2={246} stroke="#4c5669" strokeWidth={0.7} />
      <line x1={1000} y1={78} x2={905} y2={194} stroke="#4c5669" strokeWidth={0.7} />

      {/* Steam from the three working funnels (the fourth was a ventilation dummy). */}
      <g filter="url(#voy-soft)" opacity={0.5}>
        {FUNNEL_X.slice(0, 3).map((x, i) => (
          <g key={x} className="voy-steam" style={{ animationDelay: `${i * 1.3}s` }}>
            <circle cx={x - 26} cy={70} r={11} fill="#8e9aae" />
            <circle cx={x - 44} cy={56} r={15} fill="#7b879b" />
            <circle cx={x - 68} cy={46} r={19} fill="#687388" />
          </g>
        ))}
      </g>

      {/* Funnels (buff, black-topped, raked aft). */}
      {FUNNEL_X.map((x) => (
        <g key={x}>
          <path d={`M ${x - 15} 184 L ${x - 25} 78 L ${x + 5} 78 L ${x + 15} 184 Z`} fill="#c8913a" stroke="#6e4a14" strokeWidth={1} />
          <path d={`M ${x - 24} 90 L ${x - 25} 78 L ${x + 5} 78 L ${x + 6} 90 Z`} fill="#141414" />
        </g>
      ))}

      {/* Superstructure (A/B decks) + boat-deck house + bridge. */}
      <rect x={292} y={194} width={613} height={52} fill="#1a2334" stroke="#e9eef6" strokeWidth={2} />
      <rect x={330} y={184} width={530} height={10} fill="#d9dee7" />
      <rect x={880} y={176} width={38} height={18} fill="#d9dee7" />
      {LIFEBOATS_X.map((x) => (
        <rect key={x} x={x} y={177} width={18} height={7} rx={3.5} fill="#efe7d2" stroke="#9c9480" strokeWidth={0.6} />
      ))}

      {/* Hull cutaway: interior, deck lines, boiler rooms; then the livery outline. */}
      <path d={HULL} fill="#111a28" />
      <g clipPath="url(#voy-hull-clip)">
        {[272, 298, 324, 350, 376].map((y) => (
          <line key={y} x1={60} x2={1140} y1={y} y2={y} stroke="#26344a" strokeWidth={1} />
        ))}
        {[492, 548, 604, 660, 716, 772].map((x) => (
          <g key={x}>
            <rect x={x - 22} y={330} width={44} height={100} fill="#1b1410" stroke="#3a2a1c" strokeWidth={1} />
            <rect x={x - 30} y={320} width={60} height={120} fill="url(#voy-boiler)" className="voy-boiler" />
          </g>
        ))}
        <rect x={60} y={WATERLINE} width={1100} height={60} fill="#7a1d22" opacity={0.55} />
      </g>
      <path d={HULL} fill="none" stroke="#e9eef6" strokeWidth={2.2} />
      <line x1={62} y1={246} x2={1136} y2={246} stroke="#c8913a" strokeWidth={1.4} />
      {/* Poop deck (stern) and forecastle (bow). */}
      <path d="M 62 246 L 62 236 L 250 236 L 250 246" fill="#1a2334" stroke="#e9eef6" strokeWidth={1.6} />
      <path d="M 960 246 L 960 234 L 1138 232 L 1136 246" fill="#1a2334" stroke="#e9eef6" strokeWidth={1.6} />

      {Object.entries(DECKS).map(([deck, [top, bottom]]) => (
        <text key={deck} x={deck === "A" || deck === "B" ? 286 : 50} y={(top + bottom) / 2 + 3} className="voy-deck-letter" textAnchor="end">
          {deck}
        </text>
      ))}
      <text x={630} y={420} className="voy-room-label" textAnchor="middle">
        boiler rooms
      </text>
    </g>
  );
});

/** Everything in front of the passengers: the sea (hull below it stays faintly
 * visible), animated swell, and the iceberg off the bow. */
export const SceneForeground = memo(function SceneForeground({ nameplate }: { nameplate: string }) {
  return (
    <g aria-hidden="true" pointerEvents="none">
      <polygon points="1150,384 1158,340 1170,318 1178,290 1186,276 1194,300 1200,292 1200,384" fill="url(#voy-ice)" />
      <polygon points="1172,292 1178,290 1186,276 1190,300 1180,330" fill="#ffffff" opacity={0.55} />
      <polygon points="1140,384 1200,384 1200,560 1150,540 1128,470" fill="#8fc4de" opacity={0.26} />
      <rect x={0} y={WATERLINE} width={SCENE_W} height={SCENE_H - WATERLINE} fill="url(#voy-sea)" />
      {/* The lit ship's reflection on a flat-calm sea. */}
      <g className="voy-reflection">
        {REFLECTIONS.map((r, i) => (
          <rect key={i} x={r.x} y={r.y} width={r.w} height={1.6} rx={0.8} fill="#ffcf7a" opacity={r.o} />
        ))}
      </g>
      <g className="voy-swell">
        <path
          d={`M -120 ${WATERLINE} ${Array.from({ length: 30 }, () => "q 30 -5 60 0 q 30 5 60 0").join(" ")}`}
          stroke="#5b86b3"
          strokeWidth={1.2}
          fill="none"
          opacity={0.7}
        />
      </g>
      <text x={24} y={36} className="voy-scene-title">
        {nameplate}
      </text>
    </g>
  );
});

function LinerBackdrop() {
  return <SceneBackdrop />;
}

/** The ocean liner as a VoyageView scene module (titanic). */
export const oceanLiner: SceneModule = {
  width: SCENE_W,
  height: SCENE_H,
  packZones,
  Backdrop: LinerBackdrop,
  Foreground: SceneForeground,
  // The reveal sweeps from bow to stern, the way the ship went down.
  revealDelay: (at) => ((SCENE_W - at.x) / SCENE_W) * 0.9,
  copy: {
    loadingIcon: "🚢",
    loading: (noun) => `Boarding every ${noun} and asking the model about each one…`,
    aboard: "aboard",
    everyone: "Everyone aboard",
    reveal: "Reveal real fate",
    star: "the ★ on the ship",
    pickHint: () =>
      "Click anyone aboard (or search by name) to see the model's probability and exactly which facts about them pushed it up or down. Or put someone new aboard:",
  },
};
