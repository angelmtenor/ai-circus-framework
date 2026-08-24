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

// Mirrors libs/shared/src/ai_circus_shared/scenario_schema.py's FormFieldSpec/
// FormConfig — drives ui-react's generic assisted_form renderer (see FormPanel.tsx).
// `validation` is a small, reusable set of primitives; scenario-specific detail
// (e.g. an ID number's format) is plain data (`pattern`), never new UI code.
export type FormFieldType = "text" | "textarea" | "email" | "tel" | "select" | "date";
export type FormFieldValidation = "none" | "email" | "phone" | "pattern" | "min_length";
export type RequiredIf = { field: string; in_values: string[] };
export type FormFieldSpec = {
  id: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  required_if?: RequiredIf | null;
  options?: string[] | null;
  validation: FormFieldValidation;
  pattern?: string | null;
  min_length?: number | null;
  helper_text?: string | null;
};
export type FormConfig = {
  title: string;
  fields: FormFieldSpec[];
  classification_field?: string | null;
  classification_options?: string[] | null;
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
  // assisted_form only — drives ui-react's generic form renderer (see FormPanel.tsx).
  form?: FormConfig | null;
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

export type LlmProviderModel = {
  model_name: string;
  label: string;
  route_exists: boolean;
  model: string | null;
  api_base: string | null;
  vision: boolean;
};

export type LlmProvider = {
  provider: string;
  label: string;
  needs_key: boolean;
  needs_base: boolean;
  env_vars: string[];
  hint: string;
  // A provider is one API key; most route a single model (one-element array), but
  // e.g. GroqCloud routes more than one model off the same key — see
  // platform_registry.core.llm_settings.PROVIDERS.
  models: LlmProviderModel[];
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
  modelName: string,
  accessToken: string | null,
): Promise<LlmProviderTest> {
  const response = await fetch(`${baseUrl}/llm-settings/providers/${provider}/models/${modelName}/test`, {
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

export type ActiveVoiceSettings = { stt_provider: string; tts_provider: string };

/**
 * Which STT/TTS provider agui-voice should use right now — the admin Settings
 * page's voice-mode picker, mirroring getActiveLlmModel/setActiveLlmModel above.
 */
export async function getActiveVoiceSettings(
  baseUrl: string,
  accessToken: string | null,
): Promise<ActiveVoiceSettings | null> {
  const response = await fetch(`${baseUrl}/voice-settings/active`, { headers: headers(accessToken) });
  if (response.status === 404) return null; // no default seeded yet — not an error
  return asJson<ActiveVoiceSettings>(response);
}

export async function setActiveVoiceSettings(
  baseUrl: string,
  sttProvider: string,
  ttsProvider: string,
  accessToken: string | null,
): Promise<ActiveVoiceSettings> {
  const response = await fetch(`${baseUrl}/voice-settings/active`, {
    method: "PUT",
    headers: headers(accessToken),
    body: JSON.stringify({ stt_provider: sttProvider, tts_provider: ttsProvider }),
  });
  return asJson<ActiveVoiceSettings>(response);
}

export type ChatModel = { model: string; provider: string | null; vision: boolean };

/**
 * The model (and its provider) that would answer this scenario's next chat message —
 * lets the UI show it before the first reply, not just alongside each answer.
 * `vision` tells ChatPanel's attach flow whether an attached image can go straight
 * to this model or needs OCR text-extraction first (see extractDocument below).
 */
export async function chatModel(baseUrl: string, scenarioSlug: string, accessToken: string | null): Promise<ChatModel> {
  const response = await fetch(`${baseUrl}/model/${scenarioSlug}`, { headers: headers(accessToken) });
  return asJson<ChatModel>(response);
}

export type DocumentExtract = {
  filename: string;
  kind: "text" | "markdown" | "docx" | "pdf" | "image";
  text: string;
  truncated: boolean;
  page_count: number | null;
  used_ocr: boolean;
};

/**
 * Best-effort text extraction for a session-only chat attachment — platform-registry
 * never persists the file (see platform_registry.core.document_extraction's module
 * docstring); the extracted text is folded into the next chat message as plain text
 * by ChatPanel's `send()` and then discarded along with the `File` object itself.
 * Called for every non-image attachment (pdf/docx/md/txt), and for an image
 * attachment when the scenario's active model has no vision support (ChatModel.vision).
 */
export async function extractDocument(
  platformRegistryUrl: string,
  file: File,
  accessToken: string | null,
): Promise<DocumentExtract> {
  const body = new FormData();
  body.append("file", file, file.name);
  const response = await fetch(`${platformRegistryUrl}/documents/extract`, {
    method: "POST",
    // No Content-Type header here: the browser sets multipart/form-data with the
    // right boundary itself — setting it manually (as headers() would, via its
    // "application/json" default) breaks the multipart parse server-side.
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    body,
  });
  return asJson<DocumentExtract>(response);
}

export type SubmissionError = { errors: Record<string, string> };

/**
 * Submit an assisted_form scenario's form. The backend (form-agent) is the final
 * validation authority — see ai_circus_shared.form_validation — so a 422 here can
 * happen even though FormPanel's own client-side check already passed; that's a
 * feedback bug in the client-side mirror, not something to paper over.
 */
export async function submitForm(
  baseUrl: string,
  scenarioSlug: string,
  fields: Record<string, string>,
  accessToken: string | null,
): Promise<{ case_number: string } | SubmissionError> {
  const response = await fetch(`${baseUrl}/submissions/${scenarioSlug}`, {
    method: "POST",
    headers: headers(accessToken),
    body: JSON.stringify({ fields }),
  });
  if (response.status === 422) {
    const body = await asJson<{ detail: SubmissionError }>(response);
    return body.detail;
  }
  return asJson<{ case_number: string }>(response);
}

/**
 * The chat panel's loudspeaker icon: synthesizes `text` as speech via agui-voice's
 * one-shot `POST /tts/{scenario_slug}` (see SpeakerButton.tsx) — a plain WAV blob,
 * decoupled from the live voice WebSocket session (useVoiceSession.ts).
 */
export async function speakText(
  voiceUrl: string,
  scenarioSlug: string,
  text: string,
  accessToken: string | null,
): Promise<Blob> {
  const response = await fetch(`${voiceUrl}/tts/${scenarioSlug}`, {
    method: "POST",
    headers: headers(accessToken),
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${await response.text()}`);
  }
  return response.blob();
}

export type VoiceProviderOption = {
  id: string;
  label: string;
  available: boolean;
  reason: string | null;
};

export type VoiceProviderGroup = {
  active: string;
  options: VoiceProviderOption[];
};

export type VoiceProviders = {
  stt: VoiceProviderGroup;
  tts: VoiceProviderGroup;
};

/**
 * Which STT/TTS provider agui-voice would actually use for this scenario's next
 * voice turn, plus every provider it knows how to construct (self-hosted always
 * available; a cloud one only if its API key is configured on this instance) —
 * powers both the assistant UI's "STT: ... / TTS: ..." label (MicButton.tsx) and
 * the admin Settings page's voice-mode picker.
 */
export async function voiceProviders(
  voiceUrl: string,
  scenarioSlug: string,
  accessToken: string | null,
): Promise<VoiceProviders> {
  const response = await fetch(`${voiceUrl}/providers/${scenarioSlug}`, { headers: headers(accessToken) });
  return asJson<VoiceProviders>(response);
}
