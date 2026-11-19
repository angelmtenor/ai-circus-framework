# 🛡️ Insurance Retention & Next Best Offer

A multi-line insurer's portfolio (motor, home, health, life, pet, funeral and personal accident) with three
models in one scenario (`insurance_portfolio`, [ADR 0018](../adr/0018-several-models-per-scenario.md)):

| Model | Address | Question | Kind |
|---|---|---|---|
| Lapse risk (main) | `insurance_portfolio` | Will this policyholder lapse at renewal? | LightGBM classification, SHAP |
| Cross-sell | `insurance_portfolio~cross_sell` | Will they add a product after a campaign? | LightGBM classification, SHAP |
| Product affinity | `insurance_portfolio~products` | Which products are held together? | Association rules and item factors |

**The data is synthetic.** `scripts/generate_insurance_portfolio.py` invents 5,000 policyholders of a fictional
insurer. No real person, insurer or market is behind the numbers, so the metrics illustrate the method, not a
real book of business.

## Contents

- [The Retention watchlist](#the-retention-watchlist)
- [The next best offer](#the-next-best-offer)
- [The Cross-sell leads tab](#the-cross-sell-leads-tab)
- [The data](#the-data)
- [The models](#the-models)
- [How it is built](#how-it-is-built)
- [Run it yourself](#run-it-yourself)

## The Retention watchlist

Every policyholder is scored by the lapse model and sorted into four tiers: *Loyal*, *Watch*, *At risk* and
*Likely to lapse*. The risk landscape plots the last renewal's price change against the total premium. Picking a
policyholder shows:

- the probability of lapsing and the tier's action (from the call before renewal to a retention offer);
- the SHAP drivers rolled up into four pillars: *Relationship*, *Cover held*, *Price and service*, *Profile*;
- each driver's percentile among all policyholders, next to the median of those who lapsed;
- the next best offer (below).

*Reveal* turns the watchlist into a backtest: who really lapsed. Policyholders are shown by id only.

## The next best offer

The retention watchlist's `next_best_offer` brings the scenario's other two models to the selected
policyholder, so the person making the retention call sees what to offer in the same panel:

- **What to offer.** The recommender is asked what goes with each product the policyholder holds; products they
  already have are left out, and the rest are ranked by their score summed over everything held (as training
  scores the recommender). The score is a share: of the customers holding one product, how many also hold the
  other. The panel says why with the strongest one: "held by 56% of the customers who hold Personal accident".
- **How likely they are to buy.** The cross-sell model scores the very same record (the schema checks that both
  models read the same feature columns) and shows its top SHAP drivers.
- **When the lapse risk is high,** a note suggests making the offer part of the retention call.

Both are ordinary calls to prediction for models of the same scenario, so the caller's single entitlement covers
them. If either model is missing (not trained yet) its part is left out and the panel says so.

```yaml
# scenarios/insurance_portfolio/scenario.yaml (abridged)
ui_extras:
  kind: risk_watchlist
  next_best_offer:
    propensity_model: cross_sell   # an extra model id: classification, same feature columns
    recommender: products          # an extra model id: recommendation
    holdings: {HasAuto: MOTOR, HasHome: HOME, HasHealth: HEALTH, ...}   # 0/1 feature -> item id
    at_risk_note: "Lapse risk is high: make the offer part of the retention call, not a separate campaign."
```

## The Cross-sell leads tab

The cross-sell model's own watchlist (`positive_is_adverse: false`, so the tiers use the favourable ramp):
*Cold*, *Warm*, *Hot* and *Ready to buy*, with the same pillar explanation and backtest reveal. The *Product
affinity* tab shows the recommenders side by side for any product.

## The data

One row per policyholder, 17 features shared by both classifiers:

| Group | Features |
|---|---|
| Relationship | `TenureYears`, `NumPolicies`, `Channel` (Online, Agent, Branch, Phone), `DigitalActive` |
| Cover held | `HasAuto`, `HasHome`, `HasHealth`, `HasLife`, `HasPet`, `HasFuneral`, `HasAccident` (0/1) |
| Price and service | `AnnualPremium`, `PremiumChangePct`, `ClaimsLast3Y`, `LatePayments` |
| Profile | `Age`, `Region` |

The outcomes are designed logistic formulas:

- **Lapse** rises with the renewal price increase, late payments, claims, a high premium and the phone channel,
  and falls with tenure, bundled policies, use of the digital area and an agent relationship (30.5% lapsed).
- **Adding a product** rises with digital engagement, tenure, an agent and an obvious cover gap for the
  customer's age, and falls with late payments, the lines already held and a lapse (46.6% added one).
- **Products are bundled** the way a real book tends to be: pet cover mostly with home, personal accident with
  motor, life with health, funeral with life after 55. The recommender's baskets are each policyholder's
  holdings (10,964 rows over 5,000 baskets).

## The models

LightGBM, selected by ROC AUC on a 20% hold-out (the training job's numbers, shown on each model card), and a
5-fold cross-validation of the same model on the committed sample as a check:

| Model | Hold-out ROC AUC | 5-fold CV ROC AUC | CV accuracy | Majority-class baseline |
|---|---|---|---|---|
| Lapse | 0.78 | 0.785 | 0.76 | 0.70 |
| Cross-sell | 0.82 | 0.827 | 0.75 | 0.53 |

The recommender compares association rules with trained item factors by how often a product hidden from a
policyholder's holdings is in the top 3 for the rest (`top_k: 3`: with seven products a longer list would find
every hidden product and the comparison would mean nothing). Association rules are selected, hit rate 0.90.

The sample is 5,000 policyholders on purpose: the watchlists score every row with the trained model, and on
1,200 rows LightGBM memorised enough of them to push most scores to the extremes, leaving the middle tiers empty.

## How it is built

No service code is specific to this scenario. It is a `scenario.yaml` with two `extra_models`, plus one generic
addition to the watchlist renderer:

- `libs/shared` `NextBestOffer` (`ui_extras.risk_watchlist.next_best_offer`): the schema checks that the
  propensity model is a classification extra model over the same feature columns, that the recommender is a
  recommendation extra model, and that every holding is a numeric feature.
- `ui-react` `NextBestOfferPanel.tsx`: rendered by `RiskWatchlistView.tsx` for the selected entity when the
  block is present. It draws plain DOM, nothing animated.
- `industry: insurance`, a new domain in the scenario picker.

## Run it yourself

```bash
python scripts/generate_insurance_portfolio.py   # only to regenerate the committed samples
kubectl -n ai-circus rollout restart deployment/platform-registry   # seeds the scenario
make k3s-pipeline                                 # trains every model, this scenario's three included
```

Then open **Insurance Retention & Next Best Offer** in the picker (domain *Insurance*).

## Credits

Synthetic data generated for this framework. No third-party dataset.
