/**
 * The `office_tower` scene for VoyageView.tsx (toxic_leadership): a glass office tower
 * at night, one floor per zone (top floor first), every review a person at a desk — plus
 * the building's own commentary: a C-suite penthouse with its lights always on and
 * nobody at the desks, an "AI-FIRST" neon on the roof, an elevator that never stops at
 * the top, and a "we're hiring" banner in the lobby next to the exit.
 *
 * Unlike the liner, floors are sized from the data (the Data & AI lab of 1,000 people
 * gets more floor than QA's 400), so packZones returns each zone's box and the backdrop
 * draws floors, desks and labels from it. Fixed night palette, like an illustration.
 */

import { memo } from "react";
import type { Point, Rect, SceneModule, ZoneLayout } from "./scenes";
import "./office.css";

const W = 1200;
const H = 720;
const TOWER: Rect = { x0: 200, x1: 1000, y0: 70, y1: 700 };
const DESKS_X0 = 216;
const DESKS_X1 = 930;
const SHAFT_X = 944;
const FLOORS_TOP = 136;
const FLOORS_BOTTOM = 648;
const SLAB = 7; // gap between floors
const LABEL_BAND = 13; // floor-name strip at the top of each floor

function pack(counts: number[]): ZoneLayout {
  const available = FLOORS_BOTTOM - FLOORS_TOP - SLAB * Math.max(0, counts.length - 1);
  const width = DESKS_X1 - DESKS_X0;
  let pitch = 16;
  let rows: number[] = [];
  for (; pitch > 3; pitch -= 0.25) {
    const cols = Math.floor(width / pitch);
    rows = counts.map((c) => Math.max(1, Math.ceil((c + 1) / cols)));
    const needed = rows.reduce((sum, r) => sum + r * pitch + LABEL_BAND + 4, 0);
    if (needed <= available) break;
  }
  const cols = Math.floor(width / pitch);
  const heights = rows.map((r) => r * pitch + LABEL_BAND + 4);
  const extra = Math.max(0, available - heights.reduce((a, b) => a + b, 0)) / Math.max(1, counts.length);
  const zoneRects: Rect[] = [];
  const slots: Point[][] = [];
  let y = FLOORS_TOP;
  counts.forEach((_, z) => {
    const h = heights[z] + extra;
    zoneRects.push({ x0: TOWER.x0, x1: SHAFT_X - 6, y0: y, y1: y + h });
    const x0 = DESKS_X0 + (width - cols * pitch) / 2 + pitch / 2;
    const y0 = y + LABEL_BAND + (h - LABEL_BAND - rows[z] * pitch) / 2 + pitch / 2;
    const zone: Point[] = [];
    for (let r = 0; r < rows[z]; r++) for (let c = 0; c < cols; c++) zone.push({ x: x0 + c * pitch, y: y0 + r * pitch });
    slots.push(zone);
    y += h + SLAB;
  });
  return { slots, radius: pitch * 0.36, zoneRects };
}

// Deterministic city behind the tower (no Math.random — identical on every render).
const SKYLINE: { x: number; w: number; top: number; lit: Point[] }[] = (() => {
  let seed = 23;
  const next = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  const buildings = [];
  for (const [from, to] of [
    [0, 196],
    [1004, 1200],
  ]) {
    let x = from;
    while (x < to - 12) {
      const w = Math.min(to - x, 26 + next() * 44);
      const top = 300 + next() * 260;
      const lit: Point[] = [];
      for (let wy = top + 10; wy < 690; wy += 14) for (let wx = x + 6; wx < x + w - 6; wx += 11) if (next() > 0.72) lit.push({ x: wx, y: wy });
      buildings.push({ x, w, top, lit });
      x += w + 3;
    }
  }
  return buildings;
})();

const STARS: Point[] = (() => {
  let seed = 5;
  const next = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  return Array.from({ length: 70 }, () => ({ x: next() * W, y: next() * 260 }));
})();

const MULLIONS = Array.from({ length: 14 }, (_, i) => TOWER.x0 + 57 * (i + 1));
const C_SUITE_DESKS = [260, 360, 460, 700, 800];

const Backdrop = memo(function OfficeBackdrop({ layout, zoneLabels }: { layout: ZoneLayout; zoneLabels: string[] }) {
  const rects = layout.zoneRects ?? [];
  return (
    <g aria-hidden="true">
      <defs>
        <linearGradient id="off-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#03050c" />
          <stop offset="1" stopColor="#101b33" />
        </linearGradient>
        <linearGradient id="off-glass" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#0f1f38" />
          <stop offset="1" stopColor="#0a1528" />
        </linearGradient>
        <linearGradient id="off-penthouse" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#4a3515" />
          <stop offset="1" stopColor="#2b1f0d" />
        </linearGradient>
        <radialGradient id="off-moon" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor="#f5f1dc" stopOpacity="0.28" />
          <stop offset="1" stopColor="#f5f1dc" stopOpacity="0" />
        </radialGradient>
      </defs>

      <rect width={W} height={H} fill="url(#off-sky)" />
      {STARS.map((s, i) => (
        <circle key={i} cx={s.x} cy={s.y} r={0.8} fill="#cfd9f2" opacity={0.6} />
      ))}
      <circle cx={1100} cy={92} r={60} fill="url(#off-moon)" />
      <circle cx={1100} cy={92} r={17} fill="#efe9cf" />

      {SKYLINE.map((b, i) => (
        <g key={i}>
          <rect x={b.x} y={b.top} width={b.w} height={H - b.top} fill="#0b1222" stroke="#16223a" strokeWidth={0.6} />
          {b.lit.map((p, j) => (
            <rect key={j} x={p.x} y={p.y} width={4} height={5} fill="#f4c96a" opacity={0.35} />
          ))}
        </g>
      ))}

      {/* Roof + the neon everybody can read from the street. */}
      <rect x={TOWER.x0 - 20} y={TOWER.y0 - 10} width={TOWER.x1 - TOWER.x0 + 40} height={10} fill="#2b3a55" />
      <line x1={SHAFT_X + 22} y1={TOWER.y0 - 10} x2={SHAFT_X + 22} y2={22} stroke="#56627a" strokeWidth={1.5} />
      <circle cx={SHAFT_X + 22} cy={22} r={2.2} fill="#ff5a5a" className="off-beacon" />
      <text x={600} y={48} textAnchor="middle" className="off-neon">
        AI-FIRST ✦ 10× ✦ SYNERGY
      </text>

      {/* The tower. */}
      <rect x={TOWER.x0} y={TOWER.y0} width={TOWER.x1 - TOWER.x0} height={TOWER.y1 - TOWER.y0} fill="url(#off-glass)" stroke="#8ea4c6" strokeWidth={2} />
      {MULLIONS.map((x) => (
        <line key={x} x1={x} x2={x} y1={FLOORS_TOP - 4} y2={FLOORS_BOTTOM} stroke="#17294a" strokeWidth={1} />
      ))}

      {/* Penthouse: the C-suite — lights on, desks empty, the strategy on the whiteboard. */}
      <rect x={TOWER.x0} y={TOWER.y0} width={TOWER.x1 - TOWER.x0} height={60} fill="url(#off-penthouse)" />
      {C_SUITE_DESKS.map((x) => (
        <g key={x}>
          <rect x={x} y={108} width={46} height={6} rx={1.5} fill="#8a6a3a" />
          <rect x={x + 16} y={96} width={14} height={11} rx={1} fill="#1d1d1d" stroke="#c9a15e" strokeWidth={0.6} />
          <rect x={x + 14} y={114} width={4} height={8} fill="#6b5330" />
          <rect x={x + 30} y={114} width={4} height={8} fill="#6b5330" />
        </g>
      ))}
      <rect x={540} y={80} width={130} height={40} rx={2} fill="#f2efe6" stroke="#9c9480" strokeWidth={1} />
      <text x={605} y={95} textAnchor="middle" className="off-whiteboard">
        STRATEGY 2026
      </text>
      <text x={605} y={110} textAnchor="middle" className="off-whiteboard off-whiteboard--small">
        leverage · pivot · AI-first · align
      </text>
      <text x={TOWER.x0 + 12} y={TOWER.y0 + 15} className="off-floor-label off-floor-label--suite">
        C-SUITE · SENIOR LEADERSHIP (nobody at their desk)
      </text>
      <rect x={TOWER.x0} y={TOWER.y0 + 60} width={TOWER.x1 - TOWER.x0} height={4} fill="#324a6e" />

      {/* One floor per zone, sized from the data: slab, desks, name on the glass. */}
      {rects.map((r, z) => {
        const rowYs = [...new Set((layout.slots[z] ?? []).map((p) => p.y))];
        return (
          <g key={z}>
            <rect x={r.x0 + 1} y={r.y0} width={r.x1 - r.x0} height={r.y1 - r.y0} fill={z % 2 ? "#0e1c33" : "#11213b"} />
            {rowYs.map((y) => (
              <line key={y} x1={DESKS_X0} x2={DESKS_X1} y1={y + layout.radius * 1.35} y2={y + layout.radius * 1.35} stroke="#223a60" strokeWidth={1.2} />
            ))}
            <text x={r.x0 + 12} y={r.y0 + 10} className="off-floor-label">
              {(zoneLabels[z] ?? "").toUpperCase()}
            </text>
            <rect x={TOWER.x0} y={r.y1} width={TOWER.x1 - TOWER.x0} height={SLAB - 2} fill="#324a6e" />
          </g>
        );
      })}

      {/* Elevator shaft — the car goes up and down, never all the way to the top. */}
      <rect x={SHAFT_X} y={TOWER.y0 + 64} width={44} height={FLOORS_BOTTOM - TOWER.y0 - 64} fill="#08111f" stroke="#1d2f4d" strokeWidth={1} />
      <line x1={SHAFT_X + 22} x2={SHAFT_X + 22} y1={TOWER.y0 + 64} y2={FLOORS_BOTTOM} stroke="#2a3b58" strokeWidth={0.8} />
      <g className="off-elevator">
        <rect x={SHAFT_X + 5} y={560} width={34} height={36} rx={2} fill="#c9d4e6" stroke="#6d7c95" strokeWidth={1} />
        <rect x={SHAFT_X + 9} y={566} width={26} height={16} fill="#f4d58a" opacity={0.8} />
      </g>

      {/* Lobby and street. */}
      <rect x={TOWER.x0} y={FLOORS_BOTTOM + 4} width={TOWER.x1 - TOWER.x0} height={TOWER.y1 - FLOORS_BOTTOM - 4} fill="#18212f" />
      <rect x={560} y={660} width={80} height={40} fill="#0d1520" stroke="#8ea4c6" strokeWidth={1} />
      <line x1={600} x2={600} y1={660} y2={700} stroke="#8ea4c6" strokeWidth={1} />
      <rect x={850} y={664} width={60} height={36} fill="#0d1520" stroke="#8ea4c6" strokeWidth={1} />
      <rect x={862} y={655} width={36} height={8} rx={1} fill="#1f7a3e" />
      <text x={880} y={661.5} textAnchor="middle" className="off-exit">
        EXIT
      </text>
      <rect x={250} y={664} width={170} height={22} rx={2} fill="#1f3a66" />
      <text x={335} y={679} textAnchor="middle" className="off-banner">
        WE'RE HIRING! ROCKSTAR 10× NINJAS
      </text>
      <rect x={0} y={TOWER.y1} width={W} height={H - TOWER.y1} fill="#070a11" />
      {[120, 1080].map((x) => (
        <g key={x}>
          <line x1={x} x2={x} y1={TOWER.y1} y2={640} stroke="#39455a" strokeWidth={2} />
          <circle cx={x} cy={640} r={5} fill="#f6d27a" opacity={0.85} />
        </g>
      ))}
    </g>
  );
});

const Foreground = memo(function OfficeForeground({ nameplate }: { nameplate: string }) {
  return (
    <g aria-hidden="true" pointerEvents="none">
      <polygon points={`${TOWER.x0},${TOWER.y0 + 64} ${TOWER.x0 + 260},${TOWER.y0 + 64} ${TOWER.x0 + 60},${FLOORS_BOTTOM} ${TOWER.x0},${FLOORS_BOTTOM}`} fill="#ffffff" opacity={0.035} />
      <text x={24} y={36} className="voy-scene-title">
        {nameplate}
      </text>
    </g>
  );
});

/** The office tower as a VoyageView scene module. */
export const officeTower: SceneModule = {
  width: W,
  height: H,
  packZones: pack,
  Backdrop,
  Foreground,
  // The reveal runs floor by floor, top to bottom.
  revealDelay: (at) => ((at.y - FLOORS_TOP) / (FLOORS_BOTTOM - FLOORS_TOP)) * 0.9,
  copy: {
    loadingIcon: "🏢",
    loading: (noun) => `Seating every ${noun} at a desk and asking the model about each review…`,
    aboard: "in the tower",
    everyone: "Everyone in the tower",
    reveal: "Reveal real ratings",
    star: "the ★ in the tower",
    pickHint: (noun) =>
      `Click any desk (or search by job title or id) to read that ${noun}'s review, with the words the model weighed highlighted and why it scored it as it did. Or write a new review:`,
  },
};
