import { useEffect, useRef, useSyncExternalStore } from "react";

/**
 * The link between the app's guide (GuidePanel.tsx, docs/adr/0032) and whichever scenario workspace is open.
 *
 * A workspace publishes the tabs it has (story tab name -> label, see scenario_schema.StoryConfig) and whether it has
 * a guided demo; the guide asks it to switch tab or start that demo. A tiny store outside React state, so no
 * workspace gets a new prop and App does not re-render for it. Every tab name is checked against what the workspace
 * published before it is opened, so a model's guess never leaves a workspace on a tab that does not exist.
 */

export type OpenWorkspace = { slug: string; tabs: Record<string, string>; demo: boolean };
export type WorkspaceRequest = { slug: string; tab?: string | null; demo?: boolean };

/** What the guide asks App to do: App owns the page (gallery, a scenario, Architecture), so it carries these out. */
export type GuideAction =
  | { kind: "search"; query: string }
  | { kind: "open"; slug: string; tab: string | null; demo: boolean }
  | { kind: "architecture"; component: string | null; flow: string | null };

/** Where the user is, for the guide's context. */
export type GuidePage =
  | { kind: "gallery"; query: string }
  | { kind: "scenario"; slug: string }
  | { kind: "architecture" }
  | { kind: "settings" }
  | { kind: "platform" };

// A request waits for its workspace to mount (a scenario and one of its tabs opened in one action), but not for long:
// a scenario opened by hand later must not replay it.
const REQUEST_TTL_MS = 10_000;

let open: OpenWorkspace | null = null;
let pending: (WorkspaceRequest & { at: number }) | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Ask the workspace of `slug` to open a tab or start its guided demo, now or as soon as it mounts. */
export function requestWorkspace(request: WorkspaceRequest): void {
  pending = { ...request, at: Date.now() };
  emit();
}

/** The workspace open right now (its slug, tabs and whether it has a guided demo), or null. */
export function useOpenWorkspace(): OpenWorkspace | null {
  return useSyncExternalStore(subscribe, () => open);
}

/** The tabs a workspace offers the guide: "overview" only when the scenario has a story to show there. */
export function guideTabs(tabs: Record<string, string>, hasStory: boolean): Record<string, string> {
  if (hasStory || !("overview" in tabs)) return tabs;
  return Object.fromEntries(Object.entries(tabs).filter(([name]) => name !== "overview"));
}

/**
 * Called by every scenario workspace: publishes its tabs while it is mounted and carries out the guide's requests
 * for it. `openTab` takes a story tab name; `startDemo` is absent when the scenario has no guided demo.
 */
export function useWorkspaceGuide(
  slug: string,
  tabs: Record<string, string>,
  openTab: (tab: string) => void,
  startDemo?: () => void,
): void {
  // Some workspaces build `tabs` afresh on every render: publish only when its content changes.
  const tabsKey = Object.entries(tabs)
    .map(([name, label]) => `${name}=${label}`)
    .join("|");
  const hasDemo = Boolean(startDemo);
  const latest = useRef({ tabs, openTab, startDemo });
  useEffect(() => {
    latest.current = { tabs, openTab, startDemo };
  });

  useEffect(() => {
    open = { slug, tabs: latest.current.tabs, demo: hasDemo };
    emit();
    return () => {
      if (open?.slug === slug) {
        open = null;
        emit();
      }
    };
  }, [slug, tabsKey, hasDemo]);

  useEffect(() => {
    function take(): void {
      const request = pending;
      if (!request || request.slug !== slug) return;
      pending = null;
      if (Date.now() - request.at > REQUEST_TTL_MS) return;
      const current = latest.current;
      if (request.demo && current.startDemo) current.startDemo();
      else if (request.tab && request.tab in current.tabs) current.openTab(request.tab);
    }
    take();
    return subscribe(take);
  }, [slug]);
}
