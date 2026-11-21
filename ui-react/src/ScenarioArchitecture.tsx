import { useMemo, useState } from "react";
import type { ScenarioSummary } from "./apiClient";
import { ArchitectureMap, ComponentDetail, FlowSteps } from "./ArchitectureMap";
import { CATALOG } from "./architectureCatalog";

/**
 * "How it's built" on a scenario's Overview: the platform map with the components this scenario uses lit (derived
 * from its scenario.yaml by platform-registry, never listed by hand), why each is there, and the requests that run
 * entirely inside them.
 */
export function ScenarioArchitecture({ scenario }: { scenario: ScenarioSummary }) {
  const view = scenario.architecture;
  const used = useMemo(() => new Set(view?.components ?? []), [view]);
  const [selected, setSelected] = useState<string | null>(view?.components.includes("prediction") ? "prediction" : (view?.components[0] ?? null));
  const flows = CATALOG.flows.filter((f) => view?.flows.includes(f.id));
  const [flowId, setFlowId] = useState<string | null>(null);
  const flow = flows.find((f) => f.id === flowId) ?? null;
  if (!view) return null;
  return (
    <section className="panel-card">
      <h3>How it's built</h3>
      <p className="panel-hint overview-lead">
        The {view.components.length} components this scenario uses, lit on the platform map. Nothing here is specific code: the scenario is a YAML
        file the same services read.
      </p>
      {flows.length > 0 && (
        <div className="arch-flows" role="group" aria-label="Follow a request">
          <span className="arch-flows-label">Follow a request</span>
          {flows.map((f) => (
            <button key={f.id} className={flowId === f.id ? "active" : ""} onClick={() => setFlowId(flowId === f.id ? null : f.id)}>
              {f.title}
            </button>
          ))}
        </div>
      )}
      <div className="arch-layout">
        <ArchitectureMap highlight={used} selected={selected} onSelect={setSelected} flow={flow} compact />
        <div className="arch-aside">
          {flow && (
            <section className="arch-flow-card">
              <h3>{flow.title}</h3>
              <FlowSteps flow={flow} onSelect={setSelected} />
            </section>
          )}
          {selected && <ComponentDetail id={selected} reason={view.reasons[selected] ?? "Not used by this scenario."} onSelect={setSelected} />}
        </div>
      </div>
    </section>
  );
}
