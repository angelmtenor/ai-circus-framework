# 🛡️ Insurance Retention & Next Best Offer - The models

Source: docs/scenarios/insurance_portfolio.md (this repository)

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
