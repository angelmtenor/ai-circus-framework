import { useEffect, useMemo, useRef, useState } from "react";
import { CopilotKit, useCopilotReadable } from "@copilotkit/react-core";
import type { ChatModel, ScenarioSummary } from "./apiClient";
import { draftFormPdf, sampleUpload, submissionPdf, submitForm } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";
import { ConversationSidebar } from "./ConversationSidebar";
import { FormPanel } from "./FormPanel";
import { OfficialFormSheet } from "./OfficialFormSheet";
import { useConversation } from "./useConversation";
import { useFormAssistActions, type UpdateFormFieldsArgs } from "./formAssistUi";
import { selectedVariant, validateForm } from "./formValidation";
import { useScenarioAgent } from "./useScenarioAgent";

type PdfPreview = { url: string; filename: string; title: string };

/** A generated PDF shown in-app (the browser's own viewer) with a download link. */
function PdfPreviewOverlay({ preview, onClose }: { preview: PdfPreview; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="of-pdf-overlay" role="dialog" aria-modal="true" aria-label={preview.title} onClick={onClose}>
      <div className="of-pdf-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="of-pdf-bar">
          <strong>📄 {preview.title}</strong>
          <a href={preview.url} download={preview.filename}>
            ⤓ Download
          </a>
          <a href={preview.url} target="_blank" rel="noreferrer">
            Open in new tab ↗
          </a>
          <button type="button" className="of-btn of-btn--ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <iframe src={preview.url} title={preview.title} />
      </div>
    </div>
  );
}

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
  const conversation = useConversation(config.formAgentUrl, scenario.slug, accessToken);
  const agent = useScenarioAgent(config.formAgentUrl, scenario.slug, conversation.conversationId, accessToken);
  // See TabularView.tsx's identical note: must be memoized, or every re-render
  // resets CopilotKit's internal action/context registry.
  const selfManagedAgents = useMemo(() => ({ [scenario.slug]: agent }), [scenario.slug, agent]);

  return (
    <CopilotKit selfManagedAgents={selfManagedAgents}>
      <AssistedFormContent scenario={scenario} accessToken={accessToken} agent={agent} conversation={conversation} />
    </CopilotKit>
  );
}

function AssistedFormContent({
  scenario,
  accessToken,
  agent,
  conversation,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  agent: ReturnType<typeof useScenarioAgent>;
  conversation: ReturnType<typeof useConversation>;
}) {
  const form = scenario.form;
  useFormAssistActions(form);

  const [values, setValues] = useState<Record<string, string>>({});
  const [assistantFilled, setAssistantFilled] = useState<ReadonlySet<string>>(new Set());
  // Which attached document each assistant-filled value was read from (if any).
  const [sources, setSources] = useState<Record<string, string>>({});
  // The fields the assistant's latest update wrote — highlighted for a moment.
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const freshTimer = useRef<number | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [caseNumber, setCaseNumber] = useState<string | null>(null);
  const [submittedAt, setSubmittedAt] = useState<Date | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfPreview, setPdfPreview] = useState<PdfPreview | null>(null);
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0);

  const errors = form ? validateForm(form, values) : {};

  // The form is per-request, not per-scenario-mount: switching to a new or different
  // conversation (ConversationSidebar's "+ New conversation" / picking a past one)
  // must not leave a previous request's filled-in values or "Submitted" banner
  // showing, or it reads as though the new request had already been sent.
  useEffect(() => {
    setValues({});
    setAssistantFilled(new Set());
    setSources({});
    setFresh(new Set());
    setSubmitting(false);
    setSubmitError(null);
    setCaseNumber(null);
    setSubmittedAt(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation.conversationId]);

  useEffect(() => () => window.clearTimeout(freshTimer.current), []);
  useEffect(
    () => () => {
      if (pdfPreview) URL.revokeObjectURL(pdfPreview.url);
    },
    [pdfPreview],
  );

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
      for (const u of updates) next[u.field_id] = String(u.value ?? "");
      return next;
    });
    setAssistantFilled((prev) => {
      const next = new Set(prev);
      for (const u of updates) next.add(u.field_id);
      return next;
    });
    setSources((prev) => {
      const next = { ...prev };
      for (const u of updates) {
        if (u.source) next[u.field_id] = u.source;
        else delete next[u.field_id];
      }
      return next;
    });
    setFresh(new Set(updates.map((u) => u.field_id)));
    window.clearTimeout(freshTimer.current);
    freshTimer.current = window.setTimeout(() => setFresh(new Set()), 2600);
  }

  async function handleSubmit() {
    if (!form) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await submitForm(config.formAgentUrl, scenario.slug, values, accessToken);
      if ("case_number" in result) {
        setCaseNumber(result.case_number);
        setSubmittedAt(new Date());
      } else {
        setSubmitError("Some fields still need attention — see below.");
      }
    } catch (e) {
      setSubmitError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  async function showPdf(load: () => Promise<Blob>, filename: string, title: string) {
    setPdfBusy(true);
    setSubmitError(null);
    try {
      const blob = await load();
      setPdfPreview({ url: URL.createObjectURL(blob), filename, title });
    } catch (e) {
      setSubmitError((e as Error).message);
    } finally {
      setPdfBusy(false);
    }
  }

  // Dashboard → chat: what the assistant should already know without re-asking, and
  // what it should point out if the user asks why they can't submit yet.
  const variant = form ? selectedVariant(form, values) : null;
  useCopilotReadable({
    description: `The ${scenario.title} form's current values and which required fields are still missing or invalid. Use this to avoid re-asking about fields already filled in correctly, and to explain concretely what's still needed.`,
    value: {
      ...(variant ? { selected_model: `${variant.code} — ${variant.title}` } : {}),
      values,
      missing_or_invalid: errors,
      ...(caseNumber ? { submitted_case_number: caseNumber } : {}),
    },
  });

  if (!form) {
    return <div className="app-loading">{scenario.title} has no form configured.</div>;
  }

  const official = (form.sections ?? []).length > 0;
  const pdfTitle = variant ? `${variant.code} · ${variant.title}` : form.title;
  return (
    <div className={`assisted-form-workspace${official ? " assisted-form-workspace--official" : ""}`}>
      {official ? (
        <OfficialFormSheet
          form={form}
          values={values}
          errors={caseNumber ? {} : errors}
          assistantFilled={assistantFilled}
          sources={sources}
          fresh={fresh}
          onChange={handleChange}
          onSubmit={handleSubmit}
          submitting={submitting}
          submitError={submitError}
          caseNumber={caseNumber}
          submittedAt={submittedAt}
          pdfBusy={pdfBusy}
          onDraftPdf={() =>
            showPdf(() => draftFormPdf(config.formAgentUrl, scenario.slug, values, accessToken), `borrador-${scenario.slug}.pdf`, pdfTitle)
          }
          onReceiptPdf={() =>
            caseNumber &&
            showPdf(() => submissionPdf(config.formAgentUrl, scenario.slug, caseNumber, accessToken), `${caseNumber}.pdf`, `${pdfTitle} · ${caseNumber}`)
          }
        />
      ) : (
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
      )}
      {pdfPreview && <PdfPreviewOverlay preview={pdfPreview} onClose={() => setPdfPreview(null)} />}
      <div className="assisted-form-chat">
        <ConversationSidebar
          baseUrl={config.formAgentUrl}
          scenarioSlug={scenario.slug}
          accessToken={accessToken}
          activeConversationId={conversation.conversationId}
          onSelect={conversation.selectConversation}
          onCreate={conversation.onCreate}
          onDeleteActive={conversation.onDeleteActive}
          refreshKey={sidebarRefreshKey}
          compact
        />
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
          initialMessages={conversation.initialMessages}
          conversationReady={conversation.ready}
          onRunFinished={() => setSidebarRefreshKey((k) => k + 1)}
          sampleUploads={form.sample_uploads ?? []}
          onLoadSample={(file) => sampleUpload(config.formAgentUrl, scenario.slug, file, accessToken)}
        />
      </div>
    </div>
  );
}
