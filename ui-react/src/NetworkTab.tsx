import { lazy, Suspense, useState } from "react";
import type { ScenarioSummary } from "./apiClient";
import { EMPTY_DRAFT, type Draft } from "./networkInvestigator";
import "./networkInvestigator.css";

const NetworkExplorerView = lazy(() => import("./NetworkExplorerView").then((m) => ({ default: m.NetworkExplorerView })));
const NetworkInvestigatorView = lazy(() => import("./NetworkInvestigatorView").then((m) => ({ default: m.NetworkInvestigatorView })));

/**
 * The `network_explorer` extra tab: two workspaces on the same scored network — the
 * whole-network map (NetworkExplorerView) and a person-centred investigation
 * (NetworkInvestigatorView) — with the investigated person and the analyst's
 * hypothetical individual held here, so moving between them loses nothing.
 */
export function NetworkTab({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [workspace, setWorkspace] = useState<"map" | "investigate">("map");
  const [focusId, setFocusId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);

  const investigate = (id: string | null) => {
    if (id) setFocusId(id);
    setDraft((d) => ({ ...d, active: false }));
    setWorkspace("investigate");
  };

  return (
    <div className="ni-tab">
      <div className="ni-workspaces nx-seg" role="tablist" aria-label="Network workspace">
        {(
          [
            ["map", "Network map"],
            ["investigate", "Investigate a person"],
          ] as const
        ).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={workspace === key} className={workspace === key ? "nx-seg--on" : ""} onClick={() => setWorkspace(key)}>
            {label}
          </button>
        ))}
      </div>
      <Suspense fallback={<div className="app-loading">Loading…</div>}>
        {workspace === "map" ? (
          <NetworkExplorerView scenario={scenario} accessToken={accessToken} onInvestigate={investigate} />
        ) : (
          <NetworkInvestigatorView
            scenario={scenario}
            accessToken={accessToken}
            focusId={focusId}
            onFocus={setFocusId}
            draft={draft}
            onDraft={setDraft}
            onOpenMap={() => setWorkspace("map")}
          />
        )}
      </Suspense>
    </div>
  );
}
