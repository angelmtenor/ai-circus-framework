# 0018 — One scenario can hold several models

Supersedes ADR 0016's "a scenario is one or the other": now a *model* is one or the other.

**Context.** A dataset can answer more than one question. `energy_building` lost its row-scoring
regression when it became a forecast (v0.40.0); `luznova_regional_demand` and
`luznova_regional_forecast` are two picker entries over one question; the surf chain's demand and
buyer models are two more. Each extra model meant another scenario, or replacing the one it had.

**Decision.**
- **`extra_models:`** on a `tabular_ml` scenario: a list next to `model:`, each with an `id`, a
  `title`, and its own `model`, `dataset`, optional `time_series` and optional `ui_extras`. `model:`
  stays the main model, so a scenario without the list is unchanged.
- **Each model is a complete, validated view of its scenario** (`ScenarioDefinition.model_views()`):
  the scenario with that model's blocks in place of the main ones, validated by the same rules. So
  etl-tabular, training and prediction keep treating "a definition" as one model, and every rule
  of a task type (a forecast has no business rules, a board needs a forecast) holds per model.
- **A model's address is `slug~id`** (`/predict/energy_building~forecast`); the bare slug is the main
  model, so every existing URL still means what it did. `~` is unreserved in a URL and rejected in a
  slug or id. Entitlement is checked on the scenario (`base_scenario_slug`): one entitlement covers
  all its models, and tenancy is unchanged.
- **Each model has its own `dataset.bucket`** (the schema enforces it). Its parquet and artifacts
  keep the same keys inside it, tenant-prefixed like every other object.
- **Evidence names the scenario.** Decision records and `model.trained` carry the base slug and the
  AI-system id of the scenario; a metric label, MLflow experiment and log line carry the address.
- **The assistant gets every model's tools**, the main model's under their usual names and an extra
  model's with `_<id>` appended, and its prompt describes each model.
- **UI:** one tab per model, with a Predictions / Insights switch inside it (every scenario, so
  there is one layout), then that model's `ui_extras` tab. The Data tab has a dataset selector when
  the scenario has several models. A view gets a `ScenarioSummary` whose `slug` is the address.
- **Not for extra models:** `case_desk` (it calls the assistant by slug), tutorials, rubric checks
  and document tools; these belong to the scenario, so they stay on the main model.

**Consequences.** More models per pod: prediction preloads each one, so a large merged scenario
costs what its separate scenarios cost, and no more. A model's `ml_exports` reference names its
address. A retired slug's stored objects stay in SeaweedFS until a cluster reset.
