# 🛡️ Insurance Retention & Next Best Offer - The next best offer

Source: docs/scenarios/insurance_portfolio.md (this repository)

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
ui_extras:
  kind: risk_watchlist
  next_best_offer:
    propensity_model: cross_sell   # an extra model id: classification, same feature columns
    recommender: products          # an extra model id: recommendation
    holdings: {HasAuto: MOTOR, HasHome: HOME, HasHealth: HEALTH, ...}   # 0/1 feature -> item id
    at_risk_note: "Lapse risk is high: make the offer part of the retention call, not a separate campaign."
```
