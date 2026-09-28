import { useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { rubricCheck, type RubricCheckConfig, type RubricCheckResult, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { renderMarkdown } from "./markdown";
import { initialRecord } from "./predictUtils";
import { ModelDuel, scoreTextRecord, TextFeatureExplanations, type TextScore } from "./textModels";
import "./rubric.css";

/**
 * Generic "rubric check" for a tabular_ml scenario with a `rubric_check` block (see
 * scenario_schema.RubricCheckConfig) — toxic_leadership's Leadership check. The user
 * describes what someone *does*; three readers answer side by side:
 * - an LLM coach (assistant POST /rubric-check, the active model via llm-gateway)
 *   reading it against the YAML rubric, quoting the description as evidence;
 * - the deployed TF-IDF model and the transformer challenger, scoring the same text as
 *   the scenario's text feature (other features at their defaults), with highlights.
 * It assesses the described behaviour, never a person — the copy says so, the prompt
 * enforces it.
 */
export function RubricCheckPanel({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const rubric = scenario.rubric_check as RubricCheckConfig;
  const featureColumns = scenario.feature_columns ?? [];
  const featureSchema = scenario.feature_schema ?? {};
  const textSpec = featureSchema[rubric.text_feature];
  const maxModelChars = textSpec?.type === "text" ? textSpec.max_length : rubric.max_chars;
  const [text, setText] = useState("");
  const [llm, setLlm] = useState<RubricCheckResult | null>(null);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [score, setScore] = useState<TextScore | null>(null);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [record, setRecord] = useState<Record<string, number | string> | null>(null);
  const [running, setRunning] = useState(false);
  const [showRubric, setShowRubric] = useState(false);

  useCopilotReadable({
    description: `The ${rubric.title} the user just ran: their description of a ${rubric.subject_noun}'s behaviour, the LLM rubric reading and the trained models' probabilities.`,
    value: llm || score ? { description: text, rubric_reading: llm, champion: score?.champion.prediction, challenger: score?.challenger?.prediction } : null,
  });

  async function analyse() {
    const description = text.trim();
    if (description.length < 10) return;
    setRunning(true);
    setLlm(null);
    setScore(null);
    setLlmError(null);
    setScoreError(null);
    // The models read it as the text feature of an otherwise average record.
    const modelRecord = { ...initialRecord(featureColumns, featureSchema), [rubric.text_feature]: description.slice(0, maxModelChars) };
    setRecord(modelRecord);
    await Promise.all([
      rubricCheck(config.assistantUrl, scenario.slug, description, accessToken).then(setLlm, (e: Error) => setLlmError(e.message)),
      scoreTextRecord(scenario, modelRecord, accessToken, true).then(setScore, (e: Error) => setScoreError(e.message)),
    ]);
    setRunning(false);
  }

  const contextNote = featureColumns
    .filter((f) => f !== rubric.text_feature)
    .map((f) => `${featureSchema[f]?.label ?? f} ${String(featureSchema[f]?.default)}`)
    .join(" · ");

  return (
    <div className="panel-card rubric">
      <div className="rubric-head">
        <h3>🧭 {rubric.title}</h3>
        <button className="btn-secondary" onClick={() => setShowRubric((s) => !s)}>
          {showRubric ? "Hide the rubric" : "Show the rubric"}
        </button>
      </div>
      <div className="rubric-intro">{renderMarkdown(rubric.intro)}</div>
      {showRubric && <RubricReference rubric={rubric} />}

      {rubric.examples.length > 0 && (
        <div className="rubric-examples">
          <span className="panel-hint">Try one:</span>
          {rubric.examples.map((example) => (
            <button key={example.label} className="filter-chip" onClick={() => setText(example.text)}>
              {example.label}
            </button>
          ))}
        </div>
      )}
      <label className="rubric-input">
        <span className="feature-input-label">
          {rubric.input_label}{" "}
          <span className="feature-input-range">
            {text.length}/{rubric.max_chars}
          </span>
        </span>
        <textarea
          rows={5}
          maxLength={rubric.max_chars}
          placeholder={rubric.placeholder ?? undefined}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <button className="btn-primary" onClick={analyse} disabled={running || text.trim().length < 10}>
        {running ? "Reading…" : "Analyse the behaviour"}
      </button>

      {(llm || llmError || score || scoreError) && (
        <div className="rubric-results">
          <div className="rubric-card rubric-card--llm">
            <h4>LLM coach — research-based rubric</h4>
            {llm ? <LlmReading reading={llm} rubric={rubric} /> : llmError ? <p className="error">{llmError}</p> : <div className="app-loading">Reading…</div>}
          </div>
          <div className="rubric-card">
            <h4>Trained on 4,000 engineers' reviews</h4>
            {score && record ? (
              <>
                <ModelDuel scenario={scenario} score={score} />
                <TextFeatureExplanations scenario={scenario} record={record} result={score.champion} />
                <p className="panel-hint">
                  Scored as a review's text with an average context ({contextNote}). The models learned what reviewers <em>say</em> about
                  leadership — a different lens from the rubric.
                </p>
              </>
            ) : scoreError ? (
              <p className="error">{scoreError}</p>
            ) : (
              <div className="app-loading">Scoring…</div>
            )}
          </div>
        </div>
      )}
      <p className="panel-hint rubric-caveat">
        One description is one perspective, not proof — use this to reflect, to prepare a conversation (a one-to-one, a skip-level, HR),
        never as a verdict on a person. Nothing you type here is stored.
      </p>
    </div>
  );
}

function LlmReading({ reading, rubric }: { reading: RubricCheckResult; rubric: RubricCheckConfig }) {
  const positive = reading.behaviours.filter((b) => b.polarity === "positive");
  const negative = reading.behaviours.filter((b) => b.polarity === "negative");
  return (
    <div className="rubric-reading">
      <div className="rubric-verdict-row">
        <span className={`rubric-verdict rubric-verdict--${reading.verdict}`}>{reading.verdict_label}</span>
        <div className="rubric-balance" title={`Balance ${reading.balance} (−100 = entirely ${rubric.negative_label}, +100 = entirely ${rubric.positive_label})`}>
          <span className="rubric-balance-label">{rubric.negative_label}</span>
          <div className="rubric-balance-track">
            <span className="rubric-balance-marker" style={{ left: `${(reading.balance + 100) / 2}%` }} />
          </div>
          <span className="rubric-balance-label">{rubric.positive_label}</span>
        </div>
      </div>
      {reading.summary && <p>{reading.summary}</p>}
      <div className="rubric-behaviours">
        <BehaviourList title={`🚩 ${rubric.negative_label} signals`} items={negative} kind="negative" />
        <BehaviourList title={`✅ ${rubric.positive_label} signals`} items={positive} kind="positive" />
      </div>
      {reading.advice.length > 0 && (
        <>
          <h5>What you could do</h5>
          <ul className="rubric-advice">
            {reading.advice.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      )}
      <p className="panel-hint">Read by {reading.model} (the platform's active model — change it in Settings).</p>
    </div>
  );
}

function BehaviourList({ title, items, kind }: { title: string; items: RubricCheckResult["behaviours"]; kind: "positive" | "negative" }) {
  return (
    <div className={`rubric-list rubric-list--${kind}`}>
      <h5>{title}</h5>
      {items.length === 0 ? (
        <p className="panel-hint">None described.</p>
      ) : (
        <ul>
          {items.map((b) => (
            <li key={b.key}>
              <strong>{b.name}</strong> <span className={`rubric-strength rubric-strength--${b.strength}`}>{b.strength}</span>
              {b.evidence && (
                <blockquote className={b.verified ? undefined : "rubric-unverified"} title={b.verified ? "Quoted from your description" : "Not found verbatim in your description"}>
                  “{b.evidence}” {!b.verified && <small>(not a verbatim quote)</small>}
                </blockquote>
              )}
              {b.note && <span className="panel-hint">{b.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RubricReference({ rubric }: { rubric: RubricCheckConfig }) {
  return (
    <div className="rubric-reference">
      <div>
        <h5>✅ {rubric.positive_label}</h5>
        <ul>
          {rubric.positive.map((b) => (
            <li key={b.key}>
              <strong>{b.name}</strong> — {b.description}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h5>🚩 {rubric.negative_label}</h5>
        <ul>
          {rubric.negative.map((b) => (
            <li key={b.key}>
              <strong>{b.name}</strong> — {b.description}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
