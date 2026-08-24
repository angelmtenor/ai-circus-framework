import { useEffect, useMemo, useRef, useState } from "react";
import { randomUUID, type HttpAgent } from "@ag-ui/client";
import type { Context as AguiContext, Message as AguiMessage, Tool as AguiTool } from "@ag-ui/core";
import { useCopilotKit } from "@copilotkit/react-core/v2";
import { schemaToJsonSchema } from "@copilotkit/shared";
import { zodToJsonSchema as zodToJsonSchemaImpl } from "zod-to-json-schema";

function zodToJsonSchema(schema: unknown, options?: { $refStrategy?: string }): Record<string, unknown> {
  return zodToJsonSchemaImpl(schema as never, options as never) as Record<string, unknown>;
}
import { chatModel, type ChatModel } from "./apiClient";
import { config } from "./config";
import { MicButton } from "./MicButton";
import { renderMarkdown } from "./markdown";
import { SpeakerButton } from "./SpeakerButton";

/**
 * Chat UI driven directly by an @ag-ui/client HttpAgent (see RagView.tsx/TabularView.tsx,
 * which construct one per scenario and pass it down) rather than CopilotKit's own
 * useCopilotChat hook. That hook's current version routes through a GraphQL "Copilot
 * Runtime" (@copilotkit/runtime-client-gql) meant for a Node.js backend; HttpAgent
 * speaks the AG-UI wire protocol straight to our Python /agui/{scenario_slug}
 * endpoint — verified directly, no extra service.
 *
 * CopilotKit is still used for useCopilotAction/useCopilotReadable (see
 * chatGenerativeUi.tsx and the workspace views' useCopilotReadable calls) — but read
 * back here via useCopilotKit().copilotkit (the v2 CopilotKitCore instance), not the
 * legacy useCopilotContext(). Confirmed by reading the installed package's own
 * source: both hooks now register into CopilotKitCore (`copilotkit.addTool`/
 * `copilotkit.addContext`), not the legacy CopilotContext — useCopilotContext().actions
 * stays permanently empty for any action that (like ours) has a `handler`, so tools
 * silently never reached the model until this was fixed.
 *
 * `agent.messages` (kept in sync via onMessagesChanged) is the single source of
 * truth for the transcript — includes token-by-token streamed content, so replies
 * render incrementally exactly like ChatGPT/Claude, not "wait for the full reply".
 */

type CopilotKitCoreTool = {
  name: string;
  description?: string;
  parameters?: unknown;
  available?: boolean;
  render?: (props: { args: unknown; status: string; name: string }) => React.ReactNode;
};

function toolsFromCore(tools: readonly CopilotKitCoreTool[]): AguiTool[] {
  return tools
    .filter((t) => t.available !== false)
    .map((t) => ({
      name: t.name,
      description: t.description ?? "",
      // `parameters` is a Zod v3 schema (getZodParameters' output, see useFrontendTool
      // in @copilotkit/react-core) — zod-to-json-schema converts it for the wire, since
      // AG-UI's Tool.parameters needs real JSON Schema, not a Zod object.
      parameters: (t.parameters ? schemaToJsonSchema(t.parameters as never, { zodToJsonSchema }) : { type: "object", properties: {} }) as Record<
        string,
        unknown
      >,
    }));
}

function contextFromCore(context: Record<string, { description: string; value: string }>): AguiContext[] {
  return Object.values(context).map((c) => ({ description: c.description, value: c.value }));
}

const SOURCE_TAG = /\[Source:\s*([^\]]+)\]/g;

/** Best-effort: retrieve_docs' tool result content embeds "[Source: file]" markers
 * (see rag-agent's build_retrieve_tool) — extracted here rather than carried as a
 * separate structured field, since AG-UI's ToolMessage only has a plain `content`
 * string. Returns null if no tool ran at all (message list has no tool results yet),
 * vs. an empty array if retrieve_docs ran but found nothing — same distinction the
 * old REST response made. */
function extractSources(messages: AguiMessage[]): string[] | null {
  const toolResults = messages.filter((m): m is AguiMessage & { role: "tool"; content: string } => m.role === "tool");
  if (toolResults.length === 0) return null;
  const sources = new Set<string>();
  for (const m of toolResults) {
    for (const match of m.content.matchAll(SOURCE_TAG)) sources.add(match[1].trim());
  }
  return [...sources];
}

/**
 * Frontend tools (render_chart/render_table, registered in chatGenerativeUi.tsx) are
 * dispatched by ChatPanel itself, not by CopilotKit's own runtime (see that file's
 * top-of-file note) — so nothing ever sends the model a `ToolMessage` answering those
 * tool calls. Left unanswered, the *next* turn's resent full history has a dangling
 * tool_call, which CopilotKit's LangGraph middleware strips out before the next model
 * call (it has no adjacent ToolMessage) — silently rewriting history and reshuffling
 * where that turn's chart/table appears in the transcript on replay. Backfilling a
 * synthetic ToolMessage per unanswered frontend tool call, right after each run,
 * keeps history complete so nothing gets stripped or reordered on the next turn.
 *
 * `onFrontendToolCall`, when given, fires once per call right before it's marked
 * answered — the one place a tool call is guaranteed to be processed exactly once
 * (this same `answered` dedup is what makes render_chart/render_table idempotent
 * across repeated runs). This is the only path a scenario has to let the assistant
 * write into its own dashboard state (e.g. assisted_form's update_form_fields) rather
 * than only draw inside the chat bubble — ChatPanel itself stays generic, unaware of
 * what any particular tool call means.
 */
function answerUnansweredFrontendToolCalls(
  agent: HttpAgent,
  frontendToolNames: ReadonlySet<string>,
  onFrontendToolCall?: (name: string, args: unknown) => void,
): void {
  const answered = new Set(
    agent.messages.filter((m): m is AguiMessage & { role: "tool" } => m.role === "tool").map((m) => m.toolCallId),
  );
  for (const m of agent.messages) {
    if (m.role !== "assistant" || !m.toolCalls) continue;
    for (const call of m.toolCalls) {
      if (!frontendToolNames.has(call.function.name) || answered.has(call.id)) continue;
      if (onFrontendToolCall) {
        try {
          onFrontendToolCall(call.function.name, JSON.parse(call.function.arguments));
        } catch {
          // Malformed tool-call arguments shouldn't break history bookkeeping below.
        }
      }
      agent.addMessage({ id: randomUUID(), role: "tool", toolCallId: call.id, content: "Displayed to the user." });
    }
  }
}

const TOOL_ACTIVITY_LABELS: Record<string, string> = {
  get_dataset_sample: "Fetching real dataset rows…",
  get_predictions_vs_actuals: "Fetching predictions vs. actuals…",
  predict_records: "Running the model…",
  retrieve_docs: "Searching documents…",
  render_chart: "Drawing chart…",
  render_table: "Building table…",
};

function toolActivityLabel(name: string): string {
  return TOOL_ACTIVITY_LABELS[name] ?? `Calling ${name}…`;
}

export function ChatPanel({
  agent,
  baseUrl,
  scenarioSlug,
  sampleQuestions,
  accessToken,
  variant = "dock",
  title,
  onModel,
  onFrontendToolCall,
}: {
  agent: HttpAgent;
  baseUrl: string;
  scenarioSlug: string;
  sampleQuestions: string[];
  accessToken: string | null;
  variant?: "dock" | "full";
  title?: string;
  onModel?: (model: ChatModel) => void;
  // Fires once per frontend tool call the agent makes (e.g. assisted_form's
  // update_form_fields) — the only way a scenario's own dashboard state can be
  // written from the conversation; see answerUnansweredFrontendToolCalls above.
  onFrontendToolCall?: (name: string, args: unknown) => void;
}) {
  const { copilotkit } = useCopilotKit();
  const [messages, setMessages] = useState<AguiMessage[]>(agent.messages);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    chatModel(baseUrl, scenarioSlug, accessToken)
      .then((model) => onModel?.(model))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, scenarioSlug, accessToken]);

  useEffect(() => {
    const { unsubscribe } = agent.subscribe({
      onMessagesChanged: ({ messages }) => setMessages([...messages]),
      // Live "what the agent is doing" status, straight off the AG-UI event stream —
      // fires as soon as a tool call starts/resolves, well before the final reply
      // (which may be seconds away for a multi-tool-call turn) starts streaming.
      onToolCallStartEvent: ({ event }) => setActivity(toolActivityLabel(event.toolCallName)),
      onToolCallResultEvent: () => setActivity("Thinking…"),
      onTextMessageStartEvent: () => setActivity(null),
      onRunFinishedEvent: () => setActivity(null),
      onRunErrorEvent: () => setActivity(null),
    });
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);

  async function send(text: string) {
    if (!text.trim() || sending) return;
    setMessage("");
    setSending(true);
    setActivity(null);
    agent.addMessage({ id: randomUUID(), role: "user", content: text });
    try {
      const tools = toolsFromCore(copilotkit.tools as CopilotKitCoreTool[]);
      const context = contextFromCore(copilotkit.context as Record<string, { description: string; value: string }>);
      await agent.runAgent({ tools, context });
      const frontendToolNames = new Set(
        (copilotkit.tools as CopilotKitCoreTool[]).filter((t) => t.render).map((t) => t.name),
      );
      answerUnansweredFrontendToolCalls(agent, frontendToolNames, onFrontendToolCall);
    } catch (error) {
      agent.addMessage({ id: randomUUID(), role: "assistant", content: `⚠️ ${(error as Error).message}` });
    } finally {
      setSending(false);
      setActivity(null);
    }
  }

  const sources = useMemo(() => extractSources(messages), [messages]);
  // An assistant message with neither content nor a tool call is an intermediate
  // placeholder from the agent's own multi-step loop (e.g. the turn where it only
  // decided to call a tool, before the tool result and final reply arrived) — not
  // a real reply to show as its own empty bubble.
  const visible = messages.filter(
    (m) => m.role === "user" || (m.role === "assistant" && (m.content || (m.toolCalls && m.toolCalls.length > 0))),
  );

  return (
    <div className={`chat-panel chat-panel--${variant}`}>
      {title && <div className="chat-panel-title">{title}</div>}
      <div className="chat-history" ref={historyRef}>
        {visible.length === 0 && (
          <div className="chat-empty">
            <span className="chat-empty-icon">💬</span>
            <p>Ask a question to get started.</p>
          </div>
        )}
        {visible.map((turn) => (
          <div key={turn.id} className={`chat-turn chat-turn--${turn.role}`}>
            <div className="chat-avatar">{turn.role === "user" ? "🧑" : "🤖"}</div>
            <div>
              {typeof turn.content === "string" && turn.content && (
                <div className="chat-bubble">
                  {renderMarkdown(turn.content)}
                  {turn.role === "assistant" && (
                    <SpeakerButton
                      text={turn.content}
                      voiceUrl={config.voiceUrl}
                      scenarioSlug={scenarioSlug}
                      accessToken={accessToken}
                    />
                  )}
                </div>
              )}
              {turn.role === "assistant" &&
                turn.toolCalls?.map((call) => {
                  const tool = (copilotkit.tools as CopilotKitCoreTool[]).find((t) => t.name === call.function.name);
                  if (!tool?.render) return null;
                  let args: unknown;
                  try {
                    args = JSON.parse(call.function.arguments);
                  } catch {
                    return null;
                  }
                  return <div key={call.id}>{tool.render({ args, status: "complete", name: call.function.name })}</div>;
                })}
            </div>
          </div>
        ))}
        {sending && (
          <div className="chat-turn chat-turn--assistant">
            <div className="chat-avatar">🤖</div>
            <div className="chat-bubble chat-bubble--typing">
              {activity ? (
                <span className="chat-activity">{activity}</span>
              ) : (
                <>
                  <span />
                  <span />
                  <span />
                </>
              )}
            </div>
          </div>
        )}
      </div>
      {sources !== null &&
        (sources.length > 0 ? (
          <div className="chat-sources">📎 Sources: {sources.join(", ")}</div>
        ) : (
          <div className="chat-sources">(answered directly, without consulting the documents)</div>
        ))}
      {sampleQuestions.length > 0 && visible.length === 0 && (
        <div className="chat-samples">
          {sampleQuestions.map((question) => (
            <button key={question} className="chat-sample" onClick={() => send(question)} disabled={sending}>
              {question}
            </button>
          ))}
        </div>
      )}
      <div className="chat-input-row">
        <MicButton agent={agent} voiceUrl={config.voiceUrl} scenarioSlug={scenarioSlug} accessToken={accessToken} />
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
