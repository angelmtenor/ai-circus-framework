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

export type ScenarioSummary = {
  slug: string;
  kind: string;
  title: string;
  description: string;
  icon: string;
  feature_columns?: string[] | null;
  feature_schema?: Record<string, FeatureSpec> | null;
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

export type ChatMessage = { role: string; content: string };

export type ChatResult = {
  reply: string;
  sources?: { source: string; score: number }[];
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

export async function listEntitledScenarios(baseUrl: string, orgId: string): Promise<ScenarioSummary[]> {
  const response = await fetch(`${baseUrl}/entitlements/${orgId}`);
  return asJson<ScenarioSummary[]>(response);
}

/**
 * Confirms an admin key is real before the login screen commits to it. `/entitlements`
 * has no auth check at all (see platform_registry.api's module docstring — it trusts
 * the caller to have validated already), so a bad key would otherwise sail straight
 * through to the scenario picker and only fail once a scenario's own chat/predict
 * call 401s. `/llm-settings/active-model` is admin-gated: 401 means a bad key; 200 or
 * 404 ("no active model set yet") both mean the bearer token cleared that gate.
 */
export async function verifyAdminKey(baseUrl: string, adminKey: string): Promise<boolean> {
  const response = await fetch(`${baseUrl}/llm-settings/active-model`, { headers: headers(adminKey) });
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

export async function chat(
  baseUrl: string,
  scenarioSlug: string,
  message: string,
  history: ChatMessage[],
  accessToken: string | null,
): Promise<ChatResult> {
  const response = await fetch(`${baseUrl}/chat/${scenarioSlug}`, {
    method: "POST",
    headers: headers(accessToken),
    body: JSON.stringify({ message, history }),
  });
  return asJson(response);
}
