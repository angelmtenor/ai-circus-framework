# Architecture details - Scenarios and the UI's generic renderers

Source: docs/architecture-details.md (this repository)

**Scenario-driven, not per-feature code.** A scenario (`scenarios/<slug>/scenario.yaml`,
schema in `ai_circus_shared/scenario_schema.py`) is the unit of product content, of one `kind`:
`tabular_ml`, `conversational_rag`, `assisted_form`, `deep_learning`. Adding one is a YAML file
plus a `platform-registry` restart (it seeds scenarios) — never new UI or service code. **One
service instance serves every scenario of its kind**, routed by a `{scenario_slug}` path segment:
`prediction`/`assistant` (tabular_ml), `rag-agent` (conversational_rag), `form-agent`
(assisted_form), optional `dl-inference` (deep_learning; onnxruntime only, models from the
one-shot `dl-training`, artifact contract in `ai_circus_shared/deep_learning.py`). A
`deep_learning` scenario is `task: classification` (fine-tune) or `task: anomaly_detection`
(frozen DINOv2 + patch memory bank; same ONNX interface plus an `anomaly_map` output). A classification scenario may read **frame sequences** (`source.type: http_frame_sequences`: per-camera `.tgz` of
`<ts>_<±offset s>.jpg`, label from the offset, split by camera/station so test cameras are never seen; `clear_only`
archives are no-event controls): dl-training publishes each sample's `group` (camera) and `seq` in recording order,
and `ui_extras: camera_wall` (`wildfire_smoke_watch`, `CameraWallView.tsx` + `cameraWallLogic.ts`) plays a pool of them on
one clock, really scoring every frame through dl-inference (`live` vs `replay`), with an in-app camera picker, an alert
rule (threshold, N frames, `early_grace_seconds`) and `control_margin_seconds` for controls. `ui-react`
mirrors this: `ScenarioPicker` renders whatever the entitlements API returns; `TabularView`/
`RagView`/`AssistedFormView`/`DeepLearningView` are generic renderers driven by each scenario's
`ScenarioSummary`; an optional 5th tab comes from `ui_extras` (`region_map`, `live_plant`,
`process_optimizer`, `risk_watchlist`, `network_explorer`, `dispatch_tower`, `shipment_globe`, `money_trail`, `camera_wall`, …). A `tutorial:` block (tabular_ml) adds a guided Tutorial tab. A `story:` block (any kind,
`StoryConfig`) is the scenario told for someone with no context, rendered by the first tab, **Overview**
(`ScenarioOverview.tsx`): the problem and who decides, today against with this, a flow whose boxes open the tabs that show
each step, where every input comes from (`origin: synthetic | public | official`, never hidden), the question each model
answers, and the declared limits; `story.demo` is a presenter's script that `StoryDemoBar.tsx` walks tab by tab (what to
point at, what to say, a question for the assistant). Tab names depend on the kind (`ScenarioDefinition.story_tabs`:
tabular `data`, `model`/`extra`, `model:<id>`/`extra:<id>`, `tutorial`; RAG `library`; forms `cases`; deep learning
`data`/`predict`/`insights`/`extra`; data products `tour`, `data`, `flow`, `dashboards`, …; `assistant` and `mcp` where the
workspace has them), and the schema rejects a tab the scenario does not have. The other kinds' workspaces wire the demo
through `storyWorkspace.tsx`'s `useStoryDemo`; a deep-learning workspace has no chat, so its story never asks. A story belongs to the
scenario, so model views carry none. Every Overview also has **How it's built** (`ScenarioArchitecture.tsx`): the platform
map with the components the scenario uses lit and why, derived from its YAML by `ai_circus_shared.architecture` at seed
time (served as `ScenarioSummary.architecture`); the catalog behind it is `architecture_catalog.yaml` (see
`docs/architecture/`). The **architecture tutorial** (`framework_architecture`, a `conversational_rag` with
`ui_extras: architecture_map`) answers from a snapshot of this repository's docs that `scripts/generate_architecture.py`
cuts into pieces of at most 4,000 characters (each embedding call to llm-gateway stays small) together with a ge
