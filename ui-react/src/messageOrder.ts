import type { Message as AguiMessage } from "@ag-ui/core";

/**
 * The transcript with every tool result right after the assistant message that called it.
 *
 * @ag-ui/client merges a run's MESSAGES_SNAPSHOT into the messages it streamed by keeping
 * its own order and appending messages it does not know at the end. ag_ui_langgraph gives a
 * backend tool's result one id while streaming and another in the snapshot, so that result
 * lands after the reply that used it. Sent back as history on the next turn, that order
 * makes the backend drop the tool call: the model then "forgets" it used the tool and the
 * earlier reply loses its sources. A result whose call is not in the transcript stays put.
 */
export function toolResultsAfterTheirCalls(messages: AguiMessage[]): AguiMessage[] {
  const called = new Set<string>();
  for (const m of messages) if (m.role === "assistant") for (const call of m.toolCalls ?? []) called.add(call.id);
  const results = new Map<string, AguiMessage>();
  for (const m of messages) if (m.role === "tool" && called.has(m.toolCallId) && !results.has(m.toolCallId)) results.set(m.toolCallId, m);

  const ordered: AguiMessage[] = [];
  for (const m of messages) {
    if (m.role === "tool" && called.has(m.toolCallId)) continue;
    ordered.push(m);
    if (m.role === "assistant") {
      for (const call of m.toolCalls ?? []) {
        const result = results.get(call.id);
        if (result) ordered.push(result);
      }
    }
  }
  return ordered;
}

/** Whether two transcripts hold the same messages in the same order. */
export function sameOrder(a: AguiMessage[], b: AguiMessage[]): boolean {
  return a.length === b.length && a.every((m, i) => m.id === b[i].id);
}
