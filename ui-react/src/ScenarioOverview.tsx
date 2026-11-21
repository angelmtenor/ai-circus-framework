import { lazy, Suspense } from "react";
import type { ScenarioStory, ScenarioSummary, StoryDataSource, StoryFlowStep } from "./apiClient";
import { modelViews } from "./apiClient";
import type { Audience } from "./audience";
import { EngineerCard } from "./EngineerCard";
import { Icon, type IconName } from "./Icon";
import { featureLabel } from "./predictUtils";
import { domainLabel } from "./ScenarioPicker";
import "./overview.css";

// Its own chunk: the architecture catalog only loads when an Overview reaches it.
const ScenarioArchitecture = lazy(() => import("./ScenarioArchitecture").then((m) => ({ default: m.ScenarioArchitecture })));

/** A workspace tab as a story names it (see scenario_schema.StoryConfig): what it is called, if it exists. */
export type StoryTabLabel = (tab: string) => string | null;

const FLOW_ICONS: Record<StoryFlowStep["kind"], IconName> = {
  source: "data",
  quality: "scan",
  rules: "shield",
  model: "target",
  decision: "gauge",
  person: "person",
  output: "mail",
  assistant: "chat",
};

const SOURCE_ICONS: Record<StoryDataSource["kind"], IconName> = {
  table: "data",
  series: "pulse",
  stream: "wave",
  messages: "mail",
  network: "network",
  documents: "book",
  images: "camera",
};

const ORIGIN_LABELS: Record<StoryDataSource["origin"], string> = {
  synthetic: "Synthetic",
  public: "Public dataset",
  official: "Official text",
};

const TASK_LABELS: Record<string, string> = {
  classification: "Classification",
  regression: "Regression",
  forecast: "Forecast",
  recommendation: "Recommendation",
};

type Governance = {
  declared?: boolean;
  intended_purpose?: string | null;
  owner?: string | null;
  human_oversight?: { mode?: string | null; description?: string | null } | null;
  limitations?: string[];
  known_risks?: string[];
};

/**
 * The Overview tab: the scenario told for someone with no context. With a `story:` block (see
 * scenario_schema.StoryConfig) it opens on the problem and who decides, today against with this, a
 * flow whose boxes open the tabs that show each step, where every input comes from, the question each
 * model answers and the limits; every scenario keeps its target, glossary and sample questions below.
 * Static DOM: nothing animates, so the tab stays quiet when idle.
 */
export function ScenarioOverview({
  scenario,
  audience = "analyst",
  tabLabel,
  onOpen,
  onAsk,
  onStartDemo,
}: {
  scenario: ScenarioSummary;
  // An executive reads the story without the glossary or the architecture; an engineer also gets the routes.
  audience?: Audience;
  tabLabel: StoryTabLabel;
  onOpen: (tab: string) => void;
  // Absent where the workspace has no chat (deep learning): no "ask" buttons then.
  onAsk?: (question: string) => void;
  onStartDemo?: () => void;
}) {
  const story = scenario.story ?? null;
  const governance = (scenario.governance ?? null) as Governance | null;
  const synthetic = story?.data_sources.some((s) => s.origin === "synthetic") ?? false;

  return (
    <div className="tab-panel overview">
      <section className="overview-hero panel-card">
        <div className="overview-eyebrow">
          <span className="overview-icon">{scenario.icon}</span>
          <span>{scenario.title}</span>
          <span className="overview-dot" />
          <span>{domainLabel(scenario.industry)}</span>
          {scenario.task_type && (
            <>
              <span className="overview-dot" />
              <span>{TASK_LABELS[scenario.task_type] ?? scenario.task_type}</span>
            </>
          )}
          {synthetic && <span className="overview-badge overview-badge--synthetic">Synthetic data</span>}
        </div>
        <h2 className="overview-headline">{story?.headline ?? scenario.title}</h2>
        <p className="overview-problem">{story?.problem ?? scenario.description}</p>
        {story && story.audience.length > 0 && (
          <div className="overview-audience">
            <span className="overview-audience-label">
              <Icon name="person" size={14} /> Used by
            </span>
            {story.audience.map((a) => (
              <span key={a} className="overview-chip">
                {a}
              </span>
            ))}
          </div>
        )}
        <div className="overview-actions">
          {story?.demo && onStartDemo && (
            <button className="btn-primary" onClick={onStartDemo}>
              <Icon name="play" size={14} /> Guided demo · {story.demo.minutes} min
            </button>
          )}
          {extraTabOf(tabLabel) && (
            <button className="btn-secondary" onClick={() => onOpen("extra")}>
              Open {tabLabel("extra")} <Icon name="arrow" size={14} />
            </button>
          )}
          {onAsk && (
            <button className="btn-secondary" onClick={() => onOpen("assistant")}>
              <Icon name="chat" size={14} /> Ask the assistant
            </button>
          )}
        </div>
        {!story && scenario.credits && <Credits scenario={scenario} />}
      </section>

      {story && (story.today || story.with_ai || story.value.length > 0) && <Change story={story} />}
      {story && <Flow story={story} tabLabel={tabLabel} onOpen={onOpen} />}
      {story && <Sources story={story} />}
      {story && <Models scenario={scenario} story={story} tabLabel={tabLabel} onOpen={onOpen} />}
      {governance?.declared && <Limits governance={governance} />}
      {scenario.architecture && audience !== "executive" && (
        <Suspense fallback={<div className="panel-hint">Loading the architecture…</div>}>
          <ScenarioArchitecture scenario={scenario} />
        </Suspense>
      )}
      {audience === "engineer" && <EngineerCard scenario={scenario} />}

      {audience !== "executive" && <Glossary scenario={scenario} open={audience === "engineer" || !scenario.story} />}

      {onAsk && scenario.sample_questions.length > 0 && (
        <section className="panel-card">
          <h3>Things you can ask the assistant</h3>
          <div className="overview-questions">
            {scenario.sample_questions.map((q) => (
              <button key={q} className="overview-question" onClick={() => onAsk(q)}>
                <Icon name="chat" size={14} /> {q}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

const extraTabOf = (tabLabel: StoryTabLabel) => tabLabel("extra") !== null;

function Credits({ scenario }: { scenario: ScenarioSummary }) {
  if (!scenario.credits) return null;
  return (
    <p className="panel-hint overview-credits">
      Dataset credit: {scenario.credits.source} —{" "}
      <a href={scenario.credits.url} target="_blank" rel="noreferrer">
        original source
      </a>
      {scenario.credits.note ? ` (${scenario.credits.note})` : ""}
    </p>
  );
}

function Change({ story }: { story: ScenarioStory }) {
  return (
    <section className="overview-change">
      {(story.today || story.with_ai) && (
        <div className="overview-contrast">
          {story.today && (
            <div className="overview-contrast-card overview-contrast-card--today">
              <span className="overview-contrast-label">Today</span>
              <p>{story.today}</p>
            </div>
          )}
          {story.today && story.with_ai && (
            <div className="overview-contrast-arrow" aria-hidden="true">
              <Icon name="arrow" size={22} />
            </div>
          )}
          {story.with_ai && (
            <div className="overview-contrast-card overview-contrast-card--with">
              <span className="overview-contrast-label">With this</span>
              <p>{story.with_ai}</p>
            </div>
          )}
        </div>
      )}
      {story.value.length > 0 && (
        <div className="overview-values">
          {story.value.map((v) => (
            <div key={v.label} className="overview-value">
              <span className="overview-value-number">{v.value}</span>
              <span className="overview-value-label">{v.label}</span>
              {v.note && <span className="overview-value-note">{v.note}</span>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Flow({ story, tabLabel, onOpen }: { story: ScenarioStory; tabLabel: StoryTabLabel; onOpen: (tab: string) => void }) {
  return (
    <section className="panel-card">
      <h3>How it works</h3>
      <p className="panel-hint overview-lead">From the data to the person who decides. Each step opens the tab that shows it.</p>
      <ol className="overview-flow" style={{ ["--flow-steps" as string]: story.flow.length }}>
        {story.flow.map((step, i) => {
          const label = step.tab ? tabLabel(step.tab) : null;
          const body = (
            <>
              <span className={`overview-flow-node overview-flow-node--${step.kind}`}>
                <Icon name={FLOW_ICONS[step.kind]} size={20} />
                <span className="overview-flow-index">{i + 1}</span>
              </span>
              <span className="overview-flow-title">{step.title}</span>
              <span className="overview-flow-text">{step.text}</span>
              {label && (
                <span className="overview-flow-link">
                  {label} <Icon name="arrow" size={12} />
                </span>
              )}
            </>
          );
          return (
            <li key={step.title} className={`overview-flow-step overview-flow-step--${step.kind}`}>
              {step.tab && label ? (
                <button className="overview-flow-button" onClick={() => onOpen(step.tab!)}>
                  {body}
                </button>
              ) : (
                <div className="overview-flow-button overview-flow-button--static">{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Sources({ story }: { story: ScenarioStory }) {
  return (
    <section className="panel-card">
      <h3>Where the data comes from</h3>
      <p className="panel-hint overview-lead">Every input, who produces it and what it feeds.</p>
      <div className="overview-sources">
        {story.data_sources.map((source) => (
          <article key={source.name} className={`overview-source overview-source--${source.origin}`}>
            <header>
              <span className="overview-source-icon">
                <Icon name={SOURCE_ICONS[source.kind]} size={18} />
              </span>
              <span className="overview-source-name">{source.name}</span>
              <span className={`overview-badge overview-badge--${source.origin}`}>{ORIGIN_LABELS[source.origin]}</span>
            </header>
            <p className="overview-source-feeds">{source.feeds}</p>
            <dl className="overview-source-meta">
              <dt>From</dt>
              <dd>
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noreferrer">
                    {source.provider}
                  </a>
                ) : (
                  source.provider
                )}
              </dd>
              {source.period && (
                <>
                  <dt>Period</dt>
                  <dd>{source.period}</dd>
                </>
              )}
              {source.size && (
                <>
                  <dt>Size</dt>
                  <dd>{source.size}</dd>
                </>
              )}
              {source.license && (
                <>
                  <dt>Licence</dt>
                  <dd>{source.license}</dd>
                </>
              )}
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

function Models({
  scenario,
  story,
  tabLabel,
  onOpen,
}: {
  scenario: ScenarioSummary;
  story: ScenarioStory;
  tabLabel: StoryTabLabel;
  onOpen: (tab: string) => void;
}) {
  const views = modelViews(scenario);
  if (story.models.length === 0 && views.length < 2) return null;
  return (
    <section className="panel-card">
      <h3>{views.length > 1 ? `The ${views.length} models` : "The model"}</h3>
      <p className="panel-hint overview-lead">The business question each one answers, and where to see it.</p>
      <div className="overview-models">
        {views.map((view, index) => {
          const id = index === 0 ? "main" : view.slug.split("~")[1];
          const modelTab = index === 0 ? "model" : `model:${id}`;
          const extraTab = index === 0 ? "extra" : `extra:${id}`;
          const told = story.models.find((m) => m.model === id);
          const title = index === 0 ? (scenario.main_model_title ?? "ML model") : view.title;
          const extraLabel = tabLabel(extraTab);
          const modelLabel = tabLabel(modelTab);
          return (
            <article key={view.slug} className="overview-model">
              <span className="overview-model-index">{index + 1}</span>
              <div className="overview-model-body">
                <h4>{title}</h4>
                {told && <p className="overview-model-question">{told.question}</p>}
                <p className="overview-model-meta">
                  {TASK_LABELS[view.task_type ?? ""] ?? view.task_type}
                  {view.target_label ? ` · predicts ${view.target_label}` : ""}
                  {told?.used_by ? ` · for ${told.used_by}` : ""}
                </p>
                <div className="overview-model-actions">
                  {modelLabel && (
                    <button className="btn-secondary" onClick={() => onOpen(modelTab)}>
                      <Icon name="target" size={14} /> {title}
                    </button>
                  )}
                  {extraLabel && (
                    <button className="btn-secondary" onClick={() => onOpen(extraTab)}>
                      {extraLabel} <Icon name="arrow" size={14} />
                    </button>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Limits({ governance }: { governance: Governance }) {
  const limits = governance.limitations ?? [];
  const oversight = governance.human_oversight?.description;
  if (!governance.intended_purpose && !oversight && limits.length === 0) return null;
  return (
    <section className="panel-card overview-limits">
      <h3>Limits and human oversight</h3>
      <div className="overview-limits-grid">
        {governance.intended_purpose && (
          <div>
            <h4>What it is for</h4>
            <p>{governance.intended_purpose}</p>
          </div>
        )}
        {oversight && (
          <div>
            <h4>Who decides</h4>
            <p>{oversight}</p>
          </div>
        )}
        {limits.length > 0 && (
          <div>
            <h4>What it is not</h4>
            <ul>
              {limits.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function Glossary({ scenario, open }: { scenario: ScenarioSummary; open: boolean }) {
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const classLabels = scenario.target_value_labels ? Object.entries(scenario.target_value_labels) : [];
  if (!scenario.target && featureColumns.length === 0) return null;
  return (
    <section className="panel-card">
      <details className="overview-glossary" open={open}>
        <summary>
          <h3>
            What the {modelViews(scenario).length > 1 ? "first model" : "model"} predicts, and its {featureColumns.length} inputs
          </h3>
        </summary>
        {scenario.target && (
          <p className="overview-target">
            <strong>{scenario.target_label ?? scenario.target}</strong>
            {scenario.target_units ? ` (${scenario.target_units})` : ""} <code>{scenario.target}</code>
            {scenario.target_description && <span className="panel-hint"> — {scenario.target_description}</span>}
            {classLabels.length > 0 && <span className="panel-hint"> Classes: {classLabels.map(([raw, label]) => `${label} (${raw})`).join(", ")}.</span>}
          </p>
        )}
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Input</th>
                <th>Column</th>
                <th>What it measures</th>
              </tr>
            </thead>
            <tbody>
              {featureColumns.map((f) => {
                const spec = featureSchema[f];
                return (
                  <tr key={f}>
                    <td>{featureLabel(scenario, f)}</td>
                    <td>
                      <code>{f}</code>
                    </td>
                    <td className="overview-glossary-info">{spec?.info ?? (spec?.type === "categorical" ? spec.options.join(", ") : "—")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
