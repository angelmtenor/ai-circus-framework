# 🛡️ Insurance Retention & Next Best Offer - The Retention watchlist

Source: docs/scenarios/insurance_portfolio.md (this repository)

Every policyholder is scored by the lapse model and sorted into four tiers: *Loyal*, *Watch*, *At risk* and
*Likely to lapse*. The risk landscape plots the last renewal's price change against the total premium. Picking a
policyholder shows:

- the probability of lapsing and the tier's action (from the call before renewal to a retention offer);
- the SHAP drivers rolled up into four pillars: *Relationship*, *Cover held*, *Price and service*, *Profile*;
- each driver's percentile among all policyholders, next to the median of those who lapsed;
- the next best offer (below).

*Reveal* turns the watchlist into a backtest: who really lapsed. Policyholders are shown by id only.
