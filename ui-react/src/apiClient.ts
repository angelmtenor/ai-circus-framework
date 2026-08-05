/**
 * Thin fetch-based clients for the backend services — mirrors
 * services/ui-streamlit/src/ui_streamlit/core/api_client.py's shape so the two UIs
 * stay behaviorally consistent.
 */

export type ScenarioSummary = {
  slug: string;
  kind: string;
  title: string;
  description: string;
  icon: string;
};

export type PredictionResult = {
  probability: number;
  contributions: Record<string, number>;
};

export type ChatMessage = { role: string; content: string };

export type ChatResult = {
  reply: string;
  sources?: { source: string; score: number }[];
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

export async function predict(
  baseUrl: string,
  records: Record<string, unknown>[],
  accessToken: string | null,
): Promise<{ predictions: PredictionResult[] }> {
  const response = await fetch(`${baseUrl}/predict`, {
    method: "POST",
    headers: headers(accessToken),
    body: JSON.stringify({ records }),
  });
  return asJson(response);
}

export async function chat(
  baseUrl: string,
  message: string,
  history: ChatMessage[],
  accessToken: string | null,
): Promise<ChatResult> {
  const response = await fetch(`${baseUrl}/chat`, {
    method: "POST",
    headers: headers(accessToken),
    body: JSON.stringify({ message, history }),
  });
  return asJson(response);
}
