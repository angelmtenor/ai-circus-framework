import { useEffect, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { fetchRecommendations, predict, type NextBestOffer, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { explainByFeature, featureLabel, type Record_ } from "./predictUtils";

/**
 * The watchlist's "next best offer" for the selected entity (ui_extras.risk_watchlist.next_best_offer):
 * the same record scored by another classifier of the scenario (propensity to buy, with its SHAP
 * drivers), and the products a basket recommender says go with what the entity already holds.
 * Both models are extra models of the same scenario, so the caller's one entitlement covers them;
 * a part whose model is not there (not trained, refused) is left out, never guessed. Remount it per
 * entity (`key`): it fetches once, on mount, and draws nothing in between.
 */

type Propensity = { probability: number; drivers: { feature: string; value: number }[] };
type Suggestion = { item: string; label: string; score: number; share: number; becauseOf: string };
type Products = { suggestions: Suggestion[]; held: string[] };

const DRIVERS = 4;

function pct(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

function pct0(p: number): string {
  return `${Math.round(p * 100)}%`;
}

function points(v: number): string {
  return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`;
}

// The model address of one of this scenario's extra models (docs/adr/0018: `slug~id`).
function address(scenario: ScenarioSummary, modelId: string): string {
  return `${scenario.slug.split("~")[0]}~${modelId}`;
}

export function NextBestOfferPanel({
  scenario,
  offer,
  record,
  noun,
  atRisk,
  accessToken,
}: {
  scenario: ScenarioSummary;
  noun: string;
  offer: NextBestOffer;
  record: Record_;
  atRisk: boolean;
  accessToken: string | null;
}) {
  const [propensity, setPropensity] = useState<Propensity | null>(null);
  const [products, setProducts] = useState<Products | null>(null);
  const [missing, setMissing] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    const fail = (part: string) => () => live && setMissing((prev) => [...prev, part]);

    predict(config.predictionUrl, address(scenario, offer.propensity_model), [record], accessToken)
      .then((response) => {
        const p = response.predictions[0];
        const drivers = explainByFeature(scenario.feature_columns ?? [], p.prediction, p.contributions)
          .items.sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
          .slice(0, DRIVERS);
        if (live) setPropensity({ probability: p.prediction, drivers });
      })
      .catch(fail("propensity"));

    const recommender = offer.recommender;
    if (recommender) {
      const held = Object.entries(offer.holdings)
        .filter(([feature]) => Number(record[feature]) === 1)
        .map(([, item]) => item);
      Promise.all(held.map((item) => fetchRecommendations(config.predictionUrl, address(scenario, recommender), item, accessToken)))
        .then((answers) => {
          // Rank as training scores the recommender (summed over every product held); explain each
          // suggestion by its strongest single source. A score is the share of the baskets holding the
          // source product that also hold this one (association rules), or its smoothed version.
          const ranked = new Map<string, Suggestion>();
          for (const answer of answers) {
            for (const entry of answer.candidates[answer.selected] ?? []) {
              if (held.includes(entry.item)) continue;
              const current = ranked.get(entry.item);
              const strongest = !current || entry.score > current.share;
              ranked.set(entry.item, {
                item: entry.item,
                label: entry.label,
                score: (current?.score ?? 0) + entry.score,
                share: strongest ? entry.score : current.share,
                becauseOf: strongest ? answer.label : current.becauseOf,
              });
            }
          }
          const suggestions = [...ranked.values()].sort((a, b) => b.score - a.score).slice(0, offer.top_k);
          if (live) setProducts({ suggestions, held: answers.map((answer) => answer.label) });
        })
        .catch(fail("products"));
    }
    return () => {
      live = false;
    };
  }, [scenario, offer, record, accessToken]); // keyed by entity id, so in practice this runs once per entity

  // The chat sees the same offer as the user, so it can explain it ("why Pet for this customer?").
  useCopilotReadable({
    description: `${offer.title} for the ${noun} the user is inspecting in the watchlist: products to offer (ranked by the recommender; share = of the customers holding goes_with, how many also hold the product) and the cross-sell model's propensity to buy with its top SHAP drivers (points of probability).`,
    value: {
      products: products?.suggestions.map((s) => ({ product: s.label, goes_with: s.becauseOf, share: Number(s.share.toFixed(2)) })) ?? null,
      holds_today: products?.held ?? null,
      propensity_to_buy: propensity ? Number(propensity.probability.toFixed(3)) : null,
      propensity_drivers: propensity?.drivers.map((d) => ({ feature: d.feature, points: Number((d.value * 100).toFixed(1)) })) ?? null,
    },
  });

  const top = products?.suggestions[0];
  return (
    <div className="rw-offer">
      <h5>{offer.title}</h5>
      <div className="rw-offer-grid">
        <div>
          {top && products ? (
            <p className="rw-offer-headline">
              Offer <strong>{top.label}</strong>
              <span className="rw-offer-why">
                {" "}
                — held by {pct0(top.share)} of the customers who hold {top.becauseOf}
              </span>
            </p>
          ) : products ? (
            <p className="rw-none">Nothing the recommender pairs with what this {noun} holds.</p>
          ) : (
            !missing.includes("products") && offer.recommender && <p className="rw-none">Finding products…</p>
          )}
          {products && products.suggestions.length > 1 && (
            <p className="rw-offer-also">
              Also: {products.suggestions.slice(1).map((s) => `${s.label} (${pct0(s.share)} of ${s.becauseOf} holders)`).join(" · ")}
            </p>
          )}
          {products && <p className="rw-legend-line">Holds today: {products.held.join(", ") || "—"}</p>}
          {atRisk && offer.at_risk_note && <p className="rw-profile-action">{offer.at_risk_note}</p>}
        </div>
        <div>
          {propensity ? (
            <>
              <div className="rw-offer-score">
                <span className="rw-offer-value">{pct(propensity.probability)}</span>
                <span className="rw-hero-label">{offer.score_label}</span>
              </div>
              {propensity.drivers.map((d) => (
                <div key={d.feature} className="rw-driver rw-offer-driver">
                  <span className="rw-driver-name">{featureLabel(scenario, d.feature)}</span>
                  <span className="rw-driver-value">{String(record[d.feature])}</span>
                  <span className={d.value >= 0 ? "rw-offer-up" : "rw-offer-down"}>{points(d.value)}</span>
                </div>
              ))}
              <p className="rw-legend-line">
                <span className="rw-offer-up">■</span> pushes towards buying <span className="rw-offer-down">■</span> away (SHAP)
              </p>
            </>
          ) : (
            !missing.includes("propensity") && <p className="rw-none">Scoring the propensity to buy…</p>
          )}
        </div>
      </div>
      {missing.length > 0 && (
        <p className="rw-legend-line">
          Not available: {missing.join(" and ")} model — train it with <code>make k3s-pipeline</code>.
        </p>
      )}
    </div>
  );
}
