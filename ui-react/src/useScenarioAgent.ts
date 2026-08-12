import { useMemo } from "react";
import { HttpAgent } from "@ag-ui/client";

/**
 * One AG-UI HttpAgent per (baseUrl, scenarioSlug, accessToken) — talks directly to
 * the backend's POST /agui/{scenarioSlug} endpoint (see ChatPanel.tsx's top-of-file
 * note). Memoized so it's stable across re-renders and only rebuilt when the caller
 * actually changes (e.g. a token refresh), since replacing it mid-conversation would
 * drop in-flight subscriptions.
 */
export function useScenarioAgent(baseUrl: string, scenarioSlug: string, accessToken: string | null): HttpAgent {
  return useMemo(
    () =>
      new HttpAgent({
        url: `${baseUrl}/agui/${scenarioSlug}`,
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
        threadId: scenarioSlug,
      }),
    [baseUrl, scenarioSlug, accessToken],
  );
}
