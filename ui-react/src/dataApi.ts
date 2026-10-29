// data-api client — the governed door of every `data_product` scenario (records, metrics,
// governed SQL, datasets, pipeline events, simulator). Every call carries the caller's token
// and, for the platform admin demoing the access rules, an optional "view as" role (+ its
// row-filter values): data-api honours those two headers for platform identities only.

import { config } from "./config";

export type ViewAs = { role: string; values: string[] } | null;

export type DataAccess = {
  org_id: string;
  role: string;
  role_label: string;
  layers: string[];
  sql: boolean;
  row_filter: { column: string; values: string[] } | null;
  hidden: string[];
  viewing_as: boolean;
  can_simulate: boolean;
  fallback_data: boolean;
};

export type SimulatorParam = {
  name: string;
  label: string;
  type: "choice" | "int" | "bool";
  options: string[];
  default: string | number | boolean | null;
  min: number | null;
  max: number | null;
};

export type SimulatorAction = {
  name: string;
  label: string;
  description: string;
  params: SimulatorParam[];
  tone: "normal" | "warning";
};

export type DataSourceSpec = {
  id: string;
  type: "kafka" | "files" | "cdc";
  label: string;
  topic?: string;
  prefix?: string;
  lands_in?: string;
  tables?: { name: string; key: string; label: string }[];
  rules?: { name: string; description: string }[];
};

export type DatasetSpec = {
  name: string;
  layer: "silver" | "gold" | "ops";
  label: string;
  description: string;
  grain: string;
  time_column: string | null;
  freshness_minutes: number | null;
};

export type MetricSpec = {
  name: string;
  label: string;
  description: string;
  dataset: string;
  expression: string;
  format: "currency_cents" | "integer" | "number" | "percent";
  dimensions: string[];
};

/** The `data_product` block platform-registry serves in ScenarioSummary. */
export type DataProductConfig = {
  domain: string;
  sources: DataSourceSpec[];
  datasets: DatasetSpec[];
  metrics: MetricSpec[];
  records: { entity: string; label: string; source: string; summary: Record<string, string>; search: string[] }[];
  access: {
    row_filter: { column: string; claim: string; labels: { dataset: string; label: string } | null } | null;
    roles: Record<string, { label: string; rows: "all" | "filtered"; layers: string[]; hidden: string[]; sql: boolean }>;
    default_role: string;
    admin_role: string;
  };
  simulator: { actions: SimulatorAction[]; history_days: number } | null;
  stage_labels: Record<string, string>;
  hot_path: { name: string; label: string }[];
  kpis: string[];
  dashboard: DashboardPanel[];
  apps?: { storefront?: StorefrontApp | null; insights?: InsightsApp | null };
  ml_exports?: { dataset: string; scenario: string }[];
};

export type InsightsApp = {
  title: string;
  subtitle: string;
  heatmap: { title: string; metric: string; rows: string } | null;
  forecast: { title: string; model: string; latest_flag: string; row: string; column: string; baseline: string } | null;
};

export type StorefrontApp = {
  title: string;
  tagline: string;
  disclaimer: string;
  catalogue: string;
  fields: {
    id: string;
    name: string;
    price: string;
    group: string;
    brand: string | null;
    subtitle: string | null;
    rank: string | null;
    available: string | null;
    image_shape: string | null;
    image_colour: string | null;
    image_length: string | null;
    details: string[];
  };
  recommendations: string | null;
  rec_id: string;
  rec_evidence: string[];
  checkout_action: string;
  order_entity: string;
};

export type DashboardPanel = {
  title: string;
  metric: string;
  dims: string[];
  chart: "line" | "bar" | "hbar" | "pie" | "map";
  /** chart: map — further metrics the map can switch to, and the dataset that places it. */
  metrics?: string[];
  places?: MapPlaces | null;
  period: string | null;
  compare: boolean;
  limit: number;
  wide: boolean;
};

export type MapPlaces = {
  dataset: string;
  key: string;
  label: string;
  lat: string;
  lon: string;
  group: string | null;
  group_colours?: Record<string, string>;
};

/** A row-filter value with its readable name (store_id S012 -> "Acme Málaga"). */
export type RowFilterOption = { value: string; label: string | null };

export type HotSnapshot = {
  name: string;
  label: string;
  measures: string[];
  series: Record<string, number | string>[];
  today: Record<string, number | string | null>;
  today_by: Record<string, Record<string, number | string>[]>;
  approximate: boolean;
};

export type TransformRun = {
  run_id: string;
  target: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  watermark: string | null;
  engine: string;
  models_ok: number;
  tests_passed: number;
  tests_failed: number;
  message: string;
  curated_through: string | null;
};

export type SimulatorStatus = {
  available: boolean;
  live: boolean;
  live_until: string | null;
  live_produced: number;
  backfill: { state: string; days?: number; progress?: string[]; error?: string };
  last_actions: Record<string, unknown>[];
};

export type Overview = {
  access: DataAccess;
  roles: { name: string; label: string; rows: "all" | "filtered" }[];
  row_filter: { column: string; claim: string } | null;
  row_filter_options?: RowFilterOption[];
  hot: HotSnapshot | null;
  // Per kafka source: records in bronze, corrections absorbed, archived in the lake, latest arrival.
  capture?: Record<string, { records: number; corrections: number; in_lake: number; latest: string | null }>;
  ops: { quarantine?: { open: number; today: number }; deliveries?: Record<string, number> };
  transform: TransformRun | null;
  simulator: SimulatorStatus | null;
  tiers: Record<string, boolean>;
  marts_engine: string;
  now: string;
};

export type MetricRow = Record<string, string | number | null> & { value: number | null };
export type MetricResult = {
  metric: string;
  label: string;
  format: MetricSpec["format"];
  dimensions: string[];
  period: { name: string; from: string; to: string };
  total: number | null;
  rows: (MetricRow & { compare_value?: number | null; change_pct?: number | null })[];
  compare?: { name: string; from: string; to: string; total: number | null; change_pct: number | null };
};

export type DatasetInfo = DatasetSpec & {
  built: boolean;
  columns: string[];
  engine: string;
  rows?: number;
  latest?: string | null;
  restricted?: boolean;
  curated_through: string | null;
  freshness?: { age_minutes: number; ok: boolean };
};

export type RecordTrail = Partial<Record<"produced" | "event_log" | "validated" | "bronze" | "lake" | "gold", string | null>>;
export type RecordVersion = {
  version: number;
  payload: Record<string, unknown>;
  payload_hash: string;
  event_ts: string | null;
  received_at: string;
  source_kind: string;
  delivery: string | null;
  corrections: number;
  archived_at: string | null;
  summary: Record<string, unknown>;
  trail: RecordTrail;
};
export type RecordDetail = {
  entity: string;
  key: string;
  label: string;
  current: RecordVersion | null;
  versions: RecordVersion[];
  quarantined: { quarantine_id: number; violations: { rule: string; message: string }[]; received_at: string; status: string }[];
  curated_through: string | null;
};
export type RecordListItem = {
  key: string;
  version: number;
  event_ts: string | null;
  received_at: string;
  source_kind: string;
  summary: Record<string, unknown>;
};

export type QuarantineItem = {
  quarantine_id: number;
  source_id: string;
  business_key: string | null;
  payload: Record<string, unknown> | null;
  raw: string | null;
  violations: { rule: string; message: string }[];
  source_kind: string;
  delivery: string | null;
  received_at: string;
  status: string;
};

export type SqlResult = {
  columns: string[];
  rows: Record<string, unknown>[];
  truncated: boolean;
  engine: string;
  executed_sql: string;
};

export type PipelineEvent = {
  slug: string;
  stage: string;
  status: string;
  entity: string;
  record_id: string;
  source: string;
  ts: string;
  latency_ms: number | null;
  filter: string | null;
  detail: Record<string, unknown>;
};

export type LineageGraph = {
  nodes: { id: string; label: string; kind: string; layer: string; tests?: number; description?: string }[];
  edges: { from: string; to: string }[];
  generated_at: string | null;
};

function headers(accessToken: string | null, viewAs: ViewAs): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (accessToken) h.Authorization = `Bearer ${accessToken}`;
  if (viewAs) {
    h["X-Data-View-As"] = viewAs.role;
    if (viewAs.values.length) h["X-Data-View-As-Values"] = viewAs.values.join(",");
  }
  return h;
}

export class DataApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(
  slug: string,
  path: string,
  accessToken: string | null,
  viewAs: ViewAs,
  init?: { method?: string; body?: unknown; signal?: AbortSignal },
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${config.dataApiUrl}/v1/${slug}${path}`, {
      method: init?.method ?? "GET",
      headers: headers(accessToken, viewAs),
      body: init?.body === undefined ? undefined : JSON.stringify(init.body),
      signal: init?.signal,
    });
  } catch (exc) {
    if ((exc as Error).name === "AbortError") throw exc;
    throw new DataApiError(0, "The Data Platform is not reachable — is its core tier up? (make k3s-data-core-up)");
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new DataApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export type DataClient = ReturnType<typeof dataClient>;

/** One client per (scenario, token, view-as). */
export function dataClient(slug: string, accessToken: string | null, viewAs: ViewAs) {
  const get = <T,>(path: string, signal?: AbortSignal) => call<T>(slug, path, accessToken, viewAs, { signal });
  const post = <T,>(path: string, body: unknown) => call<T>(slug, path, accessToken, viewAs, { method: "POST", body });
  return {
    overview: () => get<Overview>("/overview"),
    hot: (name: string, minutes = 60) => get<HotSnapshot>(`/hot/${name}?minutes=${minutes}`),
    metric: (
      name: string,
      opts: { dims?: string[]; period?: string; compare?: string; filters?: string; limit?: number; order?: string } = {},
      signal?: AbortSignal,
    ) => {
      const q = new URLSearchParams();
      if (opts.dims?.length) q.set("dims", opts.dims.join(","));
      q.set("period", opts.period ?? "last_7_days");
      if (opts.compare) q.set("compare", opts.compare);
      if (opts.filters) q.set("filters", opts.filters);
      if (opts.limit) q.set("limit", String(opts.limit));
      if (opts.order) q.set("order", opts.order);
      return get<MetricResult>(`/metrics/${name}?${q}`, signal);
    },
    datasets: () => get<DatasetInfo[]>("/datasets"),
    datasetRows: (name: string, limit = 50, signal?: AbortSignal, where: Record<string, string> = {}) => {
      const q = new URLSearchParams({ limit: String(limit) });
      for (const [column, value] of Object.entries(where)) q.append("where", `${column}=${value}`);
      return get<{ dataset: string; columns: string[]; rows: Record<string, unknown>[] }>(`/datasets/${name}/rows?${q}`, signal);
    },
    records: (entity: string, q: string, limit = 25) =>
      get<RecordListItem[]>(`/records/${entity}?limit=${limit}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
    record: (entity: string, key: string) => get<RecordDetail>(`/records/${entity}/${encodeURIComponent(key)}`),
    query: (sql: string) => post<SqlResult>("/query", { sql }),
    quarantine: (status = "open") => get<QuarantineItem[]>(`/quarantine?status=${status}`),
    resolveQuarantine: (id: number, body: { payload?: Record<string, unknown> | null; discard?: boolean }) =>
      post<{ quarantine_id: number; status: string }>(`/quarantine/${id}`, body),
    simulate: (action: string, params: Record<string, string | number | boolean | null | { id: string; qty: number }[]>) =>
      post<Record<string, unknown>>(`/simulator/actions/${action}`, { params }),
    backfill: (days?: number) => post<SimulatorStatus["backfill"]>("/simulator/backfill", days ? { days } : {}),
    live: (on: boolean, minutes = 30) => post<SimulatorStatus>("/simulator/live", { on, minutes }),
    events: () => get<PipelineEvent[]>("/events?limit=200"),
    lineage: () => get<LineageGraph>("/lineage"),
    transformRuns: (limit = 15) => get<(TransformRun & { models_failed: number; failures: { node: string; status: string; message: string }[] })[]>(`/transform/runs?limit=${limit}`),
    /** Server-sent pipeline events over fetch (an EventSource can't send the Authorization header). */
    async stream(onEvent: (event: PipelineEvent) => void, signal: AbortSignal): Promise<void> {
      const response = await fetch(`${config.dataApiUrl}/v1/${slug}/events/stream`, {
        headers: headers(accessToken, viewAs),
        signal,
      });
      if (!response.ok || !response.body) throw new DataApiError(response.status, "event stream unavailable");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        buffer += decoder.decode(value, { stream: true });
        let cut = buffer.indexOf("\n\n");
        while (cut >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          for (const line of chunk.split("\n")) {
            if (line.startsWith("data: ")) {
              try {
                onEvent(JSON.parse(line.slice(6)) as PipelineEvent);
              } catch {
                /* a malformed event is skipped */
              }
            }
          }
          cut = buffer.indexOf("\n\n");
        }
      }
    },
  };
}

export function formatMetric(value: number | null | undefined, format: MetricSpec["format"] | string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (format === "currency_cents") {
    const euros = value / 100;
    if (Math.abs(euros) >= 1_000_000) return `€${(euros / 1_000_000).toFixed(2)}M`;
    if (Math.abs(euros) >= 10_000) return `€${(euros / 1000).toFixed(1)}k`;
    return `€${euros.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
  }
  if (format === "percent") return `${value.toFixed(1)}%`;
  if (format === "integer") return Math.round(value).toLocaleString();
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function changeLabel(pct: number | null | undefined): { text: string; tone: "up" | "down" | "flat" } {
  if (pct === null || pct === undefined) return { text: "no comparison", tone: "flat" };
  const tone = pct > 0.5 ? "up" : pct < -0.5 ? "down" : "flat";
  return { text: `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`, tone };
}

export const FLOW_STAGES = ["produced", "event_log", "validated", "bronze", "hot", "lake", "transformed", "gold", "served"] as const;
export type FlowStage = (typeof FLOW_STAGES)[number];

/** "S012 · Acme Málaga" for a row-filter value, or the bare value when no name is known. */
export function optionLabel(value: string, options: RowFilterOption[] | undefined): string {
  const label = options?.find((o) => o.value === value)?.label;
  return label ? `${value} · ${label}` : value;
}
