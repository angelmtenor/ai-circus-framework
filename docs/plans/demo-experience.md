# Plan: scenarios a client understands end to end

The handoff for anyone (a person or a new agent session) picking this work up. It says what was asked, what is built
and where, the rules learned on the way, and every pending step with enough detail to start it cold.

Each step was built on its own `feature/*` branch from `develop` and is released: F6 in v0.64.0, F7 and F8 in
v0.65.0, F9, F10 and the spoken replies in v0.66.0, the spoken screen actions in v0.67.0. Finishing a feature,
cutting a release and pushing are the maintainer's calls (`AGENTS.md` §5, the `git-flow-finish` skill).

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

### 5. F9 — a master assistant for the whole framework — done

Branch `feature/master-assistant` (from `develop` after v0.65.0). [ADR 0032](../adr/0032-the-apps-guide.md): neither a
new scenario nor a new agent — the top bar's **Guide** (`GuidePanel.tsx`) is the architecture tutorial's own rag-agent
chat (same documents, citations, entitlement and history, titled "🧭 Framework guide") with `forwardedProps.guide`
(extra instructions only, `rag_agent.core.agent.system_prompt`) and four frontend tools: `search_scenarios` (ranks the
gallery), `open_scenario` (a scenario, optionally on a tab; also switches tab), `start_guided_demo`,
`open_architecture` (on a component or a request flow). Its context is the user's own gallery, where they are and the
open workspace's tabs, published by every workspace through `guideBridge.ts` (`useStoryDemo`; TabularView directly).
App checks every slug against the entitled list; a workspace opens only a tab it published. A side panel, lazy, kept
mounted while hidden; on a wide screen the page makes room for it (the top bar wraps) and, from 1,400 px, a scenario's
own chat dock opens beside it. A scenario whose service is off or whose model is untrained for the tenant is marked
unavailable in the context and refused with the reason. Verified on k3s with gemini-flash: a documents answer with
sources, search, open + guided demo, tab switch, Architecture flow, the same request again from another page, the
scenario dock beside the guide, close/reopen keeps the conversation, idle 0 frames/s with the guide open, no overflow
at phone width; `make k3s-idle-check` on Reservoirs and Insurance stays quiet.

Done since: the guide asks a scenario's own assistant. A fifth app action, `ask_scenario_assistant(slug, question)`,
goes through `guideBridge.ts` to the workspace's own `ask` (the one its Ask buttons use): the scenario opens, its chat
opens and the question is sent there, so the answer comes from that scenario's agent with its tools, data and its own
entitlement check (every kind but deep learning; never the guide's own tutorial). rag-agent's guide instructions send a
question about a scenario's data, model or results there instead of answering from the documents. On the way, an
`assisted_form` without a story or case tracker dropped such a question (the bare form got no `prompt`). And the guide
now answers about ADR 0032 and 0033 (the index had missed them: see the etl-vectorize follow-up below). Verified on k3s
with gemini-flash: "How does voice fill a form?" is answered from three pieces of ADR 0033; "Ask the reservoirs scenario:
at the current trend, when does the Central system enter alert?" opens Reservoirs with its chat beside the guide, and
that chat answers from the tenant's trained model (provenance: Model).

### 6. F10 — voice, push to talk — done

[ADR 0033](../adr/0033-push-to-talk-voice.md). Press the mic to start talking, press again (or hold and release the
space bar) to send; no end-of-speech detection. agui-voice's pipeline swaps the Silero VAD for `PushToTalkProcessor`
(`services/agui-voice/src/agui_voice/core/push_to_talk.py`), which turns the client's `push-to-talk` RTVI message into
the frames that bound Whisper's segment, interrupts the bot on a press and caps a turn at 60 s; the `silero` extra is
gone and start-up still warms STT and TTS. `useVoiceSession.ts` sends audio only while talking (plus a 320 ms tail),
drops the queued reply on a barge-in, gives the button back after 30 s if the agent never starts (2 minutes once it
has) and hangs up after 3 minutes unused; `MicButton.tsx`
shows talking (red) / thinking / speaking, takes the space bar once voice is on (never inside a field), and has a ✕ to
end voice mode. Fixed on the way: the voice bridge ran the agent on `thread_id=<slug>`, which every agent has refused
with 404 since conversation history arrived (no spoken turn got an answer); the browser now passes the open
conversation (`?thread_id=`), so spoken turns are answered and stored with the typed ones. Verified on k3s with a fake
microphone (two questions with a 2 s pause, looped) in the insurance chat: one press gives one turn with both
questions and one spoken answer; nothing is sent between turns; a connected idle session draws 0 frames/s; holding
the space bar talks; a press while it answers is back to talking in 0.2 s; the ✕ hangs up; after a reload the spoken
questions are in the conversation's history; every insurance tab passes the idle check.

### Smaller follow-ups

- Done: a spoken reply was as long as a typed one (the insurance chat's first voice answer took about two minutes to
  say). agui-voice now sends `forwardedProps: {"spoken": true}` and every agent ends its prompt with
  `ai_circus_shared.spoken`'s instructions. Measured on k3s with a fake microphone: insurance 61 words said in 20 s,
  the architecture tutorial 64 words in 22 s, the service request form 51 words in 13 s. On the way, the browser's
  30 s wait now applies only until the agent starts; one insurance answer took 60 s (two Gemini calls and a tool) and
  would otherwise have been dropped.
- Done: a spoken turn had none of the browser's tools (`tools=[]`, `context=[]`), so by voice the form was never
  filled, no chart was drawn and the guide could not act, while the form agent said it had. Each press now sends a
  `voice-turn` message with the chat's tools, readable context, forwarded props and transcript; agui-voice hands a
  screen action back (`frontend-tool-call`), the chat carries it out as after a typed turn, and the agent runs once more
  to say what it did ([ADR 0033](../adr/0033-push-to-talk-voice.md)). Found on the way: with Gemini, LangGraph's AG-UI
  adapter never streams a call's arguments (start and end only), so they come from the run's closing
  `MESSAGES_SNAPSHOT`. Measured on k3s with a fake microphone: the form gets 3 fields filled and says so (36 words);
  insurance draws the SHAP chart and explains it (58 words); the guide opens the insurance scenario (14 words); two
  agent runs each, the largest `voice-turn` message 26.5 KB.

- Done: `SCENARIOS` in a Job's env did not narrow etl-vectorize (its `settings.yaml` profile default beat the
  environment). The default is gone (`mandatory: false`, as for etl-tabular), and `make k3s-vectorize SCENARIOS=a,b`
  (`scripts/k3s_vectorize.sh`, same `^[a-z0-9_,]+$` check as `k3s_ml_train.sh`) sets it on the Job with
  `kubectl set env --local`; the manifest itself stays equal to data-platform-manager's Job (its test). On k3s the
  tutorial alone indexes in about 15 minutes (240 documents, 792 chunks).
- Done, found on the way: once a tenant's bucket existed, etl-vectorize never refreshed its documents, so ADR 0031-0033
  and the snapshot's newer pieces never reached the guide (232 files in the bucket against 240 in the seed, 24 of them
  stale). A seed manifest (`seed/<raw_prefix>.json`, SHA-256 per file) now updates the seed documents that changed, adds
  new ones and removes deleted ones, and leaves alone what the tenant edited or deleted; a bucket from before the
  manifest is adopted when all its files are seed names. GitHub-sourced documents are not touched. On k3s: "32 file(s)
  updated for org=admin", then 6 after the snapshot fix below.
- Done: `split_markdown` dropped the tail of a paragraph longer than 4,000 characters; it lost text in ADR 0016, 0027
  and 0033 (pieces 02/03). It now cuts such a paragraph at sentences, or between lines when it is a table, a list or
  code, then at words; a test checks that every word of every tutorial source is in the snapshot.

- Done: the drought tools and the control room read the committed artifacts, not the forecast trained for the
  caller's tenant (they differed by up to 3.75 pp). Training's quantile forecast now writes each series' horizons
  (`quantiles`: origin, current storage, p10/p50/p90 and drivers per horizon) into the served `forecast.json`, and
  prediction's `/forecast` passes them through. The assistant asks it with the caller's own token (chat, `/drought/…`
  routes and the MCP door alike; cached per organisation and scenario for 60 s, at most 64 entries) and
  `DroughtToolkit.with_forecast` returns a view over that forecast with the outlook recomputed by the same
  `drought_outlook` code (a test shows the recompute reproduces the committed outlook exactly). It falls back to the
  committed reference when the tenant has no model, prediction is down, or the forecast was trained from other readings
  (another last day or current storage than the tools' data). `forecast_source` tells which, and the room shows it
  under the chart. Verified on k3s after a retrain: the room says "Your organisation's trained model
  (lightgbm_quantile)", and the what-if's median at 30/90/180 days equals the served one for every system (Central at
  90 days: 93.6 against 95.4 in the committed file).
- Done: the guided demo's "Ask" buttons, tried against the live model (gemini-3.1-flash-lite) on Reservoirs ("when does
  the Central system enter alert? And with a dry quarter?") and Insurance ("does a big price increase at renewal drive
  customers away?"): both send the step's question to the scenario's chat and get an answer from its tools (Central
  stays above alert at 90 days, 1 % chance either way; the premium change is the model's strongest driver). Found on
  the way: a drought answer said "No tools: scenario context only", because `chatProvenance.ts` knew none of the drought
  tools; they now show as Model, Rules (the plan's articles) and Data health.
- Done: quantiles are clipped to [0, 100] % in `DroughtToolkit.forecast_of` (served p90 reached 106 % for Westfield
  and the committed p50 101 % for Lantern Lake), and the control room's chart caps its lines and bands the same way.
  Checked in the browser for all 13 series: no point above the 100 % line.
- Done: control-room screenshots (`docs/screenshots/water_reservoirs_drought/`: the room and the outlook with
  measures, captured with the Playwright image and stored as webp like the others) in the water guide and in the
  README's showcase (Industry & energy).
- Done: README, voice mode: "with the details shown (or offered) on screen".
- Done, reported by the user: the mic only said "Voice connection failed — press to retry" while agui-voice was scaled
  to 0 (the lite profiles keep it there), so nothing told voice was off from a fault. The mic now asks the gallery's
  shared `/healthz` poll (`serviceAvailability.ts`, which gains `agui-voice`): while the service is down it reads "Voice
  is off" with how to start it (Platform → Health → Start, or `kubectl scale`), and a press only checks again instead of
  asking for the microphone; a failed connection re-checks at once. A browser that refuses the microphone gets its own
  message (`no-mic`). The providers label is no longer fetched from a stopped service (it logged CORS errors). Checked
  in the browser on k3s: off (no microphone request), back on (one press re-checks, the next connects and talks),
  microphone refused, and a full spoken turn in the Reservoirs chat (answer after 4.9 s); every Reservoirs tab still
  idles at 0 frames/s.
- Done, reported by the user: with a real microphone the turn stayed at "…" and nothing was transcribed (a fake
  microphone worked). Deployed on k3s (`make check-all` green): each turn logs its audio and its
  transcription in numbers, local Whisper runs int8 with a no-speech threshold of 0.6, and STT `groq` (hosted
  `whisper-large-v3-turbo`, local fallback) can be picked in Settings. On k3s with a fake microphone Groq transcribed
  Spanish and English (0.4-0.5 s a turn, language detected) and the guide's input is 163 px wide with voice on. The real
  turns logged before that were silence (five at -inf dBFS, one at -72), so the fault is in the capture (the browser's or
  the system's microphone), not in the transcription; a silent turn is now said on screen, and is not sent to Groq, which
  answered "Thank you.". The notice named the cause: Chrome used a VR headset's virtual input, not Windows' default
  microphone, so Settings and the notice now choose the microphone. With the laptop's microphone chosen, the
  user's turns were transcribed in English and Spanish (peak -14 dBFS, 0.4 s). Then, also reported by the user: a press
  to stop a slow answer (up to 36 s) opened another turn, and its near-silence came out as "Thank you." and Russian. Now
  two buttons, as in Claude's app: the mic dictates into the input (Enter sends) and the voice button converses, where
  a press only stops; recordings without speech are not transcribed. Driven in Chromium with a fake microphone and a
  stand-in server; the user confirmed dictation with their own. Not verified yet: the conversation button with a real
  microphone, the Spanish voice by ear, and the idle check.

## How to verify a step

```bash
make check-all                          # libs/shared, mcp-kit and every service
cd ui-react && npm run build && npm run lint
make architecture-check                 # after touching the catalog or k8s/
make k3s-ui-image && k3d image import ai-circus/ui-react:local -c ai-circus && kubectl -n ai-circus rollout restart deployment/ui-react
kubectl -n ai-circus rollout restart deployment/platform-registry   # after editing a scenario.yaml
```

Then a real browser check and the idle check (the `playwright-headless-verify` skill), with screenshots.
