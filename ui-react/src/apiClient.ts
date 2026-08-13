/**
 * Thin fetch-based clients for the backend services.
 */

export type NumericFeatureSpec = {
  type: "numeric";
  min: number;
  max: number;
  default: number;
  step?: number;
};

export type CategoricalFeatureSpec = {
  type: "categorical";
  options: string[];
  default: string;
};

export type FeatureSpec = NumericFeatureSpec | CategoricalFeatureSpec;

// Mirrors libs/shared/src/ai_circus_shared/scenario_schema.py's ChartSpec — one chart
// in a scenario's default "Data" dashboard combination (see DataView.tsx/chartBuilder.ts).
export type ChartType = "histogram" | "bar" | "line" | "scatter" | "scatter3d" | "box" | "pie" | "heatmap";
export type ChartAgg = "count" | "sum" | "mean" | "min" | "max";
export type ChartSpec = {
  type: ChartType;
  x?: string | null;
  y?: string | null;
  z?: string | null;
  color_by?: string | null;
  agg?: ChartAgg;
};

export type ScenarioSummary = {
  slug: string;
  kind: string;
  title: string;
  description: string;
  icon: string;
  // Attribution for a ported public dataset (see scenario_schema.py's
  // DatasetCredits) — null for scenarios whose content is original.
  credits?: { source: string; url: string; note?: string | null } | null;
  feature_columns?: string[] | null;
  feature_schema?: Record<string, FeatureSpec> | null;
  // tabular_ml only — seeds the Data tab dashboard; empty/missing falls back to the
  // UI's own generic default (see DataView.tsx).
  default_charts?: ChartSpec[] | null;
  sample_questions: string[];
  // tabular_ml only — "regression" scenarios render a plain "value units"
  // prediction instead of the classification probability view.
  task_type?: string | null;
  target_units?: string | null;
  // tabular_ml only — the dataset column being predicted (not itself a feature).
  target?: string | null;
};

export type PredictionResult = {
  prediction: number;
  contributions: Record<string, number>;
  // Regression-only 90% prediction interval (LightGBM quantile models) — null for
  // classification scenarios or if a scenario has no interval models trained.
  prediction_lower: number | null;
  prediction_upper: number | null;
};

export type DatasetSample = {
  columns: string[];
  rows: Record<string, string | number | null>[];
  total_rows: number;
};

export type FeatureImportance = { feature: string; importance: number };
export type CategoryBreakdown = { category: string; score: number; n: number };

export type DatasetEvaluation = {
  task_type: string;
  target: string;
  n: number;
  metrics: Record<string, number>;
  breakdown_feature: string | null;
  breakdown: CategoryBreakdown[];
  actuals: number[];
  predictions: number[];
  prediction_lower: number[] | null;
  prediction_upper: number[] | null;
};

export type DatasetExplainability = {
  feature_importance: FeatureImportance[];
  sample_size: number;
};

export type LlmProvider = {
  provider: string;
  label: string;
  model_name: string;
  route_exists: boolean;
  model: string | null;
  api_base: string | null;
  needs_key: boolean;
  needs_base: boolean;
  env_vars: string[];
  hint: string;
};

export type LlmProviderTest = {
  ok: boolean;
  error: string | null;
  latency_ms: number | null;
  reply?: string | null;
};

function headers(accessToken: string | null): HeadersInit {
  return accessToken ? { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export async function listEntitledScenarios(
  baseUrl: string,
  orgId: string,
  accessToken: string | null,
): Promise<ScenarioSummary[]> {
  const response = await fetch(`${baseUrl}/entitlements/${orgId}`, { headers: headers(accessToken) });
  return asJson<ScenarioSummary[]>(response);
}

/**
 * Confirms an admin key is real before the login screen commits to it — cheap
 * fail-fast so a bad key doesn't sail through to the scenario picker only to error
 * on every scenario's own chat/predict call. `/entitlements/{org_id}` itself now also
 * enforces (server-side, see platform_registry.api.require_org_match) that the
 * caller's resolved identity actually matches the requested org, but a wrong admin
 * key would otherwise still resolve to *some* org via Logto/fallthrough — this check
 * confirms the key itself is real. `/llm-settings/active-model` is admin-gated: 401
 * means a bad key; 200 or 404 ("no active model set yet") both mean the bearer token
 * cleared that gate.
 */
export async function verifyAdminKey(baseUrl: string, adminKey: string): Promise<boolean> {
  const response = await fetch(`${baseUrl}/llm-settings/active-model`, { headers: headers(adminKey) });
  return response.status !== 401;
}

/**
 * Confirms an engineering-demo key is real before the login screen commits to it —
 * same reasoning as verifyAdminKey above, against platform-registry's dedicated
 * verify-engineering-demo-key endpoint (this key isn't admin-gated, so it can't use
 * /llm-settings/active-model).
 */
export async function verifyEngineeringDemoKey(baseUrl: string, demoKey: string): Promise<boolean> {
  const response = await fetch(`${baseUrl}/auth/verify-engineering-demo-key`, { headers: headers(demoKey) });
  return response.status !== 401;
}

export async function predict(
  baseUrl: string,
  scenarioSlug: string,
  records: Record<string, unknown>[],
  accessToken: string | null,
): Promise<{ predictions: PredictionResult[] }> {
  // One consolidated prediction instance serves every tabular_ml scenario — see root
  // plan's "Consolidation mechanism" decision — routed here by scenarioSlug.
  const response = await fetch(`${baseUrl}/predict/${scenarioSlug}`, {
    method: "POST",
    headers: headers(accessToken),
    body: JSON.stringify({ records }),
  });
  return asJson(response);
}

export async function datasetSample(
  baseUrl: string,
  scenarioSlug: string,
  limit: number,
  accessToken: string | null,
): Promise<DatasetSample> {
  const response = await fetch(`${baseUrl}/dataset/${scenarioSlug}/sample?limit=${limit}`, {
    headers: headers(accessToken),
  });
  return asJson(response);
}

export async function datasetEvaluation(
  baseUrl: string,
  scenarioSlug: string,
  limit: number,
  accessToken: string | null,
): Promise<DatasetEvaluation> {
  const response = await fetch(`${baseUrl}/dataset/${scenarioSlug}/evaluation?limit=${limit}`, {
    headers: headers(accessToken),
  });
  return asJson(response);
}

export async function datasetExplainability(
  baseUrl: string,
  scenarioSlug: string,
  limit: number,
  accessToken: string | null,
): Promise<DatasetExplainability> {
  const response = await fetch(`${baseUrl}/dataset/${scenarioSlug}/explainability?limit=${limit}`, {
    headers: headers(accessToken),
  });
  return asJson(response);
}

export async function listLlmProviders(baseUrl: string, accessToken: string | null): Promise<LlmProvider[]> {
  const response = await fetch(`${baseUrl}/llm-settings/providers`, { headers: headers(accessToken) });
  return asJson(response);
}

export async function testLlmProvider(
  baseUrl: string,
  provider: string,
  accessToken: string | null,
): Promise<LlmProviderTest> {
  const response = await fetch(`${baseUrl}/llm-settings/providers/${provider}/test`, {
    method: "POST",
    headers: headers(accessToken),
  });
  return asJson(response);
}

export async function getActiveLlmModel(baseUrl: string, accessToken: string | null): Promise<string | null> {
  const response = await fetch(`${baseUrl}/llm-settings/active-model`, { headers: headers(accessToken) });
  if (response.status === 404) return null; // no default seeded yet — not an error
  const body = await asJson<{ model_name: string }>(response);
  return body.model_name;
}

export async function setActiveLlmModel(
  baseUrl: string,
  modelName: string,
  accessToken: string | null,
): Promise<string> {
  const response = await fetch(`${baseUrl}/llm-settings/active-model`, {
    method: "PUT",
    headers: headers(accessToken),
    body: JSON.stringify({ model_name: modelName }),
  });
  const body = await asJson<{ model_name: string }>(response);
  return body.model_name;
}

/**
 * The model that would answer this scenario's next chat message — lets the UI show
 * it before the first reply, not just alongside each answer.
 */
export async function chatModel(baseUrl: string, scenarioSlug: string, accessToken: string | null): Promise<string> {
  const response = await fetch(`${baseUrl}/model/${scenarioSlug}`, { headers: headers(accessToken) });
  const body = await asJson<{ model: string }>(response);
  return body.model;
}
