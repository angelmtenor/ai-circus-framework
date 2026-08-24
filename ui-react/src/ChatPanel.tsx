import { useEffect, useMemo, useRef, useState } from "react";
import { randomUUID, type HttpAgent } from "@ag-ui/client";
import type { Context as AguiContext, InputContent, Message as AguiMessage, Tool as AguiTool } from "@ag-ui/core";
import { useCopilotKit } from "@copilotkit/react-core/v2";
import { schemaToJsonSchema } from "@copilotkit/shared";
import { zodToJsonSchema as zodToJsonSchemaImpl } from "zod-to-json-schema";

function zodToJsonSchema(schema: unknown, options?: { $refStrategy?: string }): Record<string, unknown> {
  return zodToJsonSchemaImpl(schema as never, options as never) as Record<string, unknown>;
}
import { AttachButton } from "./AttachButton";
import { chatModel, extractDocument, type ChatModel } from "./apiClient";
import { config } from "./config";
import { MicButton } from "./MicButton";
import { renderMarkdown } from "./markdown";
import { SpeakerButton } from "./SpeakerButton";

/** Read `file` as base64 (no `data:...;base64,` prefix) — the shape AG-UI's
 * `InputContentDataSource.value` expects (see ag_ui_langgraph.utils._media_source_to_url,
 * which itself re-adds the `data:<mime>;base64,` prefix server-side).
 */
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read the attached file."));
    reader.readAsDataURL(file);
  });
}

// Chat attachments are session-only (never persisted — see
// platform_registry.core.document_extraction's module docstring), so this caps how
// many a single browser tab can hold pending/sent at once, not anything server-side.
const MAX_ATTACHMENTS = 10;

type AttachmentResult = { kind: "image"; part: InputContent } | { kind: "text"; block: string };

/** One file → either a native vision content block, or an extracted-text block —
 * the per-file half of buildMessageContent below, split out so files can be
 * processed in parallel via Promise.all.
 */
async function resolveAttachment(
  file: File,
  vision: boolean,
  platformRegistryUrl: string,
  accessToken: string | null,
): Promise<AttachmentResult> {
  if (file.type.startsWith("image/") && vision) {
    const value = await fileToBase64(file);
    return {
      kind: "image",
      part: { type: "image", source: { type: "data", value, mimeType: file.type || "image/png" } },
    };
  }
  const extracted = await extractDocument(platformRegistryUrl, file, accessToken);
  return { kind: "text", block: `[Attached file: ${extracted.filename}]\n${extracted.text}` };
}

/**
 * Turn a typed message plus 0+ attachments (up to MAX_ATTACHMENTS) into the
 * `content` AG-UI's `agent.addMessage` expects. Each image goes straight through as
 * a native `ImageInputContent` block only when the scenario's active model is
 * vision-capable (see ChatModel.vision) — otherwise (any image without vision
 * support, or any pdf/docx/md/txt regardless of model) the file is sent to
 * platform-registry's session-only `/documents/extract` for best-effort text
 * extraction; every extracted block is folded into one plain-text string alongside
 * the typed message. See the ag_ui_langgraph.utils module comment on why a raw
 * DocumentInputContent block can't be used here: the installed adapter routes every
 * non-text media type through a LangChain `image_url` block, which is wrong for a
 * PDF/DOCX.
 */
async function buildMessageContent(
  text: string,
  files: File[],
  vision: boolean,
  platformRegistryUrl: string,
  accessToken: string | null,
): Promise<string | InputContent[]> {
  const results = await Promise.all(files.map((file) => resolveAttachment(file, vision, platformRegistryUrl, accessToken)));
  const imageParts = results.filter((r): r is { kind: "image"; part: InputContent } => r.kind === "image").map((r) => r.part);
  const textBlocks = results.filter((r): r is { kind: "text"; block: string } => r.kind === "text").map((r) => r.block);
  const combinedText = [text, ...textBlocks].filter(Boolean).join("\n\n");

  if (imageParts.length === 0) return combinedText;
  const parts: InputContent[] = [];
  if (combinedText) parts.push({ type: "text", text: combinedText });
  parts.push(...imageParts);
  return parts;
}

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

/** A user turn's `content` is `string | InputContent[]` (see buildMessageContent
 * above) — these two helpers pull out the plain text and any inline images
 * regardless of which shape a given turn used, so the render below doesn't need to
 * branch on it. An assistant turn's content is always a plain string. Typed as
 * `unknown` (not `AguiMessage["content"]`) because that property's type varies
 * per role across the `Message` union (e.g. ActivityMessage's content is a
 * `Record<string, unknown>`, not a string or array) — `Array.isArray` below narrows
 * it back down regardless of which shape actually showed up.
 */
function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return (content as InputContent[])
    .filter((c): c is { type: "text"; text: string } => c.type === "text")
    .map((c) => c.text)
    .join("\n");
}

function messageImages(content: unknown): { mimeType: string; value: string }[] {
  if (!Array.isArray(content)) return [];
  return (content as InputContent[])
    .filter((c): c is InputContent & { type: "image" } => c.type === "image")
    .map((c) => c.source)
    .filter((s): s is { type: "data"; value: string; mimeType: string } => s.type === "data")
    .map((s) => ({ mimeType: s.mimeType, value: s.value }));
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
  // A plain string, or (see AssistedFormView.tsx) a title plus an inline
  // `.chat-model-badge` — same pattern RagView.tsx/TabularView.tsx render in their
  // own headers, just placed inside ChatPanel's title bar for the fixed-column
  // (variant="full") case instead of a separate overlay header.
  title?: React.ReactNode;
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
  const [vision, setVision] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const historyRef = useRef<HTMLDivElement>(null);

  // Merges newly picked files onto whatever's already pending, capped at
  // MAX_ATTACHMENTS — a session can attach files across several "+" clicks, not
  // just the files chosen in one dialog; excess beyond the cap is silently dropped.
  function handleAttach(files: File[]) {
    setPendingFiles((prev) => [...prev, ...files].slice(0, MAX_ATTACHMENTS));
  }

  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    chatModel(baseUrl, scenarioSlug, accessToken)
      .then((model) => {
        setVision(model.vision);
        onModel?.(model);
      })
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
    const trimmed = text.trim();
    if ((!trimmed && pendingFiles.length === 0) || sending) return;
    const files = pendingFiles;
    setMessage("");
    setPendingFiles([]);
    setSending(true);
    setActivity(null);
    try {
      const content =
        files.length > 0 ? await buildMessageContent(trimmed, files, vision, config.platformRegistryUrl, accessToken) : trimmed;
      agent.addMessage({ id: randomUUID(), role: "user", content });
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
        {visible.map((turn) => {
          const text = messageText(turn.content);
          const images = messageImages(turn.content);
          return (
            <div key={turn.id} className={`chat-turn chat-turn--${turn.role}`}>
              <div className="chat-avatar">{turn.role === "user" ? "🧑" : "🤖"}</div>
              <div>
                {(text || images.length > 0) && (
                  <div className="chat-bubble">
                    {images.map((img) => (
                      <img
                        key={img.value.slice(0, 32)}
                        className="chat-attachment-preview"
                        src={`data:${img.mimeType};base64,${img.value}`}
                        alt="Attached"
                      />
                    ))}
                    {text && renderMarkdown(text)}
                    {turn.role === "assistant" && text && (
                      <SpeakerButton
                        text={text}
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
          );
        })}
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
      {pendingFiles.length > 0 && (
        <div className="chat-attachment-chips">
          {pendingFiles.map((file, index) => {
            const needsOcr = file.type.startsWith("image/") && !vision;
            return (
              <div
                key={`${file.name}-${index}`}
                className="chat-attachment-chip"
                title={needsOcr ? "Image will be read via OCR — the active model has no vision support." : undefined}
              >
                <span className="chat-attachment-chip-icon">📎</span>
                <span className="chat-attachment-chip-name">{file.name}</span>
                {needsOcr && <span className="chat-attachment-chip-note">OCR</span>}
                <button
                  type="button"
                  className="chat-attachment-chip-clear"
                  onClick={() => setPendingFiles((prev) => prev.filter((_, i) => i !== index))}
                  aria-label={`Remove ${file.name}`}
                >
                  ×
                </button>
              </div>
            );
          })}
        </div>
      )}
      <div className="chat-input-row">
        <MicButton agent={agent} voiceUrl={config.voiceUrl} scenarioSlug={scenarioSlug} accessToken={accessToken} />
        <AttachButton onAttach={handleAttach} disabled={sending || pendingFiles.length >= MAX_ATTACHMENTS} />
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send(message)}
          placeholder="Ask a question..."
          disabled={sending}
        />
        <button
          className="chat-send"
          onClick={() => send(message)}
          disabled={sending || (!message.trim() && pendingFiles.length === 0)}
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
