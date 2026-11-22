# Architecture details - Scenarios and the UI's generic renderers

Source: docs/architecture-details.md (this repository)

The **architecture tutorial** (`framework_architecture`, a `conversational_rag` with
`ui_extras: architecture_map`) answers from a snapshot of this repository's docs that `scripts/generate_architecture.py`
cuts into pieces of at most 4,000 characters (each embedding call to llm-gateway stays small) together with a generated
document of which components each scenario uses; a test fails when the snapshot is stale. The **app's guide**
(`GuidePanel.tsx`, ADR 0032: the top bar's Guide, a side panel on every page) is that tutorial's own rag-agent chat with
`forwardedProps.guide` (extra instructions only) and four frontend tools that act on the screen (`search_scenarios`,
`open_scenario`, `start_guided_demo`, `open_architecture`); its context is the user's own gallery, so it opens nothing the
gallery does not show. App carries the actions out; a workspace's tab or demo is reached through `guideBridge.ts`, where
every workspace publishes its story tabs (`useStoryDemo`, and TabularView directly). `industry` is the
scenario's *domain* (industries, `insurance` included, plus `tutorial`/`society_ethics`), shown as the picker's Domain filter. `scenario.yaml` is otherwise read only by `platform-registry` and, as
build-time config, `etl-tabular`/`training`/`prediction`.
