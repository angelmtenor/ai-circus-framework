# 🚢 Titanic Survival — an explained ML tutorial - The data science

Source: docs/scenarios/titanic.md (this repository)

### Preparation

[`scripts/prepare_titanic_dataset.py`](../../scripts/prepare_titanic_dataset.py) turns the
verbatim Kaggle file (checked in next to the scenario, SHA-256-pinned) into the model-ready
`sample_data/titanic.csv` that `etl-tabular` seeds:

| Step | Why |
|---|---|
| **Title** parsed from `Name` (Mr / Mrs / Miss / Master / Rare) | One feature carrying sex, age group (a *Master* is a boy), marital status and rank. It is the strongest single feature. |
| **Age**: 177 missing values filled with the median of the same title *and* class | A missing "Master" in 3rd class is a child, not the overall median of 28. (Fitted on all rows: a small, disclosed leak the tutorial points out.) |
| **TicketGroup**: passengers sharing a ticket number | Families, friends and servants who travelled, and often died, together |
| **FarePerPerson** = fare / TicketGroup | `Fare` is per ticket, so a big family on a cheap ticket looked rich |
| **Cabin dropped** | 77% missing, and recorded far more often for survivors: a label leak |
| `Pclass` / `Embarked` given readable labels | `1st`, `Southampton`, …; the two missing ports are filled with Southampton |
| `Name` kept as a **display column** only | Shown and searchable in the UI, never a model input |

### Model selection

Both candidates share one scikit-learn pipeline (impute → standardise numbers → one-hot encode
categories → estimator):

1. A stratified **80/20 split** sets aside 179 passengers that no decision ever touches.
2. **5-fold stratified cross-validation** on the other 712 ranks the candidates by mean ROC AUC.
   One 179-row hold-out can swing a metric by ±0.03 on its own; five folds cannot.
3. **Green Code**: the complex model is adopted only if it beats the simpler one by more than
   **0.01 AUC**.
4. The winner is scored on the hold-out, **then** refit on all 891 passengers for deployment.

| Candidate | 5-fold CV ROC AUC | Hold-out ROC AUC | Hold-out accuracy | |
|---|---|---|---|---|
| Logistic regression | 0.872 ± 0.016 | 0.855 | 82.1% | |
| **LightGBM (small data)** | **0.885 ± 0.015** | **0.873** | 82.1% | ✓ deployed (+0.013 > 0.01) |
| *2018 Keras DNN (for reference)* | – | 0.86 | 82.7% | |

`lightgbm_small_data` is a new, reusable training candidate:
300 trees, learning rate 0.03, 8 leaves, depth 4, `min_child_samples` 10, 80% row and 70%
column subsampling, L2 = 2. The plain `lightgbm` defaults memorise a table this small: 0.860
CV ROC AUC against 0.885.

How the feature work paid off (CV ROC AUC, same boosted model): about 0.873 with the raw columns,
**0.885** with Title and the travelling-party features. Adding the cabin (or a "has cabin" flag)
gained nothing and would have leaked the label. Scores in the high 0.80s are close to the
ceiling for this data without leaky features such as the survival of a passenger's own ticket
group.

On the hold-out, at a 50% cut-off: 99 true negatives, 11 false positives, 21 false negatives,
48 true positives. Globally, **title, sex, ticket class, fare per person and age** carry most
of the model's SHAP importance.

### Honest numbers

The metrics above are the **model card** that `training` records *before* the final refit, and
`prediction` serves them at `GET /model/{slug}/card`: per-candidate CV and hold-out scores, the
hold-out ROC curve and confusion matrix, and global SHAP importance. The platform's generic
`/dataset/{slug}/evaluation` endpoint scores the deployed (refit) model on rows it was trained
on, so it is optimistic. The Tutorial therefore reads the model card instead. The Voyage tab
also scores passengers the model was refit on, which the tutorial says openly.

### Responsible AI
