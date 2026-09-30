/**
 * The Investigate view's data model — pure functions, no React, no DOM. Given a
 * scenario's network (networkModel.ts) it answers, for one subject: who they
 * exchange flow with and month by month, how far the nearest "targets" (e.g. persons
 * of interest) are and by which route, and how exposed they are next to their peers.
 * A subject may also be *hypothetical* — a new individual with chosen ties — which is
 * why everything takes a `Subject` (contacts) rather than a node id. See
 * NetworkInvestigatorView.tsx and networkInvestigatorScene.ts.
 */
import type { NetworkTieFeatures } from "./apiClient";
import { linkBetween, shortestPath, type Network, type NxNode } from "./networkModel";
import type { Record_ } from "./predictUtils";

export const VIRTUAL_ID = "__new__";

/** The analyst's hypothetical individual — kept by the Network tab so switching views
 * doesn't lose it. `record` overrides the feature defaults; `manual` lists the
 * tie-derived features the analyst set by hand instead. Never sent anywhere but to be scored. */
export type Draft = { active: boolean; name: string; record: Record_; manual: string[]; contacts: DraftContact[] };
export const EMPTY_DRAFT: Draft = { active: false, name: "", record: {}, manual: [], contacts: [] };

export type Contact = {
  node: NxNode;
  sent: number; // flow from the subject to this node
  received: number;
  total: number;
  sentSeries: Float32Array | null; // aligned with Network.periods
  receivedSeries: Float32Array | null;
};

export type Subject = {
  id: string;
  label: string;
  detail: string;
  virtual: boolean;
  node: NxNode | null; // null for a hypothetical individual
  probability: number | null;
  tier: number | null;
  contacts: Contact[]; // strongest first
  relations: NxNode[]; // documented relations (entities), drawn beside the contacts
};

/** Every flow contact of a real node, both directions summed per contact. */
export function contactsOf(net: Network, id: string): Contact[] {
  const n = net.periods.length;
  const byId = new Map<string, Contact>();
  for (const { node, link } of net.adjacency.get(id) ?? []) {
    if (!link.flow) continue;
    const c =
      byId.get(node.id) ??
      ({ node, sent: 0, received: 0, total: 0, sentSeries: n ? new Float32Array(n) : null, receivedSeries: n ? new Float32Array(n) : null } as Contact);
    const outgoing = link.source.id === id;
    if (outgoing) c.sent += link.weight;
    else c.received += link.weight;
    c.total += link.weight;
    const target = outgoing ? c.sentSeries : c.receivedSeries;
    if (target && link.series) for (let i = 0; i < n; i++) target[i] += link.series[i];
    byId.set(node.id, c);
  }
  return [...byId.values()].sort((a, b) => b.total - a.total);
}

export function realSubject(net: Network, node: NxNode): Subject {
  return {
    id: node.id,
    label: node.label,
    detail: node.detail,
    virtual: false,
    node,
    probability: node.row?.probability ?? null,
    tier: node.row?.tier ?? null,
    contacts: contactsOf(net, node.id),
    relations: (net.adjacency.get(node.id) ?? []).filter(({ link }) => !link.flow).map(({ node: other }) => other),
  };
}

export type DraftContact = { id: string; volume: number };

/** A new individual's ties: each chosen volume is exchanged half each way, with no
 * monthly series (nothing was sent yet). */
export function hypotheticalSubject(net: Network, name: string, contacts: DraftContact[], score: { probability: number; tier: number } | null): Subject {
  const list: Contact[] = [];
  for (const { id, volume } of contacts) {
    const node = net.byId.get(id);
    if (!node) continue;
    list.push({ node, sent: volume / 2, received: volume / 2, total: volume, sentSeries: null, receivedSeries: null });
  }
  list.sort((a, b) => b.total - a.total);
  return {
    id: VIRTUAL_ID,
    label: name.trim() || "New individual",
    detail: "hypothetical",
    virtual: true,
    node: null,
    probability: score?.probability ?? null,
    tier: score?.tier ?? null,
    contacts: list,
    relations: [],
  };
}

/** Flow volume per period over a subject's contacts, all of it and the part with `isTarget` contacts. */
export function monthlyVolume(net: Network, subject: Subject, isTarget: (n: NxNode) => boolean): { all: Float32Array; target: Float32Array } {
  const n = net.periods.length;
  const all = new Float32Array(n);
  const target = new Float32Array(n);
  for (const c of subject.contacts) {
    for (let i = 0; i < n; i++) {
      const v = (c.sentSeries?.[i] ?? 0) + (c.receivedSeries?.[i] ?? 0);
      all[i] += v;
      if (isTarget(c.node)) target[i] += v;
    }
  }
  return { all, target };
}

/** Hops from `starts` to every node over every link (e-mail ties and relations alike),
 * with each start at its given offset. */
export function bfsHops(net: Network, starts: { id: string; hops: number }[]): Map<string, number> {
  const dist = new Map<string, number>();
  for (const { id, hops } of [...starts].sort((a, b) => a.hops - b.hops)) if (!dist.has(id)) dist.set(id, hops);
  const queue = [...dist.keys()];
  let head = 0;
  while (head < queue.length) {
    const id = queue[head++];
    const d = dist.get(id)!;
    for (const { node } of net.adjacency.get(id) ?? []) {
      if (!dist.has(node.id)) {
        dist.set(node.id, d + 1);
        queue.push(node.id);
      }
    }
  }
  return dist;
}

export type Route = { target: NxNode; hops: number; path: NxNode[] };

/** The nearest `limit` targets of a subject, each with its shortest route (the subject
 * itself first). A hypothetical subject routes through its contacts. */
export function nearestTargets(net: Network, subject: Subject, isTarget: (n: NxNode) => boolean, limit = 6): Route[] {
  const starts = subject.virtual ? subject.contacts.map((c) => ({ id: c.node.id, hops: 1 })) : [{ id: subject.id, hops: 0 }];
  const dist = bfsHops(net, starts);
  const candidates = net.rows
    .filter((n) => n.id !== subject.id && isTarget(n) && dist.has(n.id))
    .sort((a, b) => dist.get(a.id)! - dist.get(b.id)! || (b.row?.probability ?? 0) - (a.row?.probability ?? 0))
    .slice(0, limit);
  const routes: Route[] = [];
  for (const target of candidates) {
    let path: NxNode[] | null = null;
    if (!subject.virtual) path = shortestPath(net, subject.id, target.id, true);
    else {
      for (const c of subject.contacts) {
        const via = c.node.id === target.id ? [target] : shortestPath(net, c.node.id, target.id, true);
        if (via && (!path || via.length < path.length)) path = via;
      }
    }
    if (path) routes.push({ target, hops: subject.virtual ? path.length : path.length - 1, path });
  }
  return routes;
}

export type Exposure = {
  contacts: number; // distinct flow contacts
  targetContacts: number; // of which targets
  targetShare: number; // share of the subject's flow volume exchanged with targets
  nearest: number | null; // hops to the nearest target (null = none reachable)
  within2: number; // targets within two hops
};

export function exposureOf(net: Network, subject: Subject, isTarget: (n: NxNode) => boolean): Exposure {
  const starts = subject.virtual ? subject.contacts.map((c) => ({ id: c.node.id, hops: 1 })) : [{ id: subject.id, hops: 0 }];
  const dist = bfsHops(net, starts);
  let volume = 0;
  let targetVolume = 0;
  let targetContacts = 0;
  for (const c of subject.contacts) {
    volume += c.total;
    if (isTarget(c.node)) {
      targetVolume += c.total;
      targetContacts += 1;
    }
  }
  let nearest: number | null = null;
  let within2 = 0;
  for (const n of net.rows) {
    if (n.id === subject.id || !isTarget(n)) continue;
    const d = dist.get(n.id);
    if (d === undefined) continue;
    nearest = nearest === null ? d : Math.min(nearest, d);
    if (d <= 2) within2 += 1;
  }
  return { contacts: subject.contacts.length, targetContacts, targetShare: volume ? targetVolume / volume : 0, nearest, within2 };
}

/** The exposure of every scored row with a flow contact — the peer group a subject is
 * ranked against (sorted ascending by target share). */
export function peerShares(net: Network, isTarget: (n: NxNode) => boolean): number[] {
  const shares: number[] = [];
  for (const node of net.rows) {
    if (node.degree === 0) continue;
    const contacts = contactsOf(net, node.id);
    let volume = 0;
    let targetVolume = 0;
    for (const c of contacts) {
      volume += c.total;
      if (isTarget(c.node) && c.node.id !== node.id) targetVolume += c.total;
    }
    if (volume) shares.push(targetVolume / volume);
  }
  return shares.sort((a, b) => a - b);
}

// ── a new individual ───────────────────────────────────────────────────────

export type TieDerived = Record<string, number>;

/** The features a new individual's ties determine (see NetworkTieFeatures): the
 * contact count, the volumes, the exact share of their contacts who are also tied to
 * each other, and a rough centrality — the volume-weighted mean of their contacts' own
 * (people are as central as whom they talk to). */
export function tieDerivedFeatures(net: Network, contacts: DraftContact[], map: NetworkTieFeatures, sourceRecords: Map<string, Record_>): TieDerived {
  const out: TieDerived = {};
  const ids = contacts.map((c) => c.id).filter((id) => net.byId.has(id));
  const volume = contacts.reduce((s, c) => s + c.volume, 0);
  if (map.contacts) out[map.contacts] = ids.length;
  if (map.sent) out[map.sent] = Math.round(volume / 2);
  if (map.received) out[map.received] = Math.round(volume / 2);
  if (map.clustering) {
    let pairs = 0;
    let tied = 0;
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        pairs += 1;
        const link = linkBetween(net, ids[i], ids[j]);
        if (link?.flow) tied += 1;
      }
    }
    out[map.clustering] = pairs ? Number((tied / pairs).toFixed(3)) : 0;
  }
  if (map.centrality) {
    let weight = 0;
    let sum = 0;
    for (const c of contacts) {
      const record = sourceRecords.get(c.id);
      const v = record ? Number(record[map.centrality]) : NaN;
      if (Number.isFinite(v)) {
        sum += v * c.volume;
        weight += c.volume;
      }
    }
    out[map.centrality] = weight ? Number((sum / weight).toFixed(1)) : 0; // 0 = not in the network at all
  }
  return out;
}

/** Peer percentile of `value` within an ascending-sorted sample (share at or below). */
export function rankShare(sorted: number[], value: number): number {
  if (!sorted.length) return 0;
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sorted[mid] <= value) lo = mid + 1;
    else hi = mid;
  }
  return lo / sorted.length;
}

// ── the stage layout ───────────────────────────────────────────────────────

export type EgoNode = {
  id: string;
  node: NxNode | null; // null = the hypothetical subject
  x: number; // unit ellipse, subject at 0,0
  y: number;
  ring: 0 | 1 | 2;
  contact: Contact | null;
  subject: boolean;
};

export type EgoEdge = {
  a: string;
  b: string;
  kind: "tie" | "relation" | "route" | "mutual";
  weight: number;
  contact: Contact | null; // for a tie: the subject↔contact totals (direction = subject→contact = sent)
  cite?: string;
};

export type EgoLayout = { nodes: EgoNode[]; edges: EgoEdge[]; byId: Map<string, EgoNode>; maxTie: number };

const TAU = Math.PI * 2;

/** Radial layout: the subject at the centre, their strongest contacts (closer = stronger
 * tie) and documented relations around them, and the routes to the nearest targets
 * fanning out beyond, each leaving from the contact it goes through. */
export function layoutEgo(subject: Subject, routes: Route[], isTarget: (n: NxNode) => boolean, net: Network, maxContacts = 16): EgoLayout {
  const nodes: EgoNode[] = [{ id: subject.id, node: subject.node, x: 0, y: 0, ring: 0, contact: null, subject: true }];
  const byId = new Map<string, EgoNode>([[subject.id, nodes[0]]]);
  const edges: EgoEdge[] = [];
  const angleOf = new Map<string, number>();

  // Ring 1: the strongest contacts, every target contact, the documented relations and
  // the first step of every route.
  const ring1: { node: NxNode; contact: Contact | null; relation: boolean }[] = [];
  const seen = new Set<string>([subject.id]);
  const push = (node: NxNode, contact: Contact | null, relation: boolean) => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    ring1.push({ node, contact, relation });
  };
  subject.contacts.filter((c) => isTarget(c.node)).forEach((c) => push(c.node, c, false));
  subject.contacts.slice(0, maxContacts).forEach((c) => push(c.node, c, false));
  subject.relations.forEach((n) => push(n, null, true));
  const contactById = new Map(subject.contacts.map((c) => [c.node.id, c]));
  for (const route of routes) {
    const first = route.path[subject.virtual ? 0 : 1];
    if (first && first.id !== route.target.id) push(first, contactById.get(first.id) ?? null, false);
  }
  const limited = ring1.slice(0, maxContacts + 10);

  // Neighbours sit next to their own community and tier, so the ring reads in clumps.
  limited.sort((a, b) => a.node.groupIndex - b.node.groupIndex || (b.node.row?.tier ?? -1) - (a.node.row?.tier ?? -1) || a.node.label.localeCompare(b.node.label));
  const maxTie = Math.max(1, ...subject.contacts.map((c) => c.total));
  limited.forEach((item, i) => {
    const angle = -Math.PI / 2 + (i / limited.length) * TAU;
    const strength = item.contact ? Math.log1p(item.contact.total) / Math.log1p(maxTie) : 0.25;
    const radius = 0.56 - 0.2 * strength; // stronger ties sit closer
    const node: EgoNode = { id: item.node.id, node: item.node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, ring: 1, contact: item.contact, subject: false };
    nodes.push(node);
    byId.set(node.id, node);
    angleOf.set(node.id, angle);
    edges.push({ a: subject.id, b: node.id, kind: item.relation ? "relation" : "tie", weight: item.contact?.total ?? 0, contact: item.contact });
  });

  // Ring 2: each route's remaining hops, fanned out from the ring-1 node it leaves by.
  const leaving = new Map<string, number>();
  routes.forEach((route) => {
    const path = subject.virtual ? [{ id: subject.id } as NxNode, ...route.path] : route.path;
    const bridge = path[1];
    if (!bridge) return;
    const slot = leaving.get(bridge.id) ?? 0;
    leaving.set(bridge.id, slot + 1);
    const base = angleOf.get(bridge.id);
    if (base === undefined) return;
    const beyond = path.length - 2; // nodes after the bridge, the target last
    for (let k = 1; k < path.length - 1; k++) {
      const node = path[k + 1];
      if (!node || byId.has(node.id)) continue;
      const radius = 0.74 + 0.2 * (beyond <= 1 ? 1 : (k - 1) / (beyond - 1));
      const angle = base + (slot - 0.5) * 0.3;
      const egoNode: EgoNode = { id: node.id, node, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, ring: 2, contact: null, subject: false };
      nodes.push(egoNode);
      byId.set(node.id, egoNode);
    }
    for (let k = 1; k < path.length - 1; k++) {
      const a = path[k].id;
      const b = path[k + 1].id;
      if (!byId.has(a) || !byId.has(b)) continue;
      if (edges.some((e) => (e.a === a && e.b === b) || (e.a === b && e.b === a))) continue;
      const link = linkBetween(net, a, b);
      edges.push({ a, b, kind: "route", weight: link?.flow ? link.weight : 0, contact: null, cite: link?.citation });
    }
  });

  // Mutual ties between the ring-1 contacts (a closed circle shows as a web).
  const ringIds = nodes.filter((n) => n.ring === 1 && n.node && n.node.kind !== "context").map((n) => n.id);
  for (let i = 0; i < ringIds.length; i++) {
    for (let j = i + 1; j < ringIds.length; j++) {
      const link = linkBetween(net, ringIds[i], ringIds[j]);
      if (link?.flow) edges.push({ a: ringIds[i], b: ringIds[j], kind: "mutual", weight: link.weight, contact: null });
    }
  }
  return { nodes, edges, byId, maxTie };
}
