# Architecture details - Free-text features, challenger and rubric

Source: docs/architecture-details.md (this repository)

A tabular feature can be `type: text` (free text): TF-IDF step `text_<col>` inside the pipeline,
SHAP terms rolled up server-side (`tabular_ml.original_feature`), per-word spans via `/predict`
`explain_text`; `explain: false` is the fast bulk path (TreeExplainer SHAP ≈5 ms/row). Such a
scenario may add a `model.text_challenger` (sentence embeddings from llm-gateway's `local-embed`,
cached from the host GPU — the cluster's CPU embedder is ~0.6 s/text; served as `/predict`
`model: "challenger"`, never auto-promoted) and a `rubric_check` (assistant `POST /rubric-check`: the
active LLM reads a *description of behaviour* against a YAML rubric — never judges named people).
