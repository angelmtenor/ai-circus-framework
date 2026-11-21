# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - How it is built

Source: docs/scenarios/toxic_leadership.md (this repository)

Everything is generic platform code driven by the scenario YAML; nothing is specific to this
scenario.

- **`type: text` features** (`libs/shared` schema): etl-tabular keeps them as strings; training
  adds a `text_<column>` TF-IDF step to the pipeline and records `text_term_importance` (each
  term's mean SHAP where present) in the model card; prediction rolls the term columns up
  server-side and, with `explain_text` (≤ 50 records), returns each word's span and weight.
  `explain: false` scores a whole dataset without SHAP (TreeExplainer costs ~5 ms per row).
- **The challenger** (`model.text_challenger`): embeddings are computed *outside* the sklearn
  pipeline (each text column becomes `<column>__emb_<i>` numeric columns), so nothing custom is
  pickled and no torch is needed in training or prediction. `make k3s-text-embeddings` embeds the
  dataset on the host GPU with the exact model llm-gateway serves (dl-training's
  `dl-training-embed-texts`) into a SeaweedFS cache; training checks a few cached vectors against
  the gateway (cosine ≥ 0.99) before using it. Prediction serves it as `/predict`
  `model: "challenger"`, embedding live through `local-embed`.
- **The rubric check** (`rubric_check`): assistant's `POST /rubric-check/{slug}` builds the prompt
  from the YAML rubric, calls the active model at temperature 0 (tenant `user` + Langfuse
  metadata like every chat), and validates the JSON answer against the rubric — unknown keys
  dropped, polarity taken from the rubric, quotes checked against the text.
- **The Office**: VoyageView became a scene-agnostic engine (`scenes.ts`); `officeScene.tsx` draws
  the tower and sizes the floors from the data, next to the liner (`voyageScene.tsx`).
  `positive_is_adverse` colours the positive class red when it is the bad outcome.

```yaml
dataset:
  index_col: ReviewId
  display_columns: [JobTitle]
  target: BadLeadership
  feature_schema:
    Review: {type: text, label: "Review (headline + cons)", max_length: 1000}
    CompBenefits: {type: numeric, label: "Pay & benefits rating", min: 1, max: 5, default: 3}
    # … JobFamily, EmployerType, EmployeeStatus, Tenure, ReviewYear, WorkLifeBalance
model:
  candidates: ["lightgbm_small_data", "lightgbm"]
  selection_metric: roc_auc
  cv_folds: 5
  text_challenger:
    embedding_model: local-embed
    hf_model_id: "voyageai/voyage-4-nano"
    label: "voyage-4-nano sentence embeddings"
ui_extras: {kind: voyage_explorer, scene: office_tower, tab_label: "The Office", zone_by: JobFamily, positive_is_adverse: true, …}
rubric_check: {title: "Leadership check", text_feature: Review, positive: […], negative: […], guidance: …}
tutorial: {positive_is_adverse: true, steps: […13 chapters…], examples: […]}
```

---
