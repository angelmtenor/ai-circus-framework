# Plan: scenarios a client understands end to end

The handoff for anyone (a person or a new agent session) picking this work up. It says what was asked, what is built
and where, the rules learned on the way, and every pending step with enough detail to start it cold.

Branch: `feature/scenario-story` (from `develop` after v0.63.0). Nothing is pushed or released; finishing the feature
and cutting a release are the maintainer's calls (`AGENTS.md` §5, the `git-flow-finish` skill).

## Contents

- [What was asked](#what-was-asked)
- [What is built](#what-is-built)
- [Rules and gotchas learned](#rules-and-gotchas-learned)
- [Pending work, in order](#pending-work-in-order)
- [How to verify a step](#how-to-verify-a-step)

## What was asked

Make the demo scenarios understandable to someone with no context — above all the water scenario, then the fraud and
insurance ones — and make the whole repository work as an architecture tutorial:

1. Explain each scenario's use case, context and data provenance, with diagrams.
2. A complete architecture diagram (infrastructure, data, consumption — AI and BI —, gateways), and for each scenario
   the subset of services it uses, without lists maintained by hand.
3. One way to choose parameters, the same for querying data and for asking a model.
4. A clean view for executives and a detailed one for engineers.
5. Later, optional: an architecture tutorial scenario (MCP and the full stack), MCP access to the new scenarios, a
   master assistant for the whole framework that can also act (open a scenario, switch a tab), and push-to-talk voice.

## What is built

Phases F0-F5 and part of F6, all deployed on the local k3s cluster and checked in a headless browser (idle check:
0 fps on every tab tried), `make check-all` green.

| Phase | What | Where |
|---|---|---|
| F0 | Data & BI: a forecast's target is a numeric range (it rendered thousands of chips), a date-range filter, a time line chart per series; forecast tab: change instead of a summed level, days instead of steps, key-dates table | `DataView.tsx`, `DatasetFilterPanel.tsx`, `chartBuilder.ts`, `ForecastViews.tsx` |
| F1 | `story:` in scenario.yaml (`StoryConfig`); the **Overview** tab (problem, today vs with this, value tiles, a flow linked to tabs, data provenance by origin, models, limits); the **guided demo** bar | `scenario_schema.py`, `ScenarioOverview.tsx`, `StoryDemoBar.tsx`, `TabularView.tsx` |
| F2 | The **drought control room** for `analysis_toolset: drought_watch`: system states, map coloured by storage band with a weekly replay since 2000, plan thresholds behind the chart, what-if and measure optimiser, drivers, data quality (published vs cleaned per flag kind), plan, bulletin | `DroughtControlRoom.tsx`; assistant `/drought/{slug}/...` in `api.py` over `core/drought_tools.py` |
| F3 | One **architecture catalog** → the README map, `docs/architecture/` (guide + components), the app's **Architecture** page, and each Overview's **How it's built** (derived from the YAML) | `libs/shared/.../architecture_catalog.yaml`, `architecture.py`, `scripts/generate_architecture.py`, `ArchitectureMap.tsx`, `ArchitectureView.tsx`, `ScenarioArchitecture.tsx` |
| F4 | Shared **controls** (range slider over a histogram, number slider, choice select) in filters, predictions, what-ifs and levers; the gallery's **ranked search** by words (bilingual concepts) and meaning (registry embeddings) | `controls.tsx`, `predictUtils.tsx`, `scenarioSearch.ts`, `ScenarioPicker.tsx`, registry `core/scenario_search.py` |
| F5 | **Executive · Analyst · Engineer** switch per tabular workspace (also the Surf tour's depth); the engineer's card (YAML, routes, commands) | `audience.ts`, `EngineerCard.tsx`, `DataTourView.tsx` |
| F6 (part) | Stories and demo scripts for 10 tabular scenarios: water, financial crime, insurance, AML money trail, Enron, supply chain, global health, prestaciones (Spanish), bank early warning, churn | `scenarios/*/scenario.yaml` |

## Rules and gotchas learned

- **`scenarios/` is a live hostPath mount.** Old images *ignore* an unknown YAML key (so adding `story:` is safe before
  rebuilding), but they *reject* a value the old schema does not accept (e.g. a chart naming a non-feature column).
  Keep scenario edits valid under the deployed schema, or rebuild every image that loads scenarios first.
- **llm-gateway shares its pod with the local embedding model** (about 1.2 of 1.5 GiB in use). One batch of 35
  scenario texts OOM-killed it twice. Embed in small batches of short texts (`EMBED_BATCH = 4`, 600 characters).
- **A registry rollout restarts the port-forward** (localhost:8010) about 30 s later; a browser check in that window
  finds no scenario cards. Wait for `curl localhost:8010/healthz`.
- **First requests at once can race** on lazily built state (the drought toolkit unpacked its SQLite twice → 503).
  Build under a lock and warm at start-up.
- A story's tabs are validated: `overview`, `data`, `assistant`, `tutorial`, `model`/`extra`, `model:<id>`/
  `extra:<id>`; `section: predictions|insights` only on a model tab. A story belongs to the scenario (model views
  have none).
- The idle check (`.claude/skills/playwright-headless-verify/k3s-idle-check.js`) runs in the Playwright Docker image
  on this machine (the native Chromium lacks system libraries): see the skill for the exact command.
- Public repository: never name a client, an employer or the institution a demo is for.

## Pending work, in order

Each item is independent unless it says otherwise. Update this file when one is done.

### 1. Finish F6 for tabular scenarios (content only) — done

All 24 tabular scenarios have a story (the last 14 were added after the first handoff, unreviewed in the browser:
open each Overview once and run its guided demo). What follows is kept for reference.

Add `story:` (with `demo:`) to the 14 tabular scenarios without one: `cnc_surface_finish`, `electric_motor`,
`energy_building`, `luznova_ev_charging`, `luznova_gas_anomaly`, `luznova_gas_prospects`, `luznova_regional_demand`,
`mpm`, `steel_defects`, `supermarket_sales`, `surf_ml`, `titanic`, `toxic_leadership`, `turbofan_rul`.
Take every figure from the scenario's YAML, its `docs/scenarios/` guide or its sample data; say `synthetic` /
`public` / `official` honestly; validate with `ScenarioDefinition.load`; restart platform-registry (no rebuild).
Tutorials (`titanic`, `toxic_leadership`) open on their Tutorial tab: their story's demo can point at `tutorial`.

### 2. An Overview for the other kinds — done

Every scenario of every kind has a story; RAG, form, deep-learning and data-product workspaces open on the Overview
(`storyWorkspace.tsx`). Checked in the browser for RAG (with and without a knowledge graph), forms and the data
product; the deep-learning Overview is checked by type and build only (dl-inference was off: start it with
`make k3s-dl-up` and open a DL scenario once). What follows is kept for reference.

`story` is tabular-only today (`_story_references_real_tabs_and_models` rejects other kinds). To extend it:

- Schema: allow `story` on `conversational_rag`, `assisted_form`, `deep_learning`, `data_product`, with each kind's
  tab names (read them from `RagView.tsx`, `AssistedFormView.tsx`, `DeepLearningView.tsx`, `DataProductView.tsx`).
- UI: render `ScenarioOverview` as the first tab of each of those views (it only needs `tabLabel`, `onOpen`, `onAsk`)
  and the `StoryDemoBar`; reuse the audience switch.
- Content: stories for the 2 forms, 2 RAG, 6 deep-learning scenarios and `surf_retail` (whose guided tour stays; the
  story frames the business).

### 3. F7 — an architecture tutorial scenario (Domain: Tutorial) — first version done

Verified on k3s: 759 passages indexed, retrieval returns the right guides and ADRs, every tab idle. The chat itself
could not be checked end to end because the active model (Claude Sonnet) answered "credit balance too low": switch the
active model in Settings or add credit, then ask the Overview's questions once.

Branch `feature/architecture-tutorial` (from `feature/scenario-story` after v0.64.0's notes): `framework_architecture`,
a `conversational_rag` over a snapshot of this repository's docs generated by `scripts/generate_architecture.py`
(`make architecture`), including a document of which components each scenario uses; `ui_extras: architecture_map`
shows the platform map and its request flows next to the chat; an MCP door (`mcp.expose`). Not done yet from the plan
below: the step-by-step tour with live "Try it" calls and evidence (trace id, audit line), and an `mcp/catalog.yaml`
`scenario_door` entry so the chat apps reach it through the gateway (`make mcp-catalog`, then apply). What follows
is the original plan, kept for the remaining part.

A `conversational_rag` scenario over **this** repository's documentation (README, `docs/architecture/`, ADRs,
scenario guides, `docs/mcp/`); `ai_circus_reference` indexes another repository, so it cannot answer "which services
does the water scenario use?". Plan:

- `scenarios/framework_architecture/scenario.yaml`: `documents` from a vendored snapshot generated by a script (so it
  never drifts), `vector_store`, `mcp.expose` (the tutorial itself answers over MCP), `ui_extras` a new generic
  `architecture_tour` kind.
- `architecture_tour` tab: steps defined in the YAML (or the catalog's `flows`), each lighting its path on
  `ArchitectureMap`, with three depths (executive / analyst / engineer) and a "Try it" that makes the real call and
  shows the evidence (response, trace id, audit line). Steps: sign-in (Keycloak, tenant), entitlement, a prediction,
  the assistant (gateway, budget, Langfuse), RAG (Qdrant), a data-product query (data-api, roles, masks), the same
  question from LibreChat through the MCP gateway (token exchange, pseudonym, level 3), one trace across the hop,
  governance (AI-BOM, audit trail), "a scenario is a YAML". Each step names the `make` target it needs and greys out
  when its backend is down (`serviceAvailability.ts`).

### 4. F8 — MCP doors on the assistant (tabular scenarios) — done

[ADR 0031](../adr/0031-mcp-doors-on-the-assistant.md). The assistant serves `/mcp/{slug}` for every `tabular_ml`
scenario with `mcp.expose` (`services/assistant/src/assistant/core/mcp_door.py`): `describe_model`, `score_record`
(classifier or regressor only; checks each value against `feature_schema`, rolls SHAP drivers back to the inputs,
returns the business-rules gate) and, with `drought_watch`, seven drought tools over the same `DroughtToolkit`. Doors
on `water_reservoirs_drought` (no `score_record`: its model is a forecast), `financial_crime_desk` and
`insurance_portfolio`, each with a "The same model from any chat" demo step; `scenario_door` entries in
`mcp/catalog.yaml` (also `framework_architecture`), a "Connect via MCP" tab in TabularView, `localhost:8034` in `make
k3s-mcp-portforward`, the llm-gateway → assistant edge in the architecture catalog. `prediction` now answers 422
(not 500) for a value its pipeline cannot read. Verified on k3s: tools/list per door (404 without a door, 401 without
a token), every tool called with the admin key. Not tried: a question from LibreChat end to end (the active model had
no credit).

### 5. F9 — a master assistant for the whole framework

A chat available from the gallery (not tied to one scenario) that answers from the architecture tutorial's RAG (step
3) and acts through frontend actions (`useCopilotAction`): search scenarios (the ranked search), open one, switch tab,
start the guided demo, open the Architecture page. Bounded by the user's entitlements (it can only open what the
gallery shows). Decide whether it is a `conversational_rag` scenario with `consume` doors or a small agent in
rag-agent; keep every model call through llm-gateway.

### 6. F10 — voice, push to talk (separate session)

Voice mode exists (`agui-voice`, off in the lite profile). Change the interaction to the one of Claude's app: press to
start talking, press again (or hold/release the space bar) to stop — **no automatic end-of-speech detection**. Look
at `useVoiceSession.ts`, `MicButton.tsx` and agui-voice's VAD settings; keep the models warm at start-up.

### Smaller follow-ups

- `SCENARIOS` in a Job's env does not narrow etl-vectorize: its `settings.yaml` profile sets `SCENARIOS: ""`, and the
  profile beats the environment (`EnvConfig(**profile)`), so a vectorize run always indexes every scenario (~20 min).
  Drop that profile default (as for the governance flags) to index one scenario at a time.

- The drought assistant tools and the control room read the committed artifacts, not the forecast trained for the
  caller's tenant (water guide, "What is left" 1).
- The guided demo's "Ask" buttons were not tried against a live LLM.
- `storage_forecast` (and the chat's forecast tool) can return a p90 above 100 % of capacity straight from the
  committed forecast file; the bulletin caps it at 100 %. Clip p10/p50/p90 to [0, 100] in `DroughtToolkit.forecast_of`
  (and check the control room's chart) so every view agrees.
- `docs/scenarios/water_reservoirs_drought_guide.md` and the README's scenario tour could show control-room
  screenshots (capture with the Playwright scripts, as in `reference-demo-decks`).
- Release: after review, `git flow feature finish scenario-story`, then a minor release with a CHANGELOG entry.

## How to verify a step

```bash
make check-all                          # libs/shared, mcp-kit and every service
cd ui-react && npm run build && npm run lint
make architecture-check                 # after touching the catalog or k8s/
make k3s-ui-image && k3d image import ai-circus/ui-react:local -c ai-circus && kubectl -n ai-circus rollout restart deployment/ui-react
kubectl -n ai-circus rollout restart deployment/platform-registry   # after editing a scenario.yaml
```

Then a real browser check and the idle check (the `playwright-headless-verify` skill), with screenshots.
