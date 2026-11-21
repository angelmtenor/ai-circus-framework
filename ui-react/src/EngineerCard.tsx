import type { ScenarioSummary } from "./apiClient";
import { modelViews } from "./apiClient";
import { config } from "./config";
import { REPO_DOCS } from "./architectureCatalog";

const REPO = REPO_DOCS.replace(/docs\/$/, "");

/**
 * The Overview's card for engineers: where the scenario is declared, which service answers it and under which
 * routes, its models' addresses and the commands that build it. Read from the summary, nothing listed by hand.
 */
export function EngineerCard({ scenario }: { scenario: ScenarioSummary }) {
  const slug = scenario.slug;
  const views = modelViews(scenario);
  const routes: [string, string][] = [];
  if (scenario.kind === "tabular_ml") {
    routes.push([`POST ${config.predictionUrl}/predict/${slug}`, "score records, with SHAP"]);
    routes.push([`GET ${config.predictionUrl}/dataset/${slug}/sample`, "the Data & BI sample"]);
    routes.push([`GET ${config.predictionUrl}/model/${slug}/card`, "the model card"]);
    if (views.some((v) => v.task_type === "forecast")) routes.push([`GET ${config.predictionUrl}/forecast/${slug}`, "the stored forecast"]);
    if (views.some((v) => v.task_type === "recommendation")) routes.push([`GET ${config.predictionUrl}/recommend/${slug}~…`, "recommendations"]);
    routes.push([`POST ${config.assistantUrl}/agui/${slug}`, "the chat (AG-UI, streamed)"]);
    if (scenario.analysis_toolset === "drought_watch") routes.push([`GET ${config.assistantUrl}/drought/${slug}/control-room`, "the control room's views"]);
    if (scenario.rubric_check) routes.push([`POST ${config.assistantUrl}/rubric-check/${slug}`, "the LLM reviewer"]);
  } else if (scenario.kind === "conversational_rag") {
    routes.push([`POST ${config.ragAgentUrl}/agui/${slug}`, "the chat (AG-UI, streamed)"]);
    routes.push([`GET ${config.ragAgentUrl}/documents/${slug}`, "the indexed documents"]);
  } else if (scenario.kind === "assisted_form") {
    routes.push([`POST ${config.formAgentUrl}/agui/${slug}`, "the form conversation"]);
    routes.push([`POST ${config.formAgentUrl}/submissions/${slug}`, "submit the form"]);
  }
  if (scenario.mcp?.expose.length) routes.push([`MCP …/mcp/${slug}`, `read-only tools: ${scenario.mcp.expose.join(", ")}`]);
  const commands = [
    scenario.kind === "tabular_ml" && ["make k3s-pipeline", "clean the data and train every model"],
    (scenario.kind === "conversational_rag" || scenario.document_tool) && ["make k3s-vectorize", "index the documents"],
    scenario.text_challenger && [`make k3s-text-embeddings SCENARIOS=${slug}`, "embed the text for the challenger (host GPU)"],
    ["kubectl -n ai-circus rollout restart deployment/platform-registry", "re-seed after editing the YAML"],
  ].filter((c): c is string[] => Array.isArray(c));
  return (
    <section className="panel-card engineer-card">
      <h3>For engineers</h3>
      <dl>
        <dt>Declared in</dt>
        <dd>
          <a href={`${REPO}scenarios/${slug}/scenario.yaml`} target="_blank" rel="noreferrer">
            <code>scenarios/{slug}/scenario.yaml</code>
          </a>{" "}
          · kind <code>{scenario.kind}</code>
        </dd>
        {views.length > 1 && (
          <>
            <dt>Model addresses</dt>
            <dd>{views.map((v) => <code key={v.slug}>{v.slug}</code>)}</dd>
          </>
        )}
        <dt>Every call</dt>
        <dd>Bearer token checked by the service, then the tenant's entitlement with platform-registry; data keys carry the tenant.</dd>
      </dl>
      <h4>Routes</h4>
      <ul className="engineer-routes">
        {routes.map(([route, what]) => (
          <li key={route}>
            <code>{route}</code> <span>{what}</span>
          </li>
        ))}
      </ul>
      <h4>Commands</h4>
      <ul className="engineer-routes">
        {commands.map(([cmd, what]) => (
          <li key={cmd}>
            <code>{cmd}</code> <span>{what}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
