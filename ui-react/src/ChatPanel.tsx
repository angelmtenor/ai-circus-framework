import { useState } from "react";
import { chat, type ChatMessage } from "./apiClient";

/**
 * A minimal chat UI calling a scenario's /chat/{scenarioSlug} endpoint directly.
 *
 * @copilotkit/react-ui's <CopilotChat> is intentionally NOT used here: it expects a
 * self-hosted "Copilot Runtime" implementing CopilotKit's AG-UI protocol (a GraphQL
 * endpoint bridging to an LLM/agent), which this pass doesn't build — wiring rag-agent
 * behind a real AG-UI runtime (streaming agent state, generative UI) is a documented
 * follow-up (see root README "Reserved for later"). CopilotKit's packages are still
 * installed and the app is wrapped in <CopilotKit> in App.tsx as the intended
 * integration point once that runtime exists.
 */
export function ChatPanel({
  baseUrl,
  scenarioSlug,
  sampleQuestions,
  accessToken,
}: {
  baseUrl: string;
  scenarioSlug: string;
  sampleQuestions: string[];
  accessToken: string | null;
}) {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [sources, setSources] = useState<{ source: string; score: number }[] | null>(null);

  async function send(text: string) {
    if (!text.trim()) return;
    setMessage("");
    setSending(true);
    setHistory((h) => [...h, { role: "user", content: text }]);
    try {
      const result = await chat(baseUrl, scenarioSlug, text, history, accessToken);
      setHistory((h) => [...h, { role: "assistant", content: result.reply }]);
      setSources(result.sources ?? []);
    } catch (error) {
      setHistory((h) => [...h, { role: "assistant", content: `Error: ${(error as Error).message}` }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-history">
        {history.map((turn, i) => (
          <div key={i} className={`chat-turn chat-turn--${turn.role}`}>
            <strong>{turn.role}:</strong> {turn.content}
          </div>
        ))}
      </div>
      {sources !== null &&
        (sources.length > 0 ? (
          <div className="chat-sources">
            Sources: {sources.map((s) => `${s.source} (${s.score.toFixed(2)})`).join(", ")}
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
        <button onClick={() => send(message)} disabled={sending}>
          {sending ? "..." : "Send"}
        </button>
      </div>
    </div>
  );
}
