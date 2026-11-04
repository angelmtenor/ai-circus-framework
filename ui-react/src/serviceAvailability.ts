/**
 * Which backend a scenario needs, and whether it is running — so the gallery can gray out
 * a scenario whose service is scaled to 0 (`make k3s-demo`, `k3s-all-lite`, an overlay that
 * was never deployed) instead of letting the reader open it and hit "Failed to fetch".
 *
 * The probe is each service's own liveness route (`GET /healthz`, no auth, touches no
 * dependency) through Traefik — not data-platform-manager's `/platform/status`, which is
 * admin-only and itself scaled to 0 in demo mode. A stopped or undeployed service has no
 * Traefik backend, so the request fails (503/404 without CORS headers → a fetch error):
 * any non-2xx or network error reads as "down". One shared poll, cached for every caller.
 */
import { useEffect, useState } from "react";
import { modelsStatus, type ScenarioSummary } from "./apiClient";
import { config } from "./config";

export type ServiceKey = "prediction" | "assistant" | "rag-agent" | "form-agent" | "dl-inference" | "data-api";
export type Availability = "up" | "down" | "checking";

// `control`: its name in data-platform-manager's OPTIONAL_SERVICES (core/workloads.py), when
// an admin may start it from the UI; the core services can only be scaled back from a shell.
type ServiceInfo = { label: string; url: () => string; startHint: string; control?: string };

const scaleUp = (deployment: string) => `kubectl -n ai-circus scale deployment/${deployment} --replicas=1`;

export const SERVICES: Record<ServiceKey, ServiceInfo> = {
  prediction: { label: "Prediction service", url: () => config.predictionUrl, startHint: scaleUp("prediction") },
  assistant: { label: "Assistant", url: () => config.assistantUrl, startHint: scaleUp("assistant") },
  "rag-agent": { label: "RAG agent", url: () => config.ragAgentUrl, startHint: scaleUp("rag-agent") },
  "form-agent": { label: "Form agent", url: () => config.formAgentUrl, startHint: scaleUp("form-agent") },
  "dl-inference": {
    label: "Deep learning inference",
    url: () => config.dlInferenceUrl,
    startHint: "make k3s-dl-build k3s-dl-up",
    control: "dl-inference",
  },
  "data-api": { label: "Data API", url: () => config.dataApiUrl, startHint: "make k3s-data-core-up" },
};

/** `required`: the scenario cannot open without it. `optional`: one part (the chat) degrades. */
export function scenarioServices(scenario: Pick<ScenarioSummary, "kind">): { required: ServiceKey[]; optional: ServiceKey[] } {
  switch (scenario.kind) {
    case "tabular_ml":
      return { required: ["prediction"], optional: ["assistant"] };
    case "conversational_rag":
      return { required: ["rag-agent"], optional: [] };
    case "assisted_form":
      return { required: ["form-agent"], optional: [] };
    case "deep_learning":
      return { required: ["dl-inference"], optional: [] };
    case "data_product":
      return { required: ["data-api"], optional: ["assistant"] };
    default:
      return { required: [], optional: [] };
  }
}

const POLL_MS = 30_000;
const TIMEOUT_MS = 4_000;

const states = new Map<ServiceKey, Availability>();
const listeners = new Set<() => void>();
const watched = new Map<ServiceKey, number>(); // service -> how many mounted hooks want it
let timer: number | undefined;
let inFlight = false;
let pollAgain = false;

async function probe(key: ServiceKey): Promise<Availability> {
  try {
    const response = await fetch(`${SERVICES[key].url()}/healthz`, {
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });
    return response.ok ? "up" : "down";
  } catch {
    return "down";
  }
}

async function pollOnce(): Promise<void> {
  if (document.hidden || watched.size === 0) return;
  if (inFlight) {
    pollAgain = true; // a service started being watched mid-poll: probe it right after
    return;
  }
  inFlight = true;
  try {
    const keys = [...watched.keys()];
    const results = await Promise.all(keys.map(probe));
    let changed = false;
    keys.forEach((key, i) => {
      if (states.get(key) !== results[i]) {
        states.set(key, results[i]);
        changed = true;
      }
    });
    if (changed) listeners.forEach((notify) => notify());
  } finally {
    inFlight = false;
    if (pollAgain) {
      pollAgain = false;
      void pollOnce();
    }
  }
}

function ensurePolling(): void {
  if (timer === undefined && watched.size > 0) timer = window.setInterval(() => void pollOnce(), POLL_MS);
  if (timer !== undefined && watched.size === 0) {
    window.clearInterval(timer);
    timer = undefined;
  }
}

/** Live state of each service in `keys` (polled every 30 s while the page is visible). */
export function useServiceAvailability(keys: readonly ServiceKey[]): ReadonlyMap<ServiceKey, Availability> {
  const signature = [...new Set(keys)].sort().join(",");
  const [, setVersion] = useState(0);

  useEffect(() => {
    const wanted = signature ? (signature.split(",") as ServiceKey[]) : [];
    const notify = () => setVersion((v) => v + 1);
    listeners.add(notify);
    const fresh = wanted.filter((key) => !states.has(key));
    for (const key of wanted) watched.set(key, (watched.get(key) ?? 0) + 1);
    ensurePolling();
    if (fresh.length) void pollOnce();
    const onVisible = () => {
      if (!document.hidden) void pollOnce();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      listeners.delete(notify);
      for (const key of wanted) {
        const left = (watched.get(key) ?? 1) - 1;
        if (left > 0) watched.set(key, left);
        else watched.delete(key);
      }
      ensurePolling();
    };
  }, [signature]);

  const view = new Map<ServiceKey, Availability>();
  for (const key of keys) view.set(key, states.get(key) ?? "checking");
  return view;
}

/** Re-probe now (e.g. right after an admin pressed Start on a service). */
export function refreshServiceAvailability(): void {
  void pollOnce();
}

/**
 * Slugs of tabular scenarios whose model is not trained for the caller's tenant (a fresh cluster
 * before `make k3s-pipeline`). One request for all of them to prediction's org-scoped
 * `/models/status`, re-asked every 30 s while the page is visible; empty while the service is
 * down or the answer has not arrived (never blocks a card on a failed check).
 */
export function useUntrainedScenarios(
  slugs: readonly string[],
  predictionUp: boolean,
  accessToken: string | null,
): ReadonlySet<string> {
  const signature = [...slugs].sort().join(",");
  const [untrained, setUntrained] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    if (!signature || !predictionUp) return;
    let cancelled = false;
    const check = () => {
      if (document.hidden) return;
      modelsStatus(config.predictionUrl, signature.split(","), accessToken)
        .then((trained) => {
          if (cancelled) return;
          const next = Object.keys(trained).filter((slug) => !trained[slug]);
          setUntrained((prev) => (prev.size === next.length && next.every((s) => prev.has(s)) ? prev : new Set(next)));
        })
        .catch(() => undefined);
    };
    check();
    const timer = window.setInterval(check, POLL_MS);
    document.addEventListener("visibilitychange", check);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, [signature, predictionUp, accessToken]);

  return signature && predictionUp ? untrained : NO_SCENARIOS;
}

const NO_SCENARIOS: ReadonlySet<string> = new Set();

/** The required services that are down, in the order the scenario lists them. */
export function missingServices(scenario: Pick<ScenarioSummary, "kind">, availability: ReadonlyMap<ServiceKey, Availability>): ServiceKey[] {
  return scenarioServices(scenario).required.filter((key) => availability.get(key) === "down");
}
