import { useEffect, useState } from "react";
import {
  createConversation,
  deleteConversation,
  listConversations,
  type ConversationSummary,
} from "./apiClient";

// Must match every backend service's own default (see e.g. rag_agent.api's
// `body.title or "New conversation"` and ai_circus_shared.conversations.DEFAULT_TITLE)
// — a conversation still carrying this title has never had a message sent in it.
const DEFAULT_CONVERSATION_TITLE = "New conversation";
// A collapsible sidebar remembers, per browser, whether the reader folded it away.
const COLLAPSED_STORAGE_KEY = "ai-circus.conversations-collapsed";

function storedCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * A lite ChatGPT/Claude-style conversation manager: a vertical left-hand column
 * listing past conversations (title + delete) with a "+ New conversation" button
 * above, shared across all three scenario kinds (see RagView.tsx/TabularView.tsx/
 * AssistedFormView.tsx, which each own the `activeConversationId` state this
 * component reports into via `onSelect`/`onCreate`). `compact` shrinks it for
 * TabularView's narrower chat dock overlay; `collapsible` lets the full vertical column
 * fold into a slim rail (New + show) so the chat beside it gets the width.
 */
export function ConversationSidebar({
  baseUrl,
  scenarioSlug,
  accessToken,
  activeConversationId,
  onSelect,
  onCreate,
  onDeleteActive,
  refreshKey,
  compact = false,
  collapsible = false,
}: {
  baseUrl: string;
  scenarioSlug: string;
  accessToken: string | null;
  activeConversationId: string | null;
  onSelect: (conversationId: string) => void;
  onCreate: (conversation: ConversationSummary) => void;
  // Fires when the currently-active conversation is the one just deleted, so the
  // parent view can fall back to a fresh conversation instead of showing a dead one.
  onDeleteActive: () => void;
  // Bump this (e.g. after a chat turn completes) to re-fetch the list — picks up
  // the active conversation's title once it's auto-derived from its first message
  // (see ai_circus_shared.conversations.ConversationStore.append_messages).
  refreshKey?: unknown;
  compact?: boolean;
  collapsible?: boolean;
}) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [busy, setBusy] = useState(false);
  // TabularView's chat dock stacks this as a horizontal strip above the chat (see
  // App.css's `.chat-dock-body--stacked`) — collapsed by default there so the past-
  // conversations list doesn't permanently eat into the dock's already-tight
  // vertical space. The full vertical sidebar folds into a rail only when `collapsible`
  // (RagView), remembering the choice; AssistedFormView's always shows its list.
  const [collapsed, setCollapsed] = useState(() => compact || (collapsible && storedCollapsed()));
  const rail = collapsible && !compact && collapsed;

  function toggleCollapsed() {
    setCollapsed((c) => {
      if (collapsible && !compact) {
        try {
          localStorage.setItem(COLLAPSED_STORAGE_KEY, c ? "0" : "1");
        } catch {
          // Storage blocked (private window): the toggle still works for this page.
        }
      }
      return !c;
    });
  }

  useEffect(() => {
    let cancelled = false;
    listConversations(baseUrl, scenarioSlug, accessToken)
      .then((list) => {
        if (!cancelled) setConversations(list);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // Also re-keyed on `activeConversationId`: useConversation.ts's own bootstrap
    // can create a conversation (when the scenario has none yet) independently of
    // this component — without this, that conversation wouldn't appear in the list
    // (and the "already on an empty conversation" check below would never see it)
    // until something else happened to bump `refreshKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, scenarioSlug, accessToken, refreshKey, activeConversationId]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId);
  const activeIsEmpty = activeConversation?.title === DEFAULT_CONVERSATION_TITLE;

  async function handleNew() {
    // Already on a fresh, never-used conversation — nothing to gain from spawning
    // another empty one alongside it (matches ChatGPT's "New Chat" no-op here).
    if (activeIsEmpty) return;
    setBusy(true);
    try {
      const created = await createConversation(baseUrl, scenarioSlug, null, accessToken);
      setConversations((prev) => [created, ...prev]);
      onCreate(created);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(conversationId: string) {
    await deleteConversation(baseUrl, scenarioSlug, conversationId, accessToken);
    setConversations((prev) => prev.filter((c) => c.id !== conversationId));
    if (conversationId === activeConversationId) onDeleteActive();
  }

  const pastConversations = conversations.filter((c) => c.title !== DEFAULT_CONVERSATION_TITLE);

  if (rail) {
    return (
      <div className="conversation-sidebar conversation-sidebar--rail">
        <button
          type="button"
          className="conversation-rail-button"
          onClick={toggleCollapsed}
          aria-label="Show past conversations"
          title={`Show past conversations (${pastConversations.length})`}
        >
          <span aria-hidden="true">☰</span>
          {pastConversations.length > 0 && <span className="conversation-rail-count">{pastConversations.length}</span>}
        </button>
        <button
          type="button"
          className="conversation-rail-button"
          onClick={handleNew}
          disabled={busy || activeIsEmpty}
          aria-label="New conversation"
          title={activeIsEmpty ? "You're already on a new, empty conversation" : "New conversation"}
        >
          +
        </button>
      </div>
    );
  }

  return (
    <div className={`conversation-sidebar${compact ? " conversation-sidebar--compact" : ""}`}>
      <div className="conversation-header-row">
        <button
          className="conversation-new"
          onClick={handleNew}
          disabled={busy}
          title={activeIsEmpty ? "You're already on a new, empty conversation" : undefined}
        >
          + New conversation
        </button>
        {collapsible && !compact && (
          <button
            type="button"
            className="conversation-collapse-toggle"
            onClick={toggleCollapsed}
            aria-label="Hide past conversations"
            title="Hide past conversations"
          >
            «
          </button>
        )}
        {compact && pastConversations.length > 0 && (
          <button
            type="button"
            className="conversation-collapse-toggle"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Show past conversations" : "Hide past conversations"}
            title={collapsed ? `Show ${pastConversations.length} past conversation(s)` : "Hide past conversations"}
          >
            {collapsed ? `☰ ${pastConversations.length}` : "×"}
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="conversation-list">
          {pastConversations.map((c) => (
            <div
              key={c.id}
              className={`conversation-chip${c.id === activeConversationId ? " conversation-chip--active" : ""}`}
            >
              <button className="conversation-chip-select" onClick={() => onSelect(c.id)} title={c.title}>
                {c.title}
              </button>
              <button
                type="button"
                className="conversation-chip-remove"
                onClick={() => handleDelete(c.id)}
                aria-label={`Delete ${c.title}`}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
