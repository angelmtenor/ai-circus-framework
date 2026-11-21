// The architecture catalog as the app reads it: generated from libs/shared/src/ai_circus_shared/
// architecture_catalog.yaml by scripts/generate_architecture.py (a test fails when it drifts). Never edit the JSON.
import data from "./generated/architecture.json";

export type ArchLayerId = "gateway" | "consumption" | "data" | "infra" | "observability";
export type ArchLayer = { id: ArchLayerId; title: string; text: string };
export type ArchComponent = {
  id: string;
  name: string;
  layer: ArchLayerId;
  kind: string;
  tech: string;
  role: string;
  profile: "core" | "optional" | "external";
  enable: string | null;
  source: string | null;
  workloads: string[];
  adr: number[];
  talks_to: { to: string; label: string }[];
};
export type ArchFlow = { id: string; title: string; text: string; steps: { from: string; to: string; label: string }[] };
export type ArchCatalog = { layers: ArchLayer[]; components: ArchComponent[]; flows: ArchFlow[]; adr_files: Record<string, string> };

export const CATALOG = data as ArchCatalog;
export const COMPONENTS = new Map(CATALOG.components.map((c) => [c.id, c]));
// The stack as it is built, top to bottom on screen: the doors, what people use, the data, the cluster.
export const STACK: ArchLayerId[] = ["gateway", "consumption", "data", "infra"];
export const REPO_DOCS = "https://github.com/angelmtenor/ai-circus-framework/blob/develop/docs/";

/** Every component a flow touches, in order of first appearance, with its first step number. */
export function flowOrder(flow: ArchFlow): Map<string, number> {
  const order = new Map<string, number>();
  flow.steps.forEach((step, i) => {
    for (const id of [step.from, step.to]) if (!order.has(id)) order.set(id, i + 1);
  });
  return order;
}

/** The components a component talks to, or that talk to it. */
export function linkedTo(id: string): Set<string> {
  const out = new Set<string>();
  for (const c of CATALOG.components) {
    if (c.id === id) c.talks_to.forEach((l) => out.add(l.to));
    else if (c.talks_to.some((l) => l.to === id)) out.add(c.id);
  }
  return out;
}
