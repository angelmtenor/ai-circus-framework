/**
 * The network explorer's data model — pure functions, no React, no DOM: joins a
 * scenario's graph (prediction's GET /graph/{slug}) with its scored dataset rows,
 * and answers the questions the view asks of it (search, shortest path, strongest
 * ties, linked entities, backtest counts, peer percentiles). See NetworkExplorerView.
 */
import type { SimulationLinkDatum, SimulationNodeDatum } from "d3-force";
import type { GraphEdge, NetworkExplorerExtra, NetworkGraph } from "./apiClient";
import type { Record_ } from "./predictUtils";

export type NodeKind = "row" | "entity" | "context";

/** A scored dataset row, as the view needs it. */
export type RowInfo = {
  record: Record_;
  display: Record<string, unknown>;
  probability: number;
  tier: number;
  actual: 0 | 1;
  size: number;
  // Out-of-fold SHAP contributions (prediction's /out-of-fold) — null when the view
  // had to fall back to the deployed model's own scores.
  contributions: Record<string, number> | null;
};

export interface NxNode extends SimulationNodeDatum {
  id: string;
  kind: NodeKind;
  label: string;
  detail: string;
  group: string | null;
  groupIndex: number; // index into Network.groups, -1 when ungrouped
  type?: string;
  description?: string;
  citation?: string;
  row: RowInfo | null;
  r: number; // radius in world units
  degree: number; // distinct flow neighbours
  strength: number; // total flow weight
  orbit: boolean; // no flow tie at all: drawn on the outer ring
}

export interface NxLink extends SimulationLinkDatum<NxNode> {
  source: NxNode;
  target: NxNode;
  kind: string;
  flow: boolean; // the extras' flow_edge_kind: weighted, with a per-period series
  weight: number;
  label?: string;
  citation?: string;
  series: Float32Array | null; // aligned with Network.periods
}

export type Group = { name: string; size: number; colorIndex: number };

export type Network = {
  nodes: NxNode[];
  links: NxLink[];
  byId: Map<string, NxNode>;
  adjacency: Map<string, { node: NxNode; link: NxLink }[]>;
  periods: string[];
  periodTotals: number[];
  groups: Group[];
  maxWeight: number;
  rows: NxNode[]; // kind === "row", every dataset row (graph or not)
};

export const COLORED_GROUPS = 3; // NETWORK_PALETTE.*.communities.length

export function tierOf(probability: number, tiers: { min_probability: number }[]): number {
  let tier = 0;
  tiers.forEach((t, i) => {
    if (probability >= t.min_probability) tier = i;
  });
  return tier;
}

/** World radius of a row's node — log-scaled so both the median insider and a $100M
 * package stay readable. */
export function rowRadius(size: number): number {
  return 3.2 + 3.1 * Math.log10(1 + Math.max(0, size) / 10_000);
}

export type RowInput = { id: string; name: string; detail: string; info: RowInfo };

export function buildNetwork(graph: NetworkGraph, rows: RowInput[], extras: NetworkExplorerExtra): Network {
  const byId = new Map<string, NxNode>();
  const rowById = new Map(rows.map((r) => [r.id, r]));
  const periodIndex = new Map(graph.periods.map((p, i) => [p, i]));

  for (const node of graph.nodes) {
    const row = node.kind === "row" ? rowById.get(node.id) : undefined;
    // A graph row with no matching dataset row (a tenant whose data diverged from the
    // shared graph) is still drawn — as unscored context.
    const kind: NodeKind = node.kind === "row" && !row ? "context" : node.kind;
    byId.set(node.id, {
      id: node.id,
      kind,
      label: row?.name || node.label || node.id,
      detail: row?.detail ?? node.type ?? "",
      group: node.group ?? null,
      groupIndex: -1,
      type: node.type,
      description: node.description,
      citation: node.citation,
      row: row?.info ?? null,
      r: kind === "row" ? rowRadius(row!.info.size) : kind === "entity" ? 9 : 2.6,
      degree: 0,
      strength: 0,
      orbit: false,
    });
  }
  // Every dataset row is on the board, even one the graph doesn't mention.
  for (const row of rows) {
    if (!byId.has(row.id)) {
      byId.set(row.id, {
        id: row.id,
        kind: "row",
        label: row.name,
        detail: row.detail,
        group: null,
        groupIndex: -1,
        row: row.info,
        r: rowRadius(row.info.size),
        degree: 0,
        strength: 0,
        orbit: true,
      });
    }
  }

  const links: NxLink[] = [];
  const periodTotals = new Array(graph.periods.length).fill(0);
  let maxWeight = 1;
  for (const edge of graph.edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    const flow = edge.kind === extras.flow_edge_kind;
    let series: Float32Array | null = null;
    if (flow && graph.periods.length) {
      series = new Float32Array(graph.periods.length);
      for (const [period, value] of Object.entries(edge.series ?? {})) {
        const i = periodIndex.get(period);
        if (i !== undefined) {
          series[i] = value;
          periodTotals[i] += value;
        }
      }
    }
    if (flow) maxWeight = Math.max(maxWeight, edge.weight);
    links.push({ source, target, kind: edge.kind, flow, weight: edge.weight, label: edge.label, citation: edge.citation, series });
  }

  const adjacency = new Map<string, { node: NxNode; link: NxLink }[]>();
  const flowNeighbours = new Map<string, Set<string>>();
  for (const link of links) {
    for (const [a, b] of [
      [link.source, link.target],
      [link.target, link.source],
    ]) {
      if (!adjacency.has(a.id)) adjacency.set(a.id, []);
      adjacency.get(a.id)!.push({ node: b, link });
      if (link.flow) {
        if (!flowNeighbours.has(a.id)) flowNeighbours.set(a.id, new Set());
        flowNeighbours.get(a.id)!.add(b.id);
        a.strength += link.weight;
      }
    }
  }
  const nodes = [...byId.values()];
  for (const node of nodes) {
    node.degree = flowNeighbours.get(node.id)?.size ?? 0;
    const anyLink = (adjacency.get(node.id) ?? []).length > 0;
    node.orbit = node.kind === "row" ? node.degree === 0 && !anyLink : false;
  }

  const counts = new Map<string, number>();
  for (const node of nodes) if (node.group) counts.set(node.group, (counts.get(node.group) ?? 0) + 1);
  const groups = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, size], i) => ({ name, size, colorIndex: i < COLORED_GROUPS ? i : -1 }));
  const groupIndex = new Map(groups.map((g, i) => [g.name, i]));
  for (const node of nodes) node.groupIndex = node.group ? (groupIndex.get(node.group) ?? -1) : -1;

  return {
    nodes,
    links,
    byId,
    adjacency,
    periods: graph.periods,
    periodTotals,
    groups,
    maxWeight,
    rows: nodes.filter((n) => n.kind === "row"),
  };
}

function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Nodes whose id, name or label matches `query` — rows and entities first, best
 * (prefix) matches first. */
export function searchNodes(net: Network, query: string, limit = 8): NxNode[] {
  const q = fold(query.trim());
  if (!q) return [];
  const scored: { node: NxNode; score: number }[] = [];
  for (const node of net.nodes) {
    if (node.kind === "context") continue;
    const hay = [node.label, node.id, node.detail].map(fold);
    const at = Math.min(...hay.map((h) => (h.includes(q) ? h.indexOf(q) : Infinity)));
    if (at === Infinity) continue;
    const words = hay[0].split(/\s+/);
    const wordStart = words.some((w) => w.startsWith(q));
    scored.push({ node, score: (wordStart ? 0 : 10) + at + (node.kind === "entity" ? 0.5 : 0) });
  }
  return scored.sort((a, b) => a.score - b.score || a.node.label.localeCompare(b.node.label)).slice(0, limit).map((s) => s.node);
}

/** Shortest path between two nodes over every link (e-mail ties and relations): fewest
 * hops first, and among equally short paths the one through documented relations and
 * scored nodes rather than unscored context (Dijkstra with small tie-breaking costs). */
export function shortestPath(net: Network, from: string, to: string, viaContext = true): NxNode[] | null {
  if (!net.byId.has(from) || !net.byId.has(to)) return null;
  if (from === to) return [net.byId.get(from)!];
  const cost = new Map<string, number>([[from, 0]]);
  const previous = new Map<string, string>();
  const done = new Set<string>();
  const queue: [number, string][] = [[0, from]];
  while (queue.length) {
    let best = 0;
    for (let i = 1; i < queue.length; i++) if (queue[i][0] < queue[best][0]) best = i;
    const [c, id] = queue.splice(best, 1)[0];
    if (done.has(id)) continue;
    done.add(id);
    if (id === to) break;
    for (const { node, link } of net.adjacency.get(id) ?? []) {
      if (done.has(node.id)) continue;
      if (node.kind === "context" && node.id !== to && !viaContext) continue;
      const step = 1 + (node.kind === "context" ? 0.3 : 0) + (link.flow ? 0.05 : 0);
      const next = c + step;
      if (next < (cost.get(node.id) ?? Infinity)) {
        cost.set(node.id, next);
        previous.set(node.id, id);
        queue.push([next, node.id]);
      }
    }
  }
  if (!previous.has(to)) return null;
  const path = [to];
  while (path[path.length - 1] !== from) path.push(previous.get(path[path.length - 1])!);
  return path.reverse().map((id) => net.byId.get(id)!);
}

/** The link joining two adjacent nodes (the heavier one, for a two-way e-mail tie). */
export function linkBetween(net: Network, a: string, b: string): NxLink | null {
  const candidates = (net.adjacency.get(a) ?? []).filter((x) => x.node.id === b).map((x) => x.link);
  if (!candidates.length) return null;
  return candidates.reduce((best, l) => (l.flow && (!best.flow || l.weight > best.weight) ? l : best));
}

export type Tie = { node: NxNode; sent: number; received: number; total: number };

/** A node's strongest flow ties, both directions summed. */
export function topTies(net: Network, id: string, limit = 6): Tie[] {
  const ties = new Map<string, Tie>();
  for (const { node, link } of net.adjacency.get(id) ?? []) {
    if (!link.flow) continue;
    const tie = ties.get(node.id) ?? { node, sent: 0, received: 0, total: 0 };
    if (link.source.id === id) tie.sent += link.weight;
    else tie.received += link.weight;
    tie.total += link.weight;
    ties.set(node.id, tie);
  }
  return [...ties.values()].sort((a, b) => b.total - a.total).slice(0, limit);
}

export type Relation = { node: NxNode; label: string; citation?: string; outgoing: boolean };

/** A node's non-flow relations (e.g. "general partner of"), with their citations. */
export function relations(net: Network, id: string): Relation[] {
  return (net.adjacency.get(id) ?? [])
    .filter(({ link }) => !link.flow)
    .map(({ node, link }) => ({ node, label: link.label ?? link.kind, citation: link.citation, outgoing: link.source.id === id }));
}

export type Backtest = { tp: number; fp: number; fn: number; tn: number; flagged: number; positives: number };

/** Confusion counts with "flagged" = tier ≥ flagTier. */
export function backtest(rows: NxNode[], flagTier: number): Backtest {
  const out: Backtest = { tp: 0, fp: 0, fn: 0, tn: 0, flagged: 0, positives: 0 };
  for (const node of rows) {
    if (!node.row) continue;
    const flagged = node.row.tier >= flagTier;
    const positive = node.row.actual === 1;
    if (flagged) out.flagged += 1;
    if (positive) out.positives += 1;
    if (flagged && positive) out.tp += 1;
    else if (flagged) out.fp += 1;
    else if (positive) out.fn += 1;
    else out.tn += 1;
  }
  return out;
}

/** Share of `sorted` strictly below `v` — where a row's value sits among all rows. */
export function percentileOf(sorted: number[], v: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  return sorted.length ? lo / sorted.length : 0;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2001-10" -> "Oct 2001"; "2001-10-16" -> "16 Oct 2001"; "2022-09-03T14" -> "3 Sep · 14:00"; anything else as-is. */
export function periodLabel(period: string): string {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2})(?:T(\d{2}))?)?$/.exec(period);
  if (!m) return period;
  const month = MONTHS[Number(m[2]) - 1] ?? m[2];
  if (m[4] !== undefined) return `${Number(m[3])} ${month} · ${m[4]}:00`;
  return m[3] ? `${Number(m[3])} ${month} ${m[1]}` : `${month} ${m[1]}`;
}

/** The replay's period length: hourly periods ("2022-09-03T14") or monthly ones. */
export function periodUnit(periods: string[]): "hour" | "month" {
  return periods.some((p) => p.includes("T")) ? "hour" : "month";
}

/** Axis ticks under the timeline: the year at each January, or the day at each midnight. */
export function periodTicks(periods: string[]): { index: number; label: string }[] {
  if (periodUnit(periods) === "hour") {
    return periods
      .map((p, i) => ({ index: i, label: periodLabel(p.slice(0, 10)).replace(/ \d{4}$/, ""), midnight: p.endsWith("T00") }))
      .filter((t) => t.midnight)
      .map(({ index, label }) => ({ index, label }));
  }
  return periods.map((p, i) => ({ index: i, label: p.slice(0, 4), jan: p.endsWith("-01") })).filter((t) => t.jan).map(({ index, label }) => ({ index, label }));
}

/** Merge the edges of several `kinds` (e.g. one per payment format) into one `into` edge per
 * ordered pair — weights and per-period series summed, labelled with the biggest kinds. */
export function mergeFlowKinds(graph: NetworkGraph, kinds: string[], into: string, labels: Record<string, string> = {}): NetworkGraph {
  const wanted = new Set(kinds);
  const merged = new Map<string, { edge: GraphEdge; byKind: Map<string, number> }>();
  const rest: GraphEdge[] = [];
  for (const edge of graph.edges) {
    if (!wanted.has(edge.kind)) {
      rest.push(edge);
      continue;
    }
    const key = `${edge.source}\u0000${edge.target}`;
    let entry = merged.get(key);
    if (!entry) {
      entry = { edge: { source: edge.source, target: edge.target, kind: into, weight: 0, series: {} }, byKind: new Map() };
      merged.set(key, entry);
    }
    entry.edge.weight += edge.weight;
    for (const [period, value] of Object.entries(edge.series ?? {})) entry.edge.series[period] = (entry.edge.series[period] ?? 0) + value;
    entry.byKind.set(edge.kind, (entry.byKind.get(edge.kind) ?? 0) + edge.weight);
  }
  const flows = [...merged.values()].map(({ edge, byKind }) => ({
    ...edge,
    label: [...byKind].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([kind]) => labels[kind] ?? kind).join(" · "),
  }));
  return { ...graph, edges: [...rest, ...flows] };
}

/** Index of the period an event date ("YYYY-MM[-DD]") falls in, or -1. */
export function periodOf(periods: string[], date: string): number {
  return periods.indexOf(date.slice(0, 7));
}

/** A flow link's weight in the replay window ending at `period`: that month, plus a
 * fading tail of the two before it (so the web glows on rather than blinking). */
export function windowWeight(link: NxLink, period: number): number {
  const s = link.series;
  if (!s) return 0;
  return (s[period] ?? 0) + 0.45 * (period > 0 ? s[period - 1] : 0) + 0.2 * (period > 1 ? s[period - 2] : 0);
}

/** SHAP units: a logistic regression's LinearExplainer explains log-odds, tree models
 * (TreeExplainer, model_output="probability") explain probability. */
export type ShapScale = "probability" | "log_odds";

export function shapScale(modelName: string | null | undefined): ShapScale {
  return modelName === "logistic_regression" ? "log_odds" : "probability";
}

export function formatPush(value: number, scale: ShapScale): string {
  const sign = value >= 0 ? "+" : "−";
  return scale === "probability" ? `${sign}${Math.abs(value * 100).toFixed(1)} pts` : `${sign}${Math.abs(value).toFixed(2)} log-odds`;
}

/** Regular English plural of a YAML wording field ("entity" -> "entities", "bank" -> "banks"). */
export function plural(noun: string): string {
  if (/[^aeiou]y$/i.test(noun)) return `${noun.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/i.test(noun)) return `${noun}es`;
  return `${noun}s`;
}

export function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

/** Money-aware compact number: 81,525,000 -> "81.5M"; 0.63 -> "0.63". */
export function compact(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
  if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { maximumFractionDigits: a < 1 ? 2 : 1 });
}
