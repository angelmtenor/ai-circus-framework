# 🕵️ Payment Fraud & Financial Crime - The models

Source: docs/scenarios/financial_crime_desk.md (this repository)

Both are LightGBM with SHAP explanations and out-of-fold scores (each row scored by a model that never saw it),
which the two tabs use.

| Model | Rows | ROC AUC, hold-out | ROC AUC, out of fold |
|---|---|---|---|
| Transfer screening (TF-IDF + LightGBM, deployed) | 3,000 | 0.925 | 0.952 |
| Transfer screening challenger (voyage-4-nano embeddings + LightGBM) | 3,000 | 0.934 | — |
| Payment fraud | 4,868 (132 stopped by a blocking rule) | 0.945 | 0.952 |
| Mule accounts (logistic regression: Green Code picks the simpler model within 0.01) | 800 | 0.962 (5-fold CV) | 0.958 |

The transfer model reads both the structured fields and the words: on this data neither alone reaches the
combined score. The challenger scores slightly higher on the hold-out and the model card says so, but it is not
promoted on its own: a person decides, after weighing its cost (an embedding call per message).
