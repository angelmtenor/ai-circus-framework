/**
 * The one frontend "tool" an assisted_form scenario's agent can call to fill the
 * form — registered the same way as chatGenerativeUi.tsx's render_chart/render_table
 * (via useCopilotAction, dispatched by ChatPanel itself, see its top-of-file note),
 * but unlike those two, this one has a real effect outside the chat bubble:
 * ChatPanel's `onFrontendToolCall` callback (see AssistedFormView.tsx) is what
 * actually writes the values into the form's state — `render` below is only the
 * small "updated" chip shown inline in the transcript, not the mechanism itself.
 */
import { useCopilotAction } from "@copilotkit/react-core";
import type { ReactNode } from "react";

export type FormFieldUpdate = { field_id: string; value: string; confidence?: "confirmed" | "inferred" };
export type UpdateFormFieldsArgs = { updates: FormFieldUpdate[] };

type LooseAction = Parameters<typeof useCopilotAction>[0];

// See chatGenerativeUi.tsx's identical note: a handler is mandatory even though this
// app never lets CopilotKit's own runtime invoke it — the real effect happens in
// ChatPanel's onFrontendToolCall, not here.
async function noopHandler(): Promise<void> {}

function FormUpdateChip({ args }: { args: UpdateFormFieldsArgs }) {
  const updates = args.updates ?? [];
  if (updates.length === 0) return null;
  return (
    <div className="form-update-chip">
      ✅ Updated: {updates.map((u) => u.field_id).join(", ")}
    </div>
  );
}

/**
 * Registers update_form_fields with the surrounding <CopilotKit> provider. Call once
 * per assisted_form workspace (AssistedFormView), alongside the useCopilotReadable
 * call that shares the form's current values/validation state with the same agent.
 */
export function useFormAssistActions(): void {
  useCopilotAction({
    name: "update_form_fields",
    description:
      "Fill or update one or more fields of the form with values you've learned or confirmed from the " +
      "conversation. Call this as soon as you know a field's value — don't wait until the end of the " +
      "conversation, and call it again whenever the user corrects a value.",
    parameters: [
      {
        name: "updates",
        type: "object[]",
        required: true,
        attributes: [
          { name: "field_id", type: "string", required: true, description: "Must match one of the form's field ids." },
          { name: "value", type: "string", required: true },
          {
            name: "confidence",
            type: "string",
            enum: ["confirmed", "inferred"],
            required: false,
            description: "'confirmed' if the user stated this explicitly, 'inferred' if you deduced it.",
          },
        ],
      },
    ],
    handler: noopHandler,
    render: (({ args }: { args: UpdateFormFieldsArgs }): ReactNode => <FormUpdateChip args={args} />) as never,
  } as LooseAction);
}
