import { useMemo, useState } from "react";
import { CopilotKit, useCopilotReadable } from "@copilotkit/react-core";
import type { ChatModel, ScenarioSummary } from "./apiClient";
import { submitForm } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { FormPanel } from "./FormPanel";
import { useFormAssistActions, type UpdateFormFieldsArgs } from "./formAssistUi";
import { validateForm } from "./formValidation";
import { useScenarioAgent } from "./useScenarioAgent";

/**
 * Generic assisted_form workspace, driven entirely by the scenario's `form` config
 * (see libs/shared/scenario_schema.py's FormConfig) — no scenario-specific code, so
 * this same component renders service_request or any future assisted_form scenario.
 *
 * Unlike tabular_ml's chat dock (an overlay, opened on demand), the assistant here is
 * a fixed column always visible next to the form — the point of this scenario kind
 * is that the conversation is the primary way the form gets filled in, not an
 * afterthought.
 */
export function AssistedFormView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const agent = useScenarioAgent(config.formAgentUrl, scenario.slug, accessToken);
  // See TabularView.tsx's identical note: must be memoized, or every re-render
  // resets CopilotKit's internal action/context registry.
  const selfManagedAgents = useMemo(() => ({ [scenario.slug]: agent }), [scenario.slug, agent]);

  return (
    <CopilotKit selfManagedAgents={selfManagedAgents}>
      <AssistedFormContent scenario={scenario} accessToken={accessToken} agent={agent} />
    </CopilotKit>
  );
}

function AssistedFormContent({
  scenario,
  accessToken,
  agent,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  agent: ReturnType<typeof useScenarioAgent>;
}) {
  useFormAssistActions();
  const form = scenario.form;

  const [values, setValues] = useState<Record<string, string>>({});
  const [assistantFilled, setAssistantFilled] = useState<ReadonlySet<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [caseNumber, setCaseNumber] = useState<string | null>(null);
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);

  const errors = form ? validateForm(form, values) : {};

  function handleChange(fieldId: string, value: string) {
    setValues((v) => ({ ...v, [fieldId]: value }));
    setAssistantFilled((prev) => {
      if (!prev.has(fieldId)) return prev;
      const next = new Set(prev);
      next.delete(fieldId);
      return next;
    });
  }

  function handleFrontendToolCall(name: string, args: unknown) {
    if (name !== "update_form_fields") return;
    const updates = (args as UpdateFormFieldsArgs).updates ?? [];
    if (updates.length === 0) return;
    setValues((v) => {
      const next = { ...v };
      for (const u of updates) next[u.field_id] = u.value;
      return next;
    });
    setAssistantFilled((prev) => {
      const next = new Set(prev);
      for (const u of updates) next.add(u.field_id);
      return next;
    });
  }

  async function handleSubmit() {
    if (!form) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await submitForm(config.formAgentUrl, scenario.slug, values, accessToken);
      if ("case_number" in result) {
        setCaseNumber(result.case_number);
      } else {
        setSubmitError("Some fields still need attention — see below.");
      }
    } catch (e) {
      setSubmitError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  // Dashboard → chat: what the assistant should already know without re-asking, and
  // what it should point out if the user asks why they can't submit yet.
  useCopilotReadable({
    description: `The ${scenario.title} form's current values and which required fields are still missing or invalid. Use this to avoid re-asking about fields already filled in correctly, and to explain concretely what's still needed.`,
    value: { values, missing_or_invalid: errors },
  });

  if (!form) {
    return <div className="app-loading">{scenario.title} has no form configured.</div>;
  }

  return (
    <div className="assisted-form-workspace">
      <FormPanel
        form={form}
        values={values}
        assistantFilled={assistantFilled}
        errors={caseNumber ? {} : errors}
        onChange={handleChange}
        onSubmit={handleSubmit}
        submitting={submitting}
        submitError={submitError}
        caseNumber={caseNumber}
      />
      <div className="assisted-form-chat">
        <ChatPanel
          agent={agent}
          baseUrl={config.formAgentUrl}
          scenarioSlug={scenario.slug}
          sampleQuestions={scenario.sample_questions}
          accessToken={accessToken}
          variant="full"
          title={
            <>
              Ask about {scenario.title}
              {chatModel && (
                <span className="chat-model-badge">
                  {chatModel.model}
                  {chatModel.provider && ` (${chatModel.provider})`}
                </span>
              )}
            </>
          }
          onModel={setChatModel}
          onFrontendToolCall={handleFrontendToolCall}
        />
      </div>
    </div>
  );
}
