import { useMemo, useState, type ReactNode } from "react";
import { CopilotKit, useCopilotAction, useCopilotReadable } from "@copilotkit/react-core";
import { createConversation, type ChatModel, type ScenarioSummary } from "./apiClient";
import { CATALOG, COMPONENTS } from "./architectureCatalog";
import { ChatPanel } from "./ChatPanel";
import { APP_ACTIONS } from "./chatProvenance";
import { config } from "./config";
import { useOpenWorkspace, type GuideAction, type GuidePage, type OpenWorkspace } from "./guideBridge";
import { Icon } from "./Icon";
import { missingServices, scenarioServices, useServiceAvailability, useUntrainedScenarios } from "./serviceAvailability";
import { useConversation } from "./useConversation";
import { useScenarioAgent } from "./useScenarioAgent";

/**
 * The app's guide (docs/adr/0032): one chat for the whole framework, opened from the top bar and kept beside every
 * page. It is the architecture tutorial's own agent in rag-agent (`config.guideScenarioSlug`): it answers from the
 * same documents, citing them, and acts on the screen through frontend tools — rank the gallery for a topic, open a
 * scenario on a tab, start its guided demo, open the Architecture page on a component or a request flow.
 *
 * It is bounded by the gallery: the App Context lists only the scenarios this user is entitled to, the tools only
 * accept those slugs, App checks the slug again before it opens anything, and each workspace's backend checks the
 * entitlement once more when it loads. Every model call goes through rag-agent, so through llm-gateway.
 */

// The guide's conversations among the tutorial's own (the title a first message never replaces).
const GUIDE_TITLE = "🧭 Framework guide";
const HEADLINE_CHARS = 120;
const QUERY_CHARS = 200;

type LooseAction = Parameters<typeof useCopilotAction>[0];
// See chatGenerativeUi.tsx: ChatPanel dispatches these itself, so the handler is never called.
async function noopHandler(): Promise<void> {}

function shorten(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, max - 1).trimEnd()}…`;
}

function headline(s: ScenarioSummary): string {
  return shorten(s.story?.headline ?? s.description.split(/(?<=\.)\s/)[0] ?? s.description, HEADLINE_CHARS);
}

/** One line per scenario the user can open: the guide's whole view of the gallery, and the bound on what it opens. */
function scenarioLines(scenarios: ScenarioSummary[], unavailable: ReadonlyMap<string, string>): string {
  return scenarios
    .map((s) => {
      const parts = [s.slug, `${s.icon} ${s.title}`, s.kind, s.industry, headline(s), s.story?.demo ? "guided demo" : "no guided demo"];
      const why = unavailable.get(s.slug);
      return [...parts, ...(why ? [`UNAVAILABLE: ${why}`] : [])].join(" | ");
    })
    .join("\n");
}

/**
 * The scenarios the gallery shows but will not open right now, and why: a required service is down, or (tabular) the
 * model is not trained for this tenant. The same checks and shared poll as ScenarioPicker.
 */
function useUnavailableScenarios(scenarios: ScenarioSummary[], accessToken: string | null): ReadonlyMap<string, string> {
  const needed = useMemo(() => scenarios.flatMap((s) => scenarioServices(s).required), [scenarios]);
  const availability = useServiceAvailability(needed);
  const tabular = useMemo(() => scenarios.filter((s) => s.kind === "tabular_ml").map((s) => s.slug), [scenarios]);
  const untrained = useUntrainedScenarios(tabular, availability.get("prediction") === "up", accessToken);
  const out = new Map<string, string>();
  for (const s of scenarios) {
    const missing = missingServices(s, availability);
    if (missing.length) out.set(s.slug, `its service is off (${missing.join(", ")})`);
    else if (untrained.has(s.slug)) out.set(s.slug, "its model is not trained for this tenant yet");
  }
  return out;
}

/** Where the user is, with the open workspace's tabs (story tab name and label) when it has published them. */
function whereLine(page: GuidePage, scenarios: ScenarioSummary[], workspace: OpenWorkspace | null): string {
  switch (page.kind) {
    case "gallery":
      return `The scenario gallery${page.query.trim() ? `, ranked for "${page.query.trim()}"` : ""}.`;
    case "architecture":
      return "The Architecture page (the platform map by layer, its components and request flows).";
    case "settings":
      return "Settings (appearance, model provider, voice).";
    case "platform":
      return "The Platform page (operations and monitoring, admin only).";
    case "scenario": {
      const s = scenarios.find((x) => x.slug === page.slug);
      const title = s ? `${s.icon} ${s.title}` : page.slug;
      if (workspace?.slug !== page.slug) return `Scenario ${page.slug} (${title}), still loading.`;
      const tabs = Object.entries(workspace.tabs)
        .map(([name, label]) => `${name} (${label})`)
        .join(", ");
      return `Scenario ${page.slug} (${title}). Its tabs, by name (label): ${tabs}.${workspace.demo ? " It has a guided demo." : ""}`;
    }
  }
}

// The Architecture page's components and flows, by id: what open_architecture may focus on.
const ARCHITECTURE_LINE =
  `Components (id: name): ${CATALOG.components.map((c) => `${c.id}: ${c.name}`).join("; ")}.\n` +
  `Request flows (id: title): ${CATALOG.flows.map((f) => `${f.id}: ${f.title}`).join("; ")}.`;

function text(value: unknown, max = QUERY_CHARS): string | null {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, max) : null;
}

/** A tool call as an action App may carry out — or null when it names nothing this user can open now. */
function guideAction(
  name: string,
  args: unknown,
  scenarios: ScenarioSummary[],
  unavailable: ReadonlyMap<string, string>,
): GuideAction | null {
  const a = (args && typeof args === "object" ? args : {}) as Record<string, unknown>;
  const scenario = (slug: string | null) =>
    slug && !unavailable.has(slug) ? (scenarios.find((s) => s.slug === slug) ?? null) : null;
  switch (name) {
    case APP_ACTIONS.search:
      return { kind: "search", query: text(a.query) ?? "" };
    case APP_ACTIONS.open: {
      const s = scenario(text(a.slug));
      return s ? { kind: "open", slug: s.slug, tab: text(a.tab, 40), demo: false } : null;
    }
    case APP_ACTIONS.demo: {
      const s = scenario(text(a.slug));
      return s?.story?.demo ? { kind: "open", slug: s.slug, tab: null, demo: true } : null;
    }
    case APP_ACTIONS.architecture: {
      const component = text(a.component, 60);
      const flow = text(a.flow, 60);
      return {
        kind: "architecture",
        component: component && COMPONENTS.has(component) ? component : null,
        flow: flow && CATALOG.flows.some((f) => f.id === flow) ? flow : null,
      };
    }
    default:
      return null;
  }
}

function GuideChip({ icon, children, refused = false }: { icon: string; children: ReactNode; refused?: boolean }) {
  return (
    <div className={`guide-action${refused ? " guide-action--refused" : ""}`}>
      <span aria-hidden="true">{icon}</span>
      <span>{children}</span>
    </div>
  );
}

/** Registers the app actions with the guide's own <CopilotKit>; the enums keep the model to this user's gallery. */
function useGuideActions(scenarios: ScenarioSummary[], unavailable: ReadonlyMap<string, string>): void {
  const slugs = scenarios.map((s) => s.slug);
  const demoSlugs = scenarios.filter((s) => s.story?.demo).map((s) => s.slug);
  const key = `${slugs.join(",")}/${[...unavailable.keys()].join(",")}`;
  const title = (slug: unknown) => {
    const s = scenarios.find((x) => x.slug === slug);
    return s ? `${s.icon} ${s.title}` : null;
  };
  const unavailableChip = (slug: unknown) => {
    const why = unavailable.get(String(slug));
    return why ? (
      <GuideChip icon="⏸" refused>
        {title(slug)} is unavailable: {why}
      </GuideChip>
    ) : null;
  };
  // An enum needs at least one value: an empty list leaves the parameter free, and App refuses what is not listed.
  const oneOf = (values: string[]) => (values.length ? { enum: values } : {});

  useCopilotAction(
    {
      name: APP_ACTIONS.search,
      description:
        "App action: show the scenario gallery ranked for a topic, by words and by meaning, so the user sees the scenarios " +
        "that match. Use it when the user asks to find, list or browse scenarios about something.",
      parameters: [
        {
          name: "query",
          type: "string",
          required: true,
          description: "The topic in the user's own words, e.g. 'fraud in insurance claims'. An empty string shows the whole gallery.",
        },
      ],
      handler: noopHandler,
      render: (({ args }: { args: { query?: string } }): ReactNode => (
        <GuideChip icon="🔎">{args.query?.trim() ? <>Gallery ranked for “{args.query.trim()}”</> : "Whole gallery shown"}</GuideChip>
      )) as never,
    } as LooseAction,
    [],
  );

  useCopilotAction(
    {
      name: APP_ACTIONS.open,
      description:
        "App action: open one of the user's scenarios, optionally on one of its tabs; also switches tab in the scenario " +
        "already open. Use only a slug from the App Context's scenario list.",
      parameters: [
        { name: "slug", type: "string", required: true, ...oneOf(slugs), description: "The scenario's slug." },
        {
          name: "tab",
          type: "string",
          required: false,
          description:
            "A tab name: one listed for the open scenario in the App Context, or a common one (overview, data, model, extra, " +
            "assistant, mcp). Leave it out to open the scenario's front page.",
        },
      ],
      handler: noopHandler,
      render: (({ args }: { args: { slug?: string; tab?: string } }): ReactNode => {
        const name = title(args.slug);
        if (!name) return <GuideChip icon="⚠️" refused>Not one of your scenarios: {String(args.slug ?? "")}</GuideChip>;
        const off = unavailableChip(args.slug);
        if (off) return off;
        return (
          <GuideChip icon="↗">
            Opened {name}
            {args.tab ? <em> · {args.tab}</em> : null}
          </GuideChip>
        );
      }) as never,
    } as LooseAction,
    [key],
  );

  useCopilotAction(
    {
      name: APP_ACTIONS.demo,
      description:
        "App action: open a scenario on its Overview and start its guided demo, a presenter's step-by-step walk through its " +
        "tabs. Only scenarios marked 'guided demo' in the App Context have one.",
      parameters: [{ name: "slug", type: "string", required: true, ...oneOf(demoSlugs), description: "The scenario's slug." }],
      handler: noopHandler,
      render: (({ args }: { args: { slug?: string } }): ReactNode => {
        const name = demoSlugs.includes(String(args.slug)) ? title(args.slug) : null;
        if (!name) return <GuideChip icon="⚠️" refused>No guided demo to start for {String(args.slug ?? "")}</GuideChip>;
        return unavailableChip(args.slug) ?? <GuideChip icon="▶">Guided demo of {name} started</GuideChip>;
      }) as never,
    } as LooseAction,
    [key],
  );

  useCopilotAction(
    {
      name: APP_ACTIONS.architecture,
      description:
        "App action: open the Architecture page, the platform's map by layer. Optionally select one component to read about " +
        "(an id from the App Context), or follow one request flow step by step (a flow id).",
      parameters: [
        { name: "component", type: "string", required: false, enum: CATALOG.components.map((c) => c.id), description: "A component id." },
        { name: "flow", type: "string", required: false, enum: CATALOG.flows.map((f) => f.id), description: "A request flow id." },
      ],
      handler: noopHandler,
      render: (({ args }: { args: { component?: string; flow?: string } }): ReactNode => {
        const flow = CATALOG.flows.find((f) => f.id === args.flow);
        const component = args.component ? COMPONENTS.get(args.component) : undefined;
        const focus = flow?.title ?? component?.name;
        return (
          <GuideChip icon="🏗️">
            Opened the Architecture page{focus ? <em> · {focus}</em> : null}
          </GuideChip>
        );
      }) as never,
    } as LooseAction,
    [],
  );
}

export function GuidePanel({
  scenario,
  scenarios,
  page,
  accessToken,
  hidden,
  onAction,
  onClose,
}: {
  // The conversational_rag scenario the guide answers from (config.guideScenarioSlug).
  scenario: ScenarioSummary;
  scenarios: ScenarioSummary[];
  page: GuidePage;
  accessToken: string | null;
  hidden: boolean;
  onAction: (action: GuideAction) => void;
  onClose: () => void;
}) {
  const conversation = useConversation(config.ragAgentUrl, scenario.slug, accessToken, GUIDE_TITLE);
  const agent = useScenarioAgent(config.ragAgentUrl, scenario.slug, conversation.conversationId, accessToken);
  // Memoized for the same reason as RagView's: a new object resets CopilotKit's registry of actions.
  const selfManagedAgents = useMemo(() => ({ [scenario.slug]: agent }), [scenario.slug, agent]);
  return (
    <aside className="guide-panel" hidden={hidden} aria-label="Framework guide">
      <CopilotKit selfManagedAgents={selfManagedAgents}>
        <GuideContent
          scenario={scenario}
          scenarios={scenarios}
          page={page}
          accessToken={accessToken}
          agent={agent}
          conversation={conversation}
          onAction={onAction}
          onClose={onClose}
        />
      </CopilotKit>
    </aside>
  );
}

function GuideContent({
  scenario,
  scenarios,
  page,
  accessToken,
  agent,
  conversation,
  onAction,
  onClose,
}: {
  scenario: ScenarioSummary;
  scenarios: ScenarioSummary[];
  page: GuidePage;
  accessToken: string | null;
  agent: ReturnType<typeof useScenarioAgent>;
  conversation: ReturnType<typeof useConversation>;
  onAction: (action: GuideAction) => void;
  onClose: () => void;
}) {
  const workspace = useOpenWorkspace();
  const [chatModel, setChatModel] = useState<ChatModel | null>(null);
  const [starting, setStarting] = useState(false);
  const unavailable = useUnavailableScenarios(scenarios, accessToken);
  useGuideActions(scenarios, unavailable);
  useCopilotReadable({
    description:
      "The scenarios this user can open, one per line: slug | title | kind | domain | headline | whether it has a " +
      "guided demo | UNAVAILABLE and why, when the gallery cannot open it now (say so instead of opening it)",
    value: scenarioLines(scenarios, unavailable),
  });
  useCopilotReadable({ description: "Where the user is now", value: whereLine(page, scenarios, workspace) });
  useCopilotReadable({ description: "The Architecture page's components and request flows", value: ARCHITECTURE_LINE });
  const forwardedProps = useMemo(() => ({ guide: true }), []);

  const samples = useMemo(() => {
    // A demo that opens: never the guide's own tutorial, and a product workspace before another document chat.
    const demos = scenarios.filter((s) => s.story?.demo && s.slug !== scenario.slug && !unavailable.has(s.slug));
    const demo = demos.find((s) => s.kind !== "conversational_rag") ?? demos[0];
    return [
      "Which of my scenarios would suit a bank, and why?",
      ...(demo ? [`Open ${demo.title} and start its guided demo`] : []),
      "Show me how a prediction request travels through the platform",
      "How does a new scenario become an app without new code?",
    ];
  }, [scenarios, scenario.slug, unavailable]);

  async function newConversation() {
    setStarting(true);
    try {
      conversation.onCreate(await createConversation(config.ragAgentUrl, scenario.slug, GUIDE_TITLE, accessToken));
    } catch {
      // The current conversation stays usable; the button can be pressed again.
    } finally {
      setStarting(false);
    }
  }

  return (
    <>
      <div className="guide-header">
        <span className="guide-title">
          <Icon name="compass" /> Guide
          {chatModel && (
            <span className="chat-model-badge">
              {chatModel.model}
              {chatModel.provider && ` (${chatModel.provider})`}
            </span>
          )}
        </span>
        <div className="guide-header-actions">
          <button className="guide-new" onClick={newConversation} disabled={starting || !conversation.ready} title="Start a new conversation with the guide">
            <Icon name="plus" size={14} /> New
          </button>
          <button className="chat-dock-close" onClick={onClose} aria-label="Close the guide" title="Close the guide">
            <Icon name="close" size={14} />
          </button>
        </div>
      </div>
      <p className="guide-hint">
        Ask how the platform is built, or ask it to find, open or demo a scenario: it answers from the framework's own documents and opens only
        what your gallery shows.
      </p>
      <ChatPanel
        agent={agent}
        baseUrl={config.ragAgentUrl}
        scenarioSlug={scenario.slug}
        sampleQuestions={samples}
        accessToken={accessToken}
        variant="full"
        onModel={setChatModel}
        initialMessages={conversation.initialMessages}
        conversationReady={conversation.ready}
        onFrontendToolCall={(name, args) => {
          const action = guideAction(name, args, scenarios, unavailable);
          if (action) onAction(action);
        }}
        forwardedProps={forwardedProps}
      />
    </>
  );
}
