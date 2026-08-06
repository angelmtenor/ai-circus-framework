import type { ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";

/**
 * conversational_rag workspace — a full-page ChatGPT-style window grounded in the
 * scenario's vectorized documents. rag-agent exposes no document-listing endpoint
 * (see api.py), so there is no real "browse the uploaded documents" list to show
 * here — the chat itself (with cited sources per reply) is the exploration surface.
 */
export function RagView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  return (
    <div className="workspace workspace--rag">
      <div className="rag-hero">
        <div className="rag-hero-icon">{scenario.icon}</div>
        <div>
          <h2>{scenario.title}</h2>
          <p>{scenario.description}</p>
        </div>
      </div>
      <div className="panel-card panel-card--chat panel-card--chat-full">
        <ChatPanel
          baseUrl={config.ragAgentUrl}
          scenarioSlug={scenario.slug}
          sampleQuestions={scenario.sample_questions}
          accessToken={accessToken}
          variant="full"
        />
      </div>
    </div>
  );
}
