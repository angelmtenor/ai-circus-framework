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
import type { FormConfig, FormFieldSpec } from "./apiClient";

export type FormFieldUpdate = { field_id: string; value: string; confidence?: "confirmed" | "inferred"; source?: string };
export type UpdateFormFieldsArgs = { updates: FormFieldUpdate[] };

type LooseAction = Parameters<typeof useCopilotAction>[0];

// See chatGenerativeUi.tsx's identical note: a handler is mandatory even though this
// app never lets CopilotKit's own runtime invoke it — the real effect happens in
// ChatPanel's onFrontendToolCall, not here.
async function noopHandler(): Promise<void> {}

/** Every field of the form, shared and per-variant, by id — for readable chip labels. */
function fieldIndex(form: FormConfig | null | undefined): Map<string, FormFieldSpec> {
  const all = [...(form?.fields ?? []), ...(form?.variants ?? []).flatMap((v) => v.fields)];
  return new Map(all.map((f) => [f.id, f]));
}

function FormUpdateChip({ args, fields }: { args: UpdateFormFieldsArgs; fields: Map<string, FormFieldSpec> }) {
  const updates = args.updates ?? [];
  if (updates.length === 0) return null;
  const sources = [...new Set(updates.map((u) => u.source).filter(Boolean))];
  return (
    <div className="form-update-chip">
      <span className="form-update-chip-head">
        ✍️ {updates.length} {updates.length === 1 ? "field" : "fields"} filled
        {sources.length > 0 && <span className="form-update-chip-source"> · from {sources.join(", ")}</span>}
      </span>
      <span className="form-update-chip-list">
        {updates.map((u) => {
          const spec = fields.get(u.field_id);
          return (
            <span key={u.field_id} className="form-update-chip-item">
              {spec?.casilla && <b>{spec.casilla}</b>}
              {spec?.label ?? u.field_id}
            </span>
          );
        })}
      </span>
    </div>
  );
}

/**
 * Registers update_form_fields with the surrounding <CopilotKit> provider. Call once
 * per assisted_form workspace (AssistedFormView), alongside the useCopilotReadable
 * call that shares the form's current values/validation state with the same agent.
 */
export function useFormAssistActions(form?: FormConfig | null): void {
  const fields = fieldIndex(form);
  useCopilotAction({
    name: "update_form_fields",
    description:
      "Fill or update one or more fields of the form with values you've learned or confirmed from the " +
      "conversation or from an attached document. Call this as soon as you know a field's value — don't wait " +
      "until the end of the conversation, and call it again whenever the user corrects a value.",
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
          {
            name: "source",
            type: "string",
            required: false,
            description: "File name of the attached document this value was read from; omit if the user said it.",
          },
        ],
      },
    ],
    handler: noopHandler,
    render: (({ args }: { args: UpdateFormFieldsArgs }): ReactNode => <FormUpdateChip args={args} fields={fields} />) as never,
  } as LooseAction);
}
