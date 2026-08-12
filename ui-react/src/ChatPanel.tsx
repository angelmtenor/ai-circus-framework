import { useEffect, useRef, useState } from "react";
import { chat, chatModel, type ChatMessage } from "./apiClient";
import { renderMarkdown } from "./markdown";

/**
 * A minimal chat UI calling a scenario's /chat/{scenarioSlug} endpoint directly.
 *
 * A future AG-UI runtime bridge to rag-agent (CopilotKit's <CopilotChat>, streaming
 * agent state, generative UI) is a documented follow-up (see root README "Reserved
 * for later") — CopilotKit's packages aren't installed until that's actually built,
 * to avoid dragging in their bundle weight for an unused integration point.
 *
 * Replies are rendered through a small markdown subset (bold/code/lists/tables, see
 * markdown.tsx), plus a ```chart fenced-JSON convention (chatCharts.tsx) that reuses
 * the dashboard's SVG chart primitives. The backends don't emit ```chart yet — their
 * system prompts need to be taught the convention before an LLM reply will use it.
 */
export function ChatPanel({
  baseUrl,
  scenarioSlug,
  sampleQuestions,
  accessToken,
  variant = "dock",
  title,
  onModel,
}: {
  baseUrl: string;
  scenarioSlug: string;
  sampleQuestions: string[];
  accessToken: string | null;
  variant?: "dock" | "full";
  title?: string;
  /** Called once the active model is known, so a parent header can show it upfront. */
  onModel?: (model: string) => void;
}) {
  const [history, setHistory] = useState<(ChatMessage & { model?: string })[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [sources, setSources] = useState<{ source: string; score: number }[] | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);

  // Scroll to the latest turn whenever the conversation changes — covers the user's
  // own message landing immediately, the typing indicator appearing, and the reply
  // arriving, not just the end of send().
  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: "smooth" });
  }, [history, sending]);

  // Known upfront, before the first message, so the header can show it right away.
  useEffect(() => {
    chatModel(baseUrl, scenarioSlug, accessToken)
      .then((model) => onModel?.(model))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, scenarioSlug, accessToken]);

  async function send(text: string) {
    if (!text.trim() || sending) return;
    setMessage("");
    setSending(true);
    setHistory((h) => [...h, { role: "user", content: text }]);
    try {
      const result = await chat(baseUrl, scenarioSlug, text, history, accessToken);
      setHistory((h) => [...h, { role: "assistant", content: result.reply, model: result.model }]);
      setSources(result.sources ?? null);
    } catch (error) {
      setHistory((h) => [...h, { role: "assistant", content: `⚠️ ${(error as Error).message}` }]);
      setSources(null);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className={`chat-panel chat-panel--${variant}`}>
      {title && <div className="chat-panel-title">{title}</div>}
      <div className="chat-history" ref={historyRef}>
        {history.length === 0 && (
          <div className="chat-empty">
            <span className="chat-empty-icon">💬</span>
            <p>Ask a question to get started.</p>
          </div>
        )}
        {history.map((turn, i) => (
          <div key={i} className={`chat-turn chat-turn--${turn.role}`}>
            <div className="chat-avatar">{turn.role === "user" ? "🧑" : "🤖"}</div>
            <div>
              <div className="chat-bubble">{renderMarkdown(turn.content)}</div>
              {turn.model && <div className="chat-model">{turn.model}</div>}
            </div>
          </div>
        ))}
        {sending && (
          <div className="chat-turn chat-turn--assistant">
            <div className="chat-avatar">🤖</div>
            <div className="chat-bubble chat-bubble--typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
      </div>
      {sources !== null &&
        (sources.length > 0 ? (
          <div className="chat-sources">
            📎 Sources: {sources.map((s) => `${s.source} (${s.score.toFixed(2)})`).join(", ")}
          </div>
        ) : (
          <div className="chat-sources">(answered directly, without consulting the documents)</div>
        ))}
      {sampleQuestions.length > 0 && history.length === 0 && (
        <div className="chat-samples">
          {sampleQuestions.map((question) => (
            <button key={question} className="chat-sample" onClick={() => send(question)} disabled={sending}>
              {question}
            </button>
          ))}
        </div>
      )}
      <div className="chat-input-row">
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send(message)}
          placeholder="Ask a question..."
          disabled={sending}
        />
        <button className="chat-send" onClick={() => send(message)} disabled={sending || !message.trim()}>
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
