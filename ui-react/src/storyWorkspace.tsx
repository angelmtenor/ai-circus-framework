import { useCallback, useMemo, useState, type ReactNode } from "react";
import type { ScenarioSummary } from "./apiClient";
import { guideTabs, useWorkspaceGuide } from "./guideBridge";
import { StoryDemoBar } from "./StoryDemoBar";

/**
 * A scenario's guided demo (`story.demo`) in any workspace: `tabs` names the story tabs this workspace has (story
 * tab name -> its label, see scenario_schema.ScenarioDefinition.story_tabs) and `open` switches to one. Returns
 * what the Overview needs (`tabLabel`, `startDemo`) and the demo bar to render. TabularView keeps its own wiring
 * (several models, sections); the other kinds use this. The same tabs and demo are offered to the app's guide.
 */
export function useStoryDemo(
  scenario: ScenarioSummary,
  tabs: Record<string, string>,
  open: (tab: string) => void,
  ask?: (question: string) => void,
): { tabLabel: (tab: string) => string | null; startDemo?: () => void; demoBar: ReactNode; demoActive: boolean } {
  const [index, setIndex] = useState<number | null>(null);
  const demo = scenario.story?.demo ?? null;
  const tabLabel = useCallback((tab: string) => (tab === "assistant" && ask ? (tabs.assistant ?? "Assistant") : (tabs[tab] ?? null)), [tabs, ask]);
  function go(i: number) {
    if (!demo) return;
    const at = Math.max(0, Math.min(i, demo.steps.length - 1));
    setIndex(at);
    open(demo.steps[at].tab);
  }
  const demoBar =
    demo && index !== null ? (
      <StoryDemoBar steps={demo.steps} index={index} onIndex={go} onExit={() => setIndex(null)} onAsk={(q) => ask?.(q)} tabLabel={tabLabel} />
    ) : null;
  const startDemo = demo ? () => go(0) : undefined;
  const hasStory = Boolean(scenario.story);
  const offered = useMemo(() => guideTabs(tabs, hasStory), [tabs, hasStory]);
  useWorkspaceGuide(scenario.slug, offered, open, startDemo);
  return { tabLabel, startDemo, demoBar, demoActive: index !== null };
}
