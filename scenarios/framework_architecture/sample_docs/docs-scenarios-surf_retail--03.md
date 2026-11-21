# 🏄 Angel's Surf Co. — a governed data platform (`data_product`) - Two models on the gold marts

Source: docs/scenarios/surf_retail.md (this repository)

A gold **feature mart** is exported by `data-transform` after each Postgres build as the raw CSV of
an ordinary `tabular_ml` scenario (`ml_exports`), so the models are trained by the same
`etl-tabular` → `training` pipeline as every other scenario — `make k3s-data-ml` once a transform
has run.

| Scenario | Task | Predicts |
|---|---|---|
| `surf_ml` | LightGBM regression | Units sold next week per shop and category: season, recent weeks, the same week last year; explained with SHAP |
| `surf_ml~recommender` | Association rules and trained item factors, side by side | What goes with a product, from a committed sample of anonymous baskets (`scripts/prepare_surf_ml_samples.py`), so it runs without the data tier; the web shop keeps its dbt rules |
| `surf_ml~buyers` | LightGBM classification | Whether a loyalty customer buys a board in the next 90 days: recency, frequency, spend; customers are pseudonymised keys only |

The assistant calls them through its `ml_predict` tool, next to its governed data and catalogue
tools — it never estimates a figure the tools did not return.
