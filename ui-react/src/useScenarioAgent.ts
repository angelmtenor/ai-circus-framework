import { useMemo } from "react";
import { HttpAgent } from "@ag-ui/client";

/**
 * One AG-UI HttpAgent per (baseUrl, scenarioSlug, threadId, accessToken) — talks
 * directly to the backend's POST /agui/{scenarioSlug} endpoint (see ChatPanel.tsx's
 * top-of-file note). Memoized so it's stable across re-renders and only rebuilt when
 * the caller actually changes (e.g. a token refresh) or `threadId` switches to a
 * different conversation — see the conversation sidebar (ConversationSidebar.tsx):
 * switching conversations naturally produces a fresh agent with an empty transcript,
 * which ChatPanel then reseeds from that conversation's own persisted history (its
 * `initialMessages` prop) rather than replaying the previous conversation's messages.
 * `threadId` defaults to `scenarioSlug` only as a transient value before the caller's
 * own conversation list/creation call resolves a real conversation id.
 */
export function useScenarioAgent(
  baseUrl: string,
  scenarioSlug: string,
  threadId: string,
  accessToken: string | null,
): HttpAgent {
  return useMemo(
    () =>
      new HttpAgent({
        url: `${baseUrl}/agui/${scenarioSlug}`,
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
        threadId,
      }),
    [baseUrl, scenarioSlug, threadId, accessToken],
  );
}
