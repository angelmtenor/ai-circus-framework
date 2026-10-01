import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DecisionPolicy } from "./apiClient";
import { DESK_PALETTE, TRAY_GLYPHS, TRAYS, trayOf, type DeskCase, type Tray } from "./caseDeskLogic";
import type { SurfaceMode } from "./riskPalette";

// Logical canvas size; everything is scaled to the element's width.
const W = 1000;
const H = 450;
const NODE_W = 12;
const SOURCE = { x: 22, y: 150, h: 150 };
const GATE = { x: 262, y: 130, h: 190 };
const MODEL = { x: 540, y: 30, h: 260 };
const TRAY_RECTS: Record<Tray, { x: number; y: number; w: number; h: number }> = {
  approve: { x: 735, y: 12, w: 250, h: 124 },
  review: { x: 735, y: 164, w: 250, h: 124 },
  deny: { x: 735, y: 316, w: 250, h: 124 },
  request_info: { x: 262, y: 336, w: 262, h: 104 },
  reject: { x: 538, y: 336, w: 178, h: 104 },
};
const INTRO_SECONDS = 2.8;
const STAGGER_SECONDS = 1.5;

type Particle = {
  id: string;
  x: number;
  y: number;
  tray: Tray;
  tx: number;
  ty: number;
  radius: number;
  delay: number; // seconds after mount before this one starts moving
  startY: number; // where it enters the registry bar
  gateY: number;
  modelY: number;
  settled: boolean;
};

type Anchors = Record<string, { y0: number; y1: number }>;

const ease = (u: number) => u * u * (3 - 2 * u);
const lerp = (a: number, b: number, u: number) => a + (b - a) * u;

/** A translucent band between two vertical edges, as two cubic Béziers (a Sankey link). */
function ribbon(ctx: CanvasRenderingContext2D, x0: number, a0: number, a1: number, x1: number, b0: number, b1: number) {
  const mid = (x0 + x1) / 2;
  ctx.beginPath();
  ctx.moveTo(x0, a0);
  ctx.bezierCurveTo(mid, a0, mid, b0, x1, b0);
  ctx.lineTo(x1, b1);
  ctx.bezierCurveTo(mid, b1, mid, a1, x0, a1);
  ctx.closePath();
  ctx.fill();
}

type Props = {
  cases: DeskCase[];
  policy: Pick<DecisionPolicy, "approve_at" | "deny_at">;
  mode: SurfaceMode;
  trayLabels: Record<Tray, string>;
  nodeLabels: { registry: string; rules: string; model: string };
  ink: string;
  dim: string;
  revealed: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  caseNoun: string;
};

export function CaseDeskCircuit({ cases, policy, mode, trayLabels, nodeLabels, ink, dim, revealed, selectedId, onSelect, caseNoun }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const particles = useRef<Map<string, Particle>>(new Map());
  const startedAt = useRef<number>(0);
  const size = useRef({ scale: 1 });
  const [hover, setHover] = useState<{ x: number; y: number; id: string } | null>(null);
  const reduceMotion = useMemo(() => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false, []);

  // Everything the animation loop reads lives in one ref, so a slider move never restarts it.
  const live = useRef({ cases, policy, mode, trayLabels, nodeLabels, ink, dim, revealed, selectedId });
  live.current = { cases, policy, mode, trayLabels, nodeLabels, ink, dim, revealed, selectedId };

  const byId = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);

  const layoutTargets = useCallback(() => {
    const { cases: all, policy: pol } = live.current;
    const members: Record<Tray, DeskCase[]> = { request_info: [], reject: [], approve: [], review: [], deny: [] };
    for (const c of all) members[trayOf(pol, c)].push(c);
    for (const tray of TRAYS) {
      // Model trays are ordered by score, so moving a threshold visibly re-sorts the pile.
      members[tray].sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0) || a.id.localeCompare(b.id));
      const rect = TRAY_RECTS[tray];
      const innerW = rect.w - 20;
      const innerH = rect.h - 40;
      const n = Math.max(1, members[tray].length);
      const gap = Math.max(4.4, Math.min(9.5, Math.sqrt((innerW * innerH) / n)));
      const cols = Math.max(1, Math.floor(innerW / gap));
      members[tray].forEach((c, i) => {
        const p = particles.current.get(c.id);
        if (!p) return;
        p.tray = tray;
        p.tx = rect.x + 10 + gap / 2 + (i % cols) * gap;
        p.ty = rect.y + 34 + gap / 2 + Math.floor(i / cols) * gap;
        p.radius = Math.max(1.6, gap * 0.36);
      });
    }
  }, []);

  // (Re)build the particles when the portfolio changes — keeping the ones already on screen.
  useEffect(() => {
    const now = performance.now();
    if (particles.current.size === 0) startedAt.current = now;
    const next = new Map<string, Particle>();
    const total = cases.length || 1;
    cases.forEach((c, i) => {
      const old = particles.current.get(c.id);
      next.set(
        c.id,
        old ?? {
          id: c.id,
          x: SOURCE.x + NODE_W / 2,
          y: SOURCE.y + (i / total) * SOURCE.h,
          tray: "review",
          tx: 0,
          ty: 0,
          radius: 3,
          delay: ((i * 7919) % total) / total * STAGGER_SECONDS, // scattered, not in file order
          startY: SOURCE.y + (i / total) * SOURCE.h,
          gateY: GATE.y + 14 + ((i * 31) % total) / total * (GATE.h - 28),
          modelY: MODEL.y + 14 + ((i * 17) % total) / total * (MODEL.h - 28),
          settled: reduceMotion,
        },
      );
    });
    particles.current = next;
    layoutTargets();
    if (reduceMotion) {
      for (const p of next.values()) {
        p.x = p.tx;
        p.y = p.ty;
      }
    }
  }, [cases, layoutTargets, reduceMotion]);

  useEffect(() => {
    layoutTargets();
  }, [policy.approve_at, policy.deny_at, layoutTargets]);

  // Size the canvas to its container (crisp on retina).
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const resize = () => {
      const cssW = wrap.clientWidth;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssW * (H / W) * dpr);
      canvas.style.height = `${cssW * (H / W)}px`;
      size.current.scale = (cssW * dpr) / W;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let frame = 0;
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (!canvas || !ctx) return;
      const { cases: all, policy: pol, mode: m, trayLabels: labels, nodeLabels: nodes, ink: inkColor, dim: dimColor, revealed: reveal, selectedId: selected } = live.current;
      const palette = DESK_PALETTE[m];
      const elapsed = (now - startedAt.current) / 1000;
      ctx.setTransform(size.current.scale, 0, 0, size.current.scale, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // ── tray counts and the ribbons between the nodes ──
      const counts: Record<Tray, number> = { request_info: 0, reject: 0, approve: 0, review: 0, deny: 0 };
      for (const c of all) counts[trayOf(pol, c)] += 1;
      const total = Math.max(1, all.length);
      const k = SOURCE.h / total;
      const reaching = counts.approve + counts.review + counts.deny;
      const stack = (start: number, order: Tray[]): Anchors => {
        const anchors: Anchors = {};
        let y = start;
        for (const t of order) {
          anchors[t] = { y0: y, y1: y + Math.max(counts[t] * k, counts[t] ? 2 : 0) };
          y = anchors[t].y1;
        }
        return anchors;
      };
      const gateOut = stack(GATE.y + (GATE.h - SOURCE.h) / 2, ["approve", "request_info", "reject"]);
      const gateToModel = { y0: gateOut.approve.y0, y1: gateOut.approve.y0 + Math.max(reaching * k, 2) };
      const modelOut = stack(MODEL.y + (MODEL.h - reaching * k) / 2, ["approve", "review", "deny"]);
      ctx.globalAlpha = 0.2;
      ctx.fillStyle = dimColor;
      ribbon(ctx, SOURCE.x + NODE_W, SOURCE.y, SOURCE.y + SOURCE.h, GATE.x, GATE.y + (GATE.h - SOURCE.h) / 2, GATE.y + (GATE.h + SOURCE.h) / 2);
      ctx.fillStyle = dimColor;
      ribbon(ctx, GATE.x + NODE_W, gateToModel.y0, gateToModel.y1, MODEL.x, modelOut.approve.y0, modelOut.deny.y1);
      for (const t of ["request_info", "reject"] as const) {
        if (!counts[t]) continue;
        ctx.fillStyle = palette[t];
        const r = TRAY_RECTS[t];
        ribbon(ctx, GATE.x + NODE_W, gateOut[t].y0, gateOut[t].y1, r.x + r.w / 2 - 14, r.y, r.y + 5);
      }
      for (const t of ["approve", "review", "deny"] as const) {
        if (!counts[t]) continue;
        ctx.fillStyle = palette[t];
        const r = TRAY_RECTS[t];
        ribbon(ctx, MODEL.x + NODE_W, modelOut[t].y0, modelOut[t].y1, r.x, r.y + 26, r.y + 26 + Math.max(counts[t] * k, 2));
      }
      ctx.globalAlpha = 1;

      // ── nodes ──
      ctx.fillStyle = dimColor;
      for (const node of [SOURCE, GATE, MODEL]) {
        ctx.beginPath();
        ctx.roundRect(node.x, node.y, NODE_W, node.h, 6);
        ctx.fill();
      }
      ctx.fillStyle = inkColor;
      ctx.font = "600 15px system-ui, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(nodes.registry, SOURCE.x, SOURCE.y - 14);
      ctx.fillText(nodes.rules, GATE.x - 50, GATE.y - 14);
      ctx.fillText(nodes.model, MODEL.x - 10, MODEL.y - 12);
      ctx.fillStyle = dimColor;
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText(`${all.length}`, SOURCE.x, SOURCE.y + SOURCE.h + 20);
      ctx.fillText(`${reaching}`, MODEL.x - 10, MODEL.y + MODEL.h + 20);

      // ── trays: frame, glyph, label, count ──
      for (const t of TRAYS) {
        const r = TRAY_RECTS[t];
        ctx.fillStyle = palette[t];
        ctx.globalAlpha = 0.09;
        ctx.beginPath();
        ctx.roundRect(r.x, r.y, r.w, r.h, 10);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = palette[t];
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.roundRect(r.x, r.y, r.w, r.h, 10);
        ctx.stroke();
        ctx.fillStyle = inkColor;
        ctx.font = "600 13px system-ui, sans-serif";
        ctx.textAlign = "right";
        ctx.fillText(`${counts[t]}`, r.x + r.w - 10, r.y + 20);
        const room = r.w - 20 - ctx.measureText(`${counts[t]}`).width - 10;
        ctx.font = "700 13px system-ui, sans-serif";
        ctx.textAlign = "left";
        let title = `${TRAY_GLYPHS[t]}  ${labels[t]}`;
        while (title.length > 4 && ctx.measureText(title).width > room) title = `${title.slice(0, -2).trimEnd()}…`;
        ctx.fillText(title, r.x + 10, r.y + 20);
      }

      // ── particles ──
      const caseMap = new Map(all.map((c) => [c.id, c]));
      for (const p of particles.current.values()) {
        const c = caseMap.get(p.id);
        if (!c) continue;
        const stopped = c.gate === "request_info" || c.gate === "reject";
        if (!p.settled) {
          const u = (elapsed - p.delay) / INTRO_SECONDS;
          if (u <= 0) {
            p.x = SOURCE.x + NODE_W / 2;
            p.y = p.startY;
          } else if (u >= 1) {
            p.settled = true;
          } else {
            // Registry → rules → (model →) tray, a smooth ride through the waypoints.
            const way = stopped
              ? [[SOURCE.x + 6, p.startY], [GATE.x + 6, p.gateY], [p.tx, p.ty]]
              : [[SOURCE.x + 6, p.startY], [GATE.x + 6, p.gateY], [MODEL.x + 6, p.modelY], [p.tx, p.ty]];
            const seg = u * (way.length - 1);
            const i = Math.min(way.length - 2, Math.floor(seg));
            const f = ease(seg - i);
            p.x = lerp(way[i][0], way[i + 1][0], f);
            p.y = lerp(way[i][1], way[i + 1][1], f);
          }
        }
        if (p.settled) {
          p.x += (p.tx - p.x) * 0.16;
          p.y += (p.ty - p.y) * 0.16;
        }
        const hidden = !p.settled && elapsed - p.delay <= 0;
        let fill = palette[p.tray];
        if (reveal && !stopped) fill = c.actual ? palette.approve : palette.deny;
        ctx.globalAlpha = hidden ? 0 : 0.95;
        ctx.fillStyle = fill;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fill();
        if (reveal && !stopped && p.settled && ((p.tray === "approve") !== c.actual) && p.tray !== "review") {
          ctx.strokeStyle = inkColor; // a proposal the real resolution contradicts
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.radius + 1.6, 0, Math.PI * 2);
          ctx.stroke();
        }
        if (p.id === selected) {
          const pulse = 6 + 2.5 * Math.sin(now / 240);
          ctx.globalAlpha = 1;
          ctx.strokeStyle = inkColor;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(p.x, p.y, pulse, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
      ctx.globalAlpha = 1;
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, []);

  const nearest = (clientX: number, clientY: number): { id: string; x: number; y: number } | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const lx = ((clientX - rect.left) / rect.width) * W;
    const ly = ((clientY - rect.top) / rect.height) * H;
    let best: Particle | null = null;
    let bestD = 10 * 10;
    for (const p of particles.current.values()) {
      if (!p.settled) continue;
      const d = (p.x - lx) ** 2 + (p.y - ly) ** 2;
      if (d < bestD) {
        best = p;
        bestD = d;
      }
    }
    return best ? { id: best.id, x: (best.x / W) * rect.width, y: (best.y / H) * rect.height } : null;
  };

  const hovered = hover ? byId.get(hover.id) : null;
  return (
    <div className="cd-circuit" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`${cases.length} ${caseNoun}: ${TRAYS.map((t) => `${trayLabels[t]} ${cases.filter((c) => trayOf(policy, c) === t).length}`).join(", ")}`}
        onMouseMove={(e) => setHover(nearest(e.clientX, e.clientY))}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          const hit = nearest(e.clientX, e.clientY);
          if (hit) onSelect(hit.id);
        }}
        style={{ cursor: hover ? "pointer" : "default" }}
      />
      {hover && hovered && (
        <div className="cd-tip" style={{ left: hover.x, top: hover.y }}>
          <b>{hovered.name}</b>
          <span>{hovered.id}</span>
          <span>{hovered.probability === null ? trayLabels[trayOf(policy, hovered)] : `${(hovered.probability * 100).toFixed(0)}% · ${trayLabels[trayOf(policy, hovered)]}`}</span>
        </div>
      )}
    </div>
  );
}
