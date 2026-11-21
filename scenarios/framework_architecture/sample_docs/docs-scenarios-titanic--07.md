# 🚢 Titanic Survival — an explained ML tutorial - How it is built

Source: docs/scenarios/titanic.md (this repository)

Everything specific to Titanic is **data** in its `scenario.yaml`. The two new tabs are generic
renderers that any future scenario can reuse:

| Piece | Where | Reusable as |
|---|---|---|
| `tutorial:` block → Tutorial tab | [`TutorialView.tsx`](../../ui-react/src/TutorialView.tsx) | Any `tabular_ml` scenario: markdown chapters, `default_charts`-style charts, and widgets (`dataset_preview`, `class_balance`, `model_card`, `roc_curve`, `confusion_matrix`, `feature_importance`, `examples`) |
| `ui_extras: {kind: voyage_explorer}` → Voyage tab | [`VoyageView.tsx`](../../ui-react/src/VoyageView.tsx), [`voyageScene.tsx`](../../ui-react/src/voyageScene.tsx) | Any binary classifier whose rows are people aboard a vessel: `zone_by`, `zones`, `filters`, `range_filters`, `name_column`, `personas` |
| `model.selection_metric` + `model.cv_folds` | `training` | k-fold CV selection on ROC AUC / accuracy / R² for any scenario (default: one hold-out, as before) |
| Model card | `training` metadata → `prediction` `GET /model/{slug}/card` | Every tabular scenario retrained since this change |
| `dataset.display_columns` | `etl-tabular`, `prediction`, `ui-react` | Any scenario: descriptive columns shown next to the row id, never trained on |
| `industry: tutorial` | Scenario picker **Domain** filter | New domains: `tutorial`, `society_ethics` |

```yaml
industry: tutorial
dataset:
  index_col: "PassengerId"
  display_columns: ["Name"]
model:
  candidates: ["logistic_regression", "lightgbm_small_data"]
  accuracy_gain_threshold_for_complexity: 0.01
  selection_metric: roc_auc
  cv_folds: 5
ui_extras:
  kind: voyage_explorer
  zone_by: Pclass
  zones: [{key: "1st", label: "First class"}, {key: "2nd", ...}, {key: "3rd", ...}]
  filters: [Sex, Title, Embarked]
  range_filters: [Age, FarePerPerson]
  name_column: Name
tutorial:
  steps:
    - title: "7 · Evaluate honestly"
      body: |
        The selected model, trained on the 80% only, scored on the untouched hold-out…
      widgets: [roc_curve, confusion_matrix]
```

**Every dataset view now leads with the record's identity.** The scenario's `index_col`
(`PassengerId`, `CustomerId`, …) plus any `display_columns` come first in the Data & BI table and
in batch predictions, and they are searchable. `etl-tabular` also de-duplicates on *id + values*
rather than on values alone, which previously dropped 106 distinct Titanic passengers who
happened to share every feature.

<p align="center"><img src="../screenshots/titanic/data-ids.png" alt="Data & BI sample rows led by PassengerId and Name, filtered by a name search" width="850"></p>

**Footprint.** Titanic adds no pods or services. The model and its SHAP explainer are about
350 KB, preloaded by `prediction` at start-up like every other model. Both tabs are lazy-loaded
chunks (Voyage ~13 KB gzipped — it also carries `toxic_leadership`'s office scene — and Tutorial
~6 KB, plus their CSS), so no other scenario downloads them. The Voyage tab scores all 891
passengers in one batched call without SHAP (about 2.5 s), explains a passenger when clicked, and
caches both for the session, so re-opening it is instant. The survival
colours (#3f93eb / #ea4f58, grey midpoint) were validated for colour-vision deficiency and
contrast against the scene's night sky.

---
