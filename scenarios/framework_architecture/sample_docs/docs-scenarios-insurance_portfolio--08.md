# 🛡️ Insurance Retention & Next Best Offer - How it is built

Source: docs/scenarios/insurance_portfolio.md (this repository)

No service code is specific to this scenario. It is a `scenario.yaml` with two `extra_models`, plus one generic
addition to the watchlist renderer:

- `libs/shared` `NextBestOffer` (`ui_extras.risk_watchlist.next_best_offer`): the schema checks that the
  propensity model is a classification extra model over the same feature columns, that the recommender is a
  recommendation extra model, and that every holding is a numeric feature.
- `ui-react` `NextBestOfferPanel.tsx`: rendered by `RiskWatchlistView.tsx` for the selected entity when the
  block is present. It draws plain DOM, nothing animated.
- `industry: insurance`, a new domain in the scenario picker.
