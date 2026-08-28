import { useEffect, useState } from "react";
import type { Message as AguiMessage } from "@ag-ui/core";
import { createConversation, getConversationMessages, listConversations, type ConversationSummary } from "./apiClient";

/**
 * Owns which conversation is active for one scenario's chat, and that
 * conversation's persisted history to seed ChatPanel with (see its
 * `initialMessages` prop). On mount (and whenever the active conversation is
 * deleted from under it — see `onDeleteActive`), resumes the most-recently-updated
 * conversation if one exists, or starts a fresh one, mirroring
 * ConversationSidebar.tsx's own "+ New conversation" default.
 */
export function useConversation(baseUrl: string, scenarioSlug: string, accessToken: string | null) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [initialMessages, setInitialMessages] = useState<AguiMessage[]>([]);
  const [bootstrapKey, setBootstrapKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let retryTimeout: ReturnType<typeof setTimeout> | undefined;

    function bootstrap(attempt: number) {
      listConversations(baseUrl, scenarioSlug, accessToken)
        .then((list) => {
          if (cancelled) return undefined;
          if (list.length > 0) {
            setConversationId(list[0].id);
            return undefined;
          }
          return createConversation(baseUrl, scenarioSlug, null, accessToken).then((created) => {
            if (!cancelled) setConversationId(created.id);
          });
        })
        .catch(() => {
          if (cancelled) return;
          // A transient network/backend hiccup (e.g. a service mid-restart) must
          // not leave the chat permanently disabled with no way to recover — retry
          // with a capped backoff instead of failing silently forever.
          const delay = Math.min(1000 * 2 ** attempt, 8000);
          retryTimeout = setTimeout(() => bootstrap(attempt + 1), delay);
        });
    }

    bootstrap(0);

    return () => {
      cancelled = true;
      if (retryTimeout) clearTimeout(retryTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, scenarioSlug, accessToken, bootstrapKey]);

  useEffect(() => {
    if (!conversationId) {
      setInitialMessages([]);
      return;
    }
    let cancelled = false;
    getConversationMessages(baseUrl, scenarioSlug, conversationId, accessToken)
      .then((messages) => {
        if (cancelled) return;
        setInitialMessages(messages.map((m) => ({ id: m.id, role: m.role, content: m.content }) as AguiMessage));
      })
      .catch(() => {
        // Best-effort history replay — a failed fetch here leaves the conversation
        // usable (just without its prior turns shown/resent) rather than blocking it.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  return {
    // Transient fallback (the scenario slug) before the first list/create call
    // resolves — matches useScenarioAgent's own pre-conversation default. NOT a
    // real conversation id: sending against it 404s (see `ready` below).
    conversationId: conversationId ?? scenarioSlug,
    // False while the initial list-or-create round trip (or a post-delete
    // re-bootstrap) is still in flight. The chat must not be usable yet in that
    // window — `conversationId` is still the scenarioSlug placeholder above, and
    // the backend's ownership check 404s any /agui run against it. Callers should
    // disable sending (or show a brief loading state) while `!ready`.
    ready: conversationId !== null,
    initialMessages,
    selectConversation: (id: string) => setConversationId(id),
    onCreate: (created: ConversationSummary) => setConversationId(created.id),
    onDeleteActive: () => {
      setConversationId(null);
      setBootstrapKey((k) => k + 1);
    },
  };
}
