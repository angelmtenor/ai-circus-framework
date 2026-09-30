/**
 * The money trail's data model — pure functions, no React, no DOM: joins a scenario's
 * network (prediction's GET /graph/{slug}: banks as entities, holders as row / context
 * nodes, hourly flows per payment format) with its scored dataset rows, and answers the
 * questions MoneyTrailView asks of it (flows per hour, a holder's counterparties, the
 * backtest, the typology gallery, peer percentiles). See MoneyTrailView.
 */
import type { MoneyTrailExtra, NetworkExplorerExtra, NetworkGraph } from "./apiClient";
import type { Record_ } from "./predictUtils";

export type HolderInfo = {
  record: Record_;
  probability: number;
  tier: number;
  actual: 0 | 1;
  size: number;
  typology: string | null; // "Fan-out"…; null when the holder is in no scheme
  caseId: string | null; // the laundering attempt it belongs to
};

export type MtNode = {
  id: string;
  kind: "row" | "entity" | "context";
  label: string;
  detail: string;
  country: string;
  person: boolean; // a natural person (drawn as a circle), else a company (a diamond)
  holder: HolderInfo | null;
  // Set by the scene's layout (screen pixels) and its hit-testing.
  x: number;
  y: number;
  r: number;
  volume: number; // total USD moved across the drawn flows
};

export type MtFlow = {
  index: number;
  source: MtNode;
  target: MtNode;
  kind: string;
  weight: number; // total USD
  currency: string | null;
  hours: Float32Array; // USD per period
};

export type HolderRow = HolderInfo & {
  id: string;
  name: string;
  detail: string;
  person: boolean;
};

export type Model = {
  nodes: MtNode[];
  byId: Map<string, MtNode>;
  flows: MtFlow[];
  periods: string[];
  hourlyUsd: Float64Array;
  hourlyCount: Int32Array;
  flowsByHour: number[][]; // flow indices active in each hour, biggest first
  adjacency: Map<string, MtFlow[]>;
  banksOf: Map<string, MtNode[]>; // holder id -> the member banks it holds accounts at
  caseMembers: Map<string, string[]>; // attempt id -> node ids on the map
  countries: string[]; // countries with at least one node, in the extras' order
  maxWeight: number;
};

export function tierOf(probability: number, tiers: { min_probability: number }[]): number {
  let tier = 0;
  tiers.forEach((t, i) => {
    if (probability >= t.min_probability) tier = i;
  });
  return tier;
}

export function pct(p: number, digits = 0): string {
  return `${(p * 100).toFixed(digits)}%`;
}

export function money(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2022-09-03T14" -> "Sep 3 · 14:00". */
export function hourLabel(period: string): string {
  const [date, hour] = period.split("T");
  const [, m, d] = date.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d} · ${hour ?? "00"}:00`;
}

/** "2022-09-03T14" -> "Sep 3". */
export function dayLabel(period: string): string {
  const [, m, d] = period.split("T")[0].split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

export type RowInput = HolderRow & { display: Record<string, unknown> };

export function buildModel(graph: NetworkGraph, rows: RowInput[], extras: MoneyTrailExtra): Model {
  const rowById = new Map(rows.map((r) => [r.id, r]));
  const flowKinds = new Set(extras.flows.map((f) => f.kind));
  const periodIndex = new Map(graph.periods.map((p, i) => [p, i]));

  const nodes: MtNode[] = [];
  const byId = new Map<string, MtNode>();
  for (const node of graph.nodes) {
    const row = node.kind === "row" ? rowById.get(node.id) : undefined;
    // A graph row with no dataset row (a tenant whose data diverged) stays as unscored context.
    const kind = node.kind === "row" && !row ? "context" : node.kind;
    const mt: MtNode = {
      id: node.id,
      kind,
      label: row?.name ?? node.label ?? node.id,
      detail: row?.detail ?? node.type ?? "",
      country: node.group ?? node.type ?? "",
      person: row ? row.person : extras.person_types.includes(node.type ?? ""),
      holder: row ?? null,
      x: 0,
      y: 0,
      r: 2,
      volume: 0,
    };
    nodes.push(mt);
    byId.set(node.id, mt);
  }

  const flows: MtFlow[] = [];
  const adjacency = new Map<string, MtFlow[]>();
  const banksOf = new Map<string, MtNode[]>();
  let maxWeight = 1;
  for (const edge of graph.edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    if (edge.kind === extras.holding_edge_kind) {
      banksOf.set(source.id, [...(banksOf.get(source.id) ?? []), target]);
      continue;
    }
    if (!flowKinds.has(edge.kind)) continue;
    const hours = new Float32Array(graph.periods.length);
    for (const [period, usd] of Object.entries(edge.series)) {
      const i = periodIndex.get(period);
      if (i !== undefined) hours[i] = usd;
    }
    const flow: MtFlow = { index: flows.length, source, target, kind: edge.kind, weight: edge.weight, currency: edge.label ?? null, hours };
    flows.push(flow);
    source.volume += edge.weight;
    target.volume += edge.weight;
    maxWeight = Math.max(maxWeight, edge.weight);
    for (const id of [source.id, target.id]) adjacency.set(id, [...(adjacency.get(id) ?? []), flow]);
  }

  const hourlyUsd = new Float64Array(graph.periods.length);
  const hourlyCount = new Int32Array(graph.periods.length);
  const flowsByHour: number[][] = graph.periods.map(() => []);
  for (const flow of flows) {
    flow.hours.forEach((usd, h) => {
      if (usd > 0) {
        hourlyUsd[h] += usd;
        hourlyCount[h] += 1;
        flowsByHour[h].push(flow.index);
      }
    });
  }
  flowsByHour.forEach((list, h) => list.sort((a, b) => flows[b].hours[h] - flows[a].hours[h]));

  const caseMembers = new Map<string, string[]>();
  for (const node of nodes) {
    const id = node.holder?.caseId;
    if (id) caseMembers.set(id, [...(caseMembers.get(id) ?? []), node.id]);
  }
  const used = new Set(nodes.map((n) => n.country));
  const countries = extras.countries.map((c) => c.key).filter((k) => used.has(k));
  return { nodes, byId, flows, periods: graph.periods, hourlyUsd, hourlyCount, flowsByHour, adjacency, banksOf, caseMembers, countries, maxWeight };
}

// ── whole-dataset statistics ────────────────────────────────────────────────

export type Backtest = { n: number; positives: number; flagged: number; caught: number; perTier: number[]; perTierPositives: number[] };

export function backtest(rows: HolderRow[], nTiers: number, flagTier: number): Backtest {
  const perTier = new Array<number>(nTiers).fill(0);
  const perTierPositives = new Array<number>(nTiers).fill(0);
  let positives = 0;
  let flagged = 0;
  let caught = 0;
  for (const r of rows) {
    perTier[r.tier] += 1;
    perTierPositives[r.tier] += r.actual;
    positives += r.actual;
    if (r.tier >= flagTier) {
      flagged += 1;
      caught += r.actual;
    }
  }
  return { n: rows.length, positives, flagged, caught, perTier, perTierPositives };
}

export type TypologyStat = { name: string; holders: number; caught: number; attempts: number };

/** Per laundering typology: how many holders belong to it and how many the model flags. */
export function typologyStats(rows: HolderRow[], flagTier: number): TypologyStat[] {
  const stats = new Map<string, { holders: number; caught: number; attempts: Set<string> }>();
  for (const r of rows) {
    if (!r.typology) continue;
    const s = stats.get(r.typology) ?? { holders: 0, caught: 0, attempts: new Set<string>() };
    s.holders += 1;
    if (r.tier >= flagTier) s.caught += 1;
    if (r.caseId) s.attempts.add(r.caseId);
    stats.set(r.typology, s);
  }
  return [...stats].map(([name, s]) => ({ name, holders: s.holders, caught: s.caught, attempts: s.attempts.size }));
}

/** Percentile (0-100) of `value` among the sorted peers' values. */
export function percentileIn(sorted: number[], value: number): number {
  if (sorted.length === 0) return 0;
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= value) lo = mid + 1;
    else hi = mid;
  }
  return (lo / sorted.length) * 100;
}

/** Sorted values of every `facts` feature across all rows, for percentile lookups. */
export function factColumns(rows: HolderRow[], facts: string[]): Map<string, number[]> {
  return new Map(facts.map((f) => [f, rows.map((r) => Number(r.record[f])).filter(Number.isFinite).sort((a, b) => a - b)]));
}

/** A holder's counterparties on the map: per (other node, direction) the USD and formats. */
export type Counterparty = { node: MtNode; direction: "out" | "in"; usd: number; kinds: Set<string>; first: number; last: number };

export function counterparties(model: Model, id: string): Counterparty[] {
  const out = new Map<string, Counterparty>();
  for (const flow of model.adjacency.get(id) ?? []) {
    const direction = flow.source.id === id ? "out" : "in";
    const other = direction === "out" ? flow.target : flow.source;
    const key = `${direction}:${other.id}`;
    const c = out.get(key) ?? { node: other, direction, usd: 0, kinds: new Set<string>(), first: Infinity, last: -1 };
    c.usd += flow.weight;
    c.kinds.add(flow.kind);
    flow.hours.forEach((v, h) => {
      if (v > 0) {
        c.first = Math.min(c.first, h);
        c.last = Math.max(c.last, h);
      }
    });
    out.set(key, c);
  }
  return [...out.values()].sort((a, b) => b.usd - a.usd);
}

/** A holder's USD per hour, split into what it sent and what it received. */
export function hourlySeries(model: Model, id: string): { out: Float64Array; in: Float64Array } {
  const series = { out: new Float64Array(model.periods.length), in: new Float64Array(model.periods.length) };
  for (const flow of model.adjacency.get(id) ?? []) {
    const side = flow.source.id === id ? series.out : series.in;
    flow.hours.forEach((v, h) => {
      side[h] += v;
    });
  }
  return series;
}

/** The same scored network as a force-directed graph: a `network_explorer` config derived
 * from the money trail's own (see NetworkExplorerExtra's client-side fields) — payment
 * formats merged into one `payment` edge per pair, banks as the entities, holders outside
 * the consortium as context, the countries as communities. */
export function networkExplorerExtras(x: MoneyTrailExtra): NetworkExplorerExtra {
  const noun = x.entity_noun;
  return {
    kind: "network_explorer",
    tab_label: "Network",
    title: x.title,
    subtitle:
      `Every glowing node is a ${noun} — a person or a company — coloured by how closely its behaviour resembles the ${noun}s that are ${x.outcome_label.toLowerCase()}, ` +
      `scored by a model that never saw its own label. Lines are payments between ${noun}s; hexagons are the consortium's member banks, ` +
      `joined to the ${noun}s that hold accounts there. Pick a ${noun}, trace a path between two, replay the payments hour by hour — then reveal the ground truth.`,
    entity_noun: noun,
    name_column: x.name_column,
    detail_columns: x.detail_columns,
    size_feature: x.size_feature,
    tiers: x.tiers,
    pillars: x.pillars,
    facts: x.facts,
    outcome_label: x.outcome_label,
    flag_from_tier: x.flag_from_tier,
    flow_edge_kind: "payment",
    link_noun: x.amount_units,
    group_label: "Country",
    entity_label: "member bank",
    context_label: `${noun} outside the consortium`,
    events: [],
    tie_features: null,
    disclaimer:
      `${x.disclaimer ?? ""} This graph is a case slice built around laundering attempts: the counts and precision shown in it describe the ${noun}s drawn — the Map view's backtest covers every ${noun}.`.trim(),
    flow_edge_kinds: x.flows.map((f) => f.kind),
    flow_kind_labels: Object.fromEntries(x.flows.map((f) => [f.kind, f.label])),
    graph_rows_only: true,
    replay_tick_ms: 260,
    record_label: "ground truth",
  };
}
