/**
 * A recommendation model's two views (`model.task_type: recommendation`, docs/adr/0018): Predictions
 * asks what goes with an item and shows every candidate side by side; Insights shows how each did on
 * items hidden from baskets it never saw, next to simply listing the most popular items.
 */
import { useEffect, useState } from "react";
import {
  fetchRecommendableItems,
  fetchRecommendations,
  modelCard,
  type ModelCard,
  type RecommendableItem,
  type Recommendations,
  type ScenarioSummary,
} from "./apiClient";
import { config } from "./config";
import { StatTile } from "./charts";

const CANDIDATES: Record<string, { label: string; score: string; about: string }> = {
  association_rules: {
    label: "Association rules",
    score: "Also in its baskets",
    about: "Counted directly: the share of this product's baskets that also hold the other one.",
  },
  item_factors: {
    label: "Trained item factors",
    score: "Expected in its baskets",
    about: "Learned from every basket: a smoothed estimate of that share, so two items can match without sharing many baskets.",
  },
};

const candidateLabel = (name: string) => CANDIDATES[name]?.label ?? name;
const percent = (value: number | undefined) => (value === undefined ? "—" : `${(value * 100).toFixed(1)}%`);

export function RecommenderPredictView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [items, setItems] = useState<RecommendableItem[]>([]);
  const [item, setItem] = useState<string | null>(null);
  const [result, setResult] = useState<Recommendations | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchRecommendableItems(config.predictionUrl, scenario.slug, accessToken)
      .then((list) => {
        setItems(list);
        setItem(list[0]?.item ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, [scenario.slug, accessToken]);

  useEffect(() => {
    if (!item) return;
    fetchRecommendations(config.predictionUrl, scenario.slug, item, accessToken)
      .then(setResult)
      .catch((e: Error) => setError(e.message));
  }, [scenario.slug, item, accessToken]);

  if (error) return <div className="tab-panel"><div className="error">Could not load the recommender: {error}</div></div>;
  if (!result) return <div className="tab-panel"><div className="panel-hint">Loading the recommender…</div></div>;

  return (
    <div className="tab-panel">
      <div className="panel-card">
        <h3>What goes with this?</h3>
        <select aria-label="Item" value={item ?? ""} onChange={(e) => setItem(e.target.value)}>
          {items.map((option) => (
            <option key={option.item} value={option.item}>
              {option.label} ({option.item}), in {option.baskets} baskets
            </option>
          ))}
        </select>
      </div>
      <div className="recommender-columns">
        {Object.entries(result.candidates).map(([name, list]) => (
          <div className="panel-card" key={name}>
            <h3>
              {candidateLabel(name)}
              {name === result.selected && <span className="scenario-meta-pill"> selected</span>}
            </h3>
            <p className="panel-hint">{CANDIDATES[name]?.about}</p>
            {list.length === 0 ? (
              <p className="panel-hint">Nothing clears this model's bar for {result.label} yet.</p>
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>{CANDIDATES[name]?.score ?? "Score"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((entry) => (
                      <tr key={entry.item}>
                        <td>{entry.label}</td>
                        <td>{percent(entry.score)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function RecommenderInsightsView({ scenario, accessToken }: { scenario: ScenarioSummary; accessToken: string | null }) {
  const [card, setCard] = useState<ModelCard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    modelCard(config.predictionUrl, scenario.slug, accessToken).then(setCard).catch((e: Error) => setError(e.message));
  }, [scenario.slug, accessToken]);

  if (error) return <div className="tab-panel"><div className="error">Could not load the model card: {error}</div></div>;
  if (!card) return <div className="tab-panel"><div className="panel-hint">Loading the model card…</div></div>;

  return (
    <div className="tab-panel">
      <div className="kpi-row">
        <StatTile label="Selected" value={candidateLabel(card.model_name)} sub="the simplest that holds up" />
        <StatTile label="Hit rate" value={percent(card.metrics.hit_rate)} sub="hidden item found in the top list" />
        <StatTile label="Most popular items" value={percent(card.metrics.popularity_hit_rate)} sub="the score to beat" />
        <StatTile label="Baskets" value={String(card.training_rows ?? "—")} />
      </div>
      <div className="panel-card">
        <h3>How each candidate did</h3>
        <p className="panel-hint">
          One item is hidden from each held-out basket; a hit means the rest of the basket finds it in the top list.
          Being bought together does not mean one causes the other.
        </p>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Hit rate</th>
              </tr>
            </thead>
            <tbody>
              {card.candidates.map((candidate) => (
                <tr key={candidate.name}>
                  <td>{candidateLabel(candidate.name)}</td>
                  <td>{percent(candidate.metrics.hit_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
