import { useMemo, useState } from "react";
import type { ScenarioSummary } from "./apiClient";
import { ArchitectureMap, ComponentDetail, FlowSteps } from "./ArchitectureMap";
import { CATALOG, REPO_DOCS } from "./architectureCatalog";
import { Icon } from "./Icon";

/**
 * The Architecture page, open to every signed-in user: the whole platform by layer from the architecture catalog,
 * a request followed step by step, and any component explained — what it does, what it is built with, what it
 * talks to, how to switch it on, its design decisions and which of the user's scenarios use it.
 */
export function ArchitectureView({ scenarios }: { scenarios: ScenarioSummary[] }) {
  const [selected, setSelected] = useState<string | null>("llm-gateway");
  const [flowId, setFlowId] = useState<string | null>(null);
  const flow = CATALOG.flows.find((f) => f.id === flowId) ?? null;
  const usedBy = useMemo(() => {
    if (!selected) return [];
    return scenarios.filter((s) => s.architecture?.components.includes(selected)).map((s) => ({ slug: s.slug, title: s.title, icon: s.icon }));
  }, [scenarios, selected]);

  return (
    <div className="tab-panel arch-page">
      <section className="panel-card arch-intro">
        <h2>How the framework is built</h2>
        <p>
          One cluster, four layers. At the bottom, Kubernetes runs everything. Above it, the data: object storage, databases, vectors and the
          jobs that clean data, train models and index documents. Then what people use: the web app and one service per kind of scenario. At
          the top, the doors every request passes: the ingress, sign-in, the entitlement check and the gateway every model and agent call goes
          through. Observability watches all of it.
        </p>
        <p className="panel-hint">
          A scenario is a YAML file: the same services serve every scenario of their kind, and each scenario's Overview shows the part it uses.
          Click a component to read it, or follow a request.{" "}
          <a href={`${REPO_DOCS}architecture/README.md`} target="_blank" rel="noreferrer">
            Architecture guide <Icon name="external" size={12} />
          </a>
        </p>
      </section>

      <section className="panel-card">
        <div className="arch-flows" role="group" aria-label="Follow a request">
          <span className="arch-flows-label">Follow a request</span>
          {CATALOG.flows.map((f) => (
            <button key={f.id} className={flowId === f.id ? "active" : ""} onClick={() => setFlowId(flowId === f.id ? null : f.id)}>
              {f.title}
            </button>
          ))}
        </div>
        <div className="arch-layout">
          <ArchitectureMap selected={selected} onSelect={setSelected} flow={flow} />
          <div className="arch-aside">
            {flow && (
              <section className="arch-flow-card">
                <h3>{flow.title}</h3>
                <p className="panel-hint">{flow.text}</p>
                <FlowSteps flow={flow} onSelect={setSelected} />
              </section>
            )}
            {selected && <ComponentDetail id={selected} usedBy={usedBy} onSelect={setSelected} />}
          </div>
        </div>
      </section>
    </div>
  );
}
