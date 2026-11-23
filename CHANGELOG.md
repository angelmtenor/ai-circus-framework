# Changelog

Notable changes per release, newest first. Versions follow SemVer and every release is a git-flow
tag (`git tag`); commits follow [Conventional Commits](styleguide.md). Releases before v0.31.0 are
only recorded as tags and in `git log`.

## Unreleased

### Added
- **Dictation in every chat** ([ADR 0033](docs/adr/0033-push-to-talk-voice.md)): the microphone listens, and a second press (or
  Enter) writes what was said in the input, to edit and send with Enter (agui-voice `POST /stt/{scenario_slug}`). The
  conversation by voice has its own button beside it.
- **The guide can ask a scenario's own assistant** ([ADR 0032](docs/adr/0032-the-apps-guide.md)): a fifth app action,
  `ask_scenario_assistant(slug, question)`, opens the scenario and sends the question to its chat, as the scenario's
  Ask buttons do (every kind but deep learning). The answer comes from that scenario's agent, with its own tools, data
  and entitlement check; the guide's instructions send a question about a scenario's data, model or results there
  instead of answering it from the documents.
- **The drought tools, control room and bulletin use the tenant's trained forecast**
  ([water guide](docs/scenarios/water_reservoirs_drought_guide.md)): training's quantile forecast now carries its
  horizons (p10/p50/p90 and drivers) in the served `forecast.json`, and the assistant asks prediction's org-scoped
  `/forecast` with the caller's own token (chat, `/drought/…` routes and the MCP door alike), recomputes the outlook
  from it and falls back to the committed reference when the tenant has no model or it was trained from other
  readings. The control room says which forecast it uses.
- **Groq's hosted Whisper as a voice STT** ([ADR 0033](docs/adr/0033-push-to-talk-voice.md)), off by default: with
  `GROQ_API_KEY` and a pick in Settings → Voice, `whisper-large-v3-turbo` transcribes each turn with its language
  detected, and local Whisper takes any turn it cannot (no answer within 6 s included). The audio leaves the cluster.
- agui-voice logs each voice turn in numbers (audio length, level, language, no-speech probability), never its words.
- A spoken turn with no sound in it (a muted or wrong microphone) is said at once, naming the microphone in use,
  instead of waiting 30 s at "…". Another one can be chosen there or in Settings → Microphone, which also tests it.
- `make k3s-vectorize SCENARIOS=a,b` indexes only those scenarios (the tutorial alone takes about 15 minutes instead of
  every scenario's 20+).

### Fixed
- A `SCENARIOS` env var never narrowed etl-vectorize: its `settings.yaml` profile default beat the environment, so
  every run indexed every scenario. The default is gone (as for the governance flags).
- etl-vectorize never refreshed a tenant's documents once its bucket existed, so a changed or new seed document (the
  tutorial snapshot's newest ADRs) never reached the index. A seed manifest (SHA-256 per file) now updates the seed
  documents that changed and adds new ones, and leaves the ones a tenant edited or deleted alone.
- The tutorial snapshot dropped the tail of any paragraph longer than 4,000 characters (parts of ADR 0016, 0027 and
  0033). `split_markdown` now cuts it at sentences, or between the lines of a table, list or code block; a test checks
  no word of any source is lost.
- A drought forecast could exceed 100 % of capacity (p50 101 % and p90 105 % at 180 days for one reservoir) in the chat's
  tools and the control room's chart; quantiles are now clipped to 0-100 % everywhere, as the bulletin already did.
- An `assisted_form` scenario without a story or case tracker dropped a question sent from outside its chat.
- A drought answer was labelled "No tools: scenario context only" although it came from the drought tools; their
  answers now show as Model, Rules (the plan's articles) or Data health.
- The chat's mic said only "Voice connection failed" when agui-voice was not running (the lite profiles keep it at 0).
  It now reads "Voice is off" with how to start it, and asks for no microphone until the service is back; a browser
  that refuses the microphone gets its own message.
- Local Whisper dropped quiet speech (pipecat's no-speech threshold of 0.4; now 0.6) and ran float32 on a CPU (now int8).
- With voice on, the guide's text input shrank to 49 px behind the providers label; the engines are now in the voice
  button's tooltip, and the input keeps at least 7rem.
- Pressing the voice button to stop a slow answer opened another turn and cancelled the answer; it now only stops it, and
  the waiting icon no longer spins.
- A click or near-silence was transcribed as invented words ("Thank you.", Russian) and sent to the agent; a recording
  with no speech in it (Silero, bundled with faster-whisper) is no longer transcribed.
- With local Whisper a Spanish question was answered in the English voice: its detected language was not passed on.

### Docs
- Control-room screenshots in the [water guide](docs/scenarios/water_reservoirs_drought_guide.md) and the README's
  showcase.

## v0.67.0 — 2026-11-22

### Added
- **A spoken question acts on the screen as a typed one does** ([ADR 0033](docs/adr/0033-push-to-talk-voice.md)): by
  voice the form agent fills the form, a scenario chat draws its chart or table, and the guide opens scenarios, tabs and
  guided demos, then the agent says in a sentence what is now on screen. Each press sends agui-voice the chat's tools,
  readable context, forwarded props and transcript (a `voice-turn` message, checked and capped at 512 KB); a call to
  one of the chat's tools comes back as a `frontend-tool-call` that the chat carries out, and the agent runs once more
  to say what it did (at most three runs per spoken turn). This lifts v0.66.0's known limit.

### Changed
- The spoken instructions (`ai_circus_shared.spoken`) ask the agent to use its screen tools as for a typed question and
  to say what is on screen instead of reading it out.

## v0.66.0 — 2026-11-22

### Added
- **The framework guide** ([ADR 0032](docs/adr/0032-the-apps-guide.md)): a **Guide** button in the top bar opens one
  assistant for the whole framework on every page. It is the architecture tutorial's own chat (same documents,
  citations, entitlement and history), and it can act in the app: rank the gallery for a need, open a scenario on a
  tab, start its guided demo, or open the Architecture page on a component or a request flow. It only opens what the
  user is entitled to, and says why a scenario is unavailable (its service is off or its model is not trained for the
  tenant). On a wide screen the page makes room for it, and a scenario's own chat opens beside it.

### Changed
- **Voice mode is push to talk** ([ADR 0033](docs/adr/0033-push-to-talk-voice.md)): press the mic to talk and press
  again (or hold the space bar) to send; a pause no longer ends the turn. A press while it answers interrupts it, a
  turn is capped at 60 s, the browser sends audio only while talking and hangs up after 3 idle minutes, and a ✕ ends
  voice mode. agui-voice no longer needs the Silero VAD.
- **A spoken question gets a short spoken answer**: agui-voice marks its runs `forwardedProps.spoken`, and the
  assistant, rag-agent and form-agent then answer in two to four plain sentences and offer the detail on screen
  (`ai_circus_shared.spoken`); tools and data are those of a typed turn. An insurance answer that took about two
  minutes to say now takes about 20 s.

### Fixed
- Spoken questions got no answer: the voice bridge ran every agent on the scenario slug as the conversation, which
  each agent has refused (404) since conversation history arrived. The browser now passes the open conversation, so
  spoken turns are answered and stored with the typed ones.
- The browser gave up on a spoken answer after 30 s even while the agent was working; it now waits up to 2 minutes
  once the agent has started.
- The browser's console no longer reports an "Unknown frame kind" when a spoken answer is interrupted.

### Known limits
- A spoken turn has none of the chat's screen actions yet: by voice the form is not filled, no chart is drawn and the
  guide does not open anything ([plan](docs/plans/demo-experience.md#smaller-follow-ups)).

## v0.65.0 — 2026-11-21

### Added
- **How This Framework Is Built — Architecture Tutorial** (`framework_architecture`): ask how the platform works and
  get answers from its own documentation, every passage cited — the architecture guide and component reference, every
  ADR and scenario guide, and which components each scenario uses. A new `architecture_map` tab draws the platform map
  and its request flows next to the chat, and an MCP door serves the same documents.
- **A tabular scenario's model from any MCP client** ([ADR 0031](docs/adr/0031-mcp-doors-on-the-assistant.md)): the
  assistant opens a read-only MCP door on `water_reservoirs_drought` (drought state, storage forecast, what-if, the
  least restrictive measures, the drought plan, data quality, the weekly bulletin), `financial_crime_desk` and
  `insurance_portfolio` (what the model is for, and one record scored and explained, with any business rule that
  decides instead). Each door is in the MCP catalog, so LibreChat and Open WebUI reach it through the MCP gateway with
  the user's own token; the workspace gains a **Connect via MCP** tab, and `make k3s-mcp-portforward` adds
  `localhost:8034`.

### Fixed
- `prediction` answers **422** naming the field, instead of a 500 from inside the model pipeline, for a value it cannot
  read: a word or an infinite number in a numeric feature, or a list or an object anywhere. Nulls, numbers written as
  text, unknown categories and numbers outside a slider's range are still scored as before.

## v0.64.0 — 2026-11-21

### Added
- **Every scenario tells its story** (`story:` in scenario.yaml, any kind): the first tab, **Overview**, gives the
  problem and who decides, today against with this, headline figures, a flow whose boxes open the tabs that show each
  step, where every input comes from (synthetic, public or official), the question each model answers and the limits;
  a **guided demo** bar walks a presenter through the tabs (what to point at, what to say, a question for the
  assistant). All 35 scenarios have one; the Spanish ones are in Spanish.
- **The drought control room** (`water_reservoirs_drought`): each supply system's state against the plan, the basin
  map coloured by storage band with a weekly replay since 2000, the plan's thresholds behind the forecast, a what-if
  (rain scenario, savings, horizon) and the least restrictive package of measures, what drives the forecast, the
  data-quality flags (readings as published next to the cleaned series) and the weekly bulletin. It reads the
  assistant's new read-only `/drought/{slug}/…` routes over the same toolkit as the chat.
- **One architecture catalog** (`architecture_catalog.yaml`, [guide](docs/architecture/README.md)): every component by
  layer, its links and six request flows. It generates the README map and `docs/architecture/`, feeds the app's new
  **Architecture** page (open to every user) and each Overview's **How it's built**, which lights the components a
  scenario uses, derived from its YAML. Tests fail on a workload under `k8s/` missing from it (`make architecture`).
- **Ranked scenario search**: the gallery reads a phrase in English or Spanish and ranks by words and by meaning
  (platform-registry `GET /scenario-search/{org}`, embeddings through llm-gateway's `local-embed`).
- **Executive · Analyst · Engineer** views of a workspace: the story and the showpiece for an executive, everything for
  an analyst, plus the routes, model addresses and commands for an engineer.
- Shared parameter controls: range sliders over a histogram of the real values, a date range, number sliders and
  choice selects, the same for querying data, predicting and what-ifs.

### Changed
- Data & BI reads a forecast's target as a numeric range (it drew one chip per value) and opens a time series on its
  line over time; the forecast tab shows the change of a level instead of a sum, in days, with key dates.
- The README's architecture section embeds the generated map; the three hand-drawn diagrams and their generators are
  removed.

### Fixed
- The drought toolkit is built under a lock and warmed at start-up: two first requests at once raced on its database.

## v0.63.0 — 2026-11-20

### Added
- **Reservoirs & Drought Watch** ([docs](docs/scenarios/water_reservoirs_drought_guide.md)), `water_reservoirs_drought`:
  a fictional water authority's nine reservoirs in four supply systems, forecast 7, 30, 90 and 180 days ahead with
  p10/p50/p90 bands (`lightgbm_quantile`), backtested by year against persistence and seasonal climatology. The
  storage state is read against an illustrative drought plan, with what-if and measure comparison, data-quality flags
  and a weekly bulletin (PDF) from the assistant. Synthetic data from a calibrated, seeded basin generator
  (`scripts/generate_water_data.py`); MAE 4.2 / 10.2 / 16.5 points at 30 / 90 / 180 days against persistence's
  5.6 / 13.2 / 18.7, weaker than persistence at 90 and 180 days in the 2022-2024 drought.
- **A `lightgbm_quantile` forecast candidate**: one direct quantile model per horizon, with past-only features.
- **A forecast board map and labels** (`ui_extras.forecast_board`): an optional schematic `map` (static SVG; a
  click picks the series and outlines its group, each site coloured by where its forecast heads), `series_labels`
  for readable names, and `summary: change` for a level, where a sum means nothing.
- Shared modules other scenarios can reuse: series quality flags, threshold states, decision support, a rulebook
  with cited articles, read-only SQL, traceability, geo helpers and an observation shape.

## v0.62.0 — 2026-11-19

### Added
- **Payment Fraud & Financial Crime** ([docs](docs/scenarios/financial_crime_desk.md)), `financial_crime_desk`: a
  fictional bank's financial-crime desk with three models in one scenario. Transfer screening reads MT103 /
  pacs.008-style messages, fields and free-text purpose, with a sentence-embedding challenger and an LLM analyst
  that reads the message against a red-flag rubric. Payment fraud runs mule-list, sanctions, verification-of-payee
  and step-up rules before the model. Mule accounts scores 800 accounts from 60 days of transfers. An AML Regulation
  document tool. Synthetic data from `scripts/generate_financial_crime.py` and `scripts/generate_financial_crime_mules.py`;
  out-of-fold ROC AUC 0.952, 0.952 and 0.958.
- **A Message screening tab** (`ui_extras: message_screening`): an inbox of messages by out-of-fold score, the open
  one drawn field by field with each field tinted by its SHAP push and the free text word by word, the text
  challenger and the LLM rubric reading side by side, and a typology reveal.
- `rubric_check.reviewer_role` sets who the LLM reads as (default: the leadership coach), and a network explorer's
  `record_label` names what its reveal shows.

### Changed
- A network over 600 nodes gets half the warm-up layout, so it draws sooner and rests within the idle check.

### Fixed
- dnspython 2.9.2 and opentelemetry-api 1.45.2 in llm-gateway and rag-agent, for two new advisories.
- An extra model's view no longer keeps the scenario's `vector_store` without its documents.

## v0.61.0 — 2026-11-19

### Added
- **Insurance Retention & Next Best Offer** ([docs](docs/scenarios/insurance_portfolio.md)), `insurance_portfolio`: a
  fictional multi-line insurer (motor, home, health, life, pet, funeral, accident) with three models in one scenario —
  lapse at renewal, cross-sell propensity and a product-affinity recommender over the products each policyholder holds.
  A Retention watchlist and a Cross-sell leads watchlist; 5,000 synthetic policyholders from
  `scripts/generate_insurance_portfolio.py`, hold-out ROC AUC 0.78 (lapse) and 0.82 (cross-sell).
- **A next best offer in the risk watchlist** (`ui_extras.risk_watchlist.next_best_offer`): the selected entity is
  scored by another classifier of the scenario (propensity to buy, with its SHAP drivers) and the scenario's
  recommender suggests what goes with the products it already holds. The chat sees the same offer. The schema checks
  that both are extra models of the scenario and that the classifier reads the same feature columns.
- An `insurance` domain in the scenario picker.

## v0.60.2 — 2026-11-14

### Fixed
- `make k3s-resume` no longer hangs for minutes on a cluster that is already up: k3d's last start step (a CoreDNS record
  nothing here uses) could stall. The target now waits for the node to be Ready; a resume takes about 90 s. The MCP lite
  profile starts an existing cluster the same way.

### Documentation
- The MCP guide has a diagram of the services the lite profile runs with observability: namespaces, ports and
  connections. The guide and its README section are now titled "MCP Lite Stack".

## v0.60.1 — 2026-11-14

### Documentation
- **A guide to the MCP Lite Stack**, [docs/mcp](docs/mcp/README.md): architecture, the steps of a request, the pieces
  and their decisions (ADR 0019 to 0029), security levels, who sees which identity, how to run it in the framework or
  alone, how to add an API, operations and known limits. The README has a section on it under Architecture.
- A test keeps the guide's links and screenshots valid and checks it cites every MCP decision.

## v0.60.0 — 2026-11-13

### Added
- **Each API sees a pseudonym of the user** (ADR 0029), never the user's Keycloak id: a stable pseudonym per user and
  per API, so two APIs cannot join their logs. Generated from `mcp/catalog.yaml` for every MCP server built with the kit.
  The chat UIs, the gateway and the MCP servers' audit lines keep the real id, so no chat account changes.
- `make k3s-mcp-pseudonym WHO=<user>` shows the pseudonym each API sees for a user; `FIND=<pseudonym>` says who one is.
  It needs Keycloak's admin access.

### Changed
- Open WebUI starts new chats on `gemini-flash`, as LibreChat does.

### Fixed
- A fresh cluster no longer leaves the pseudonyms' secret salt empty: the identity step has Keycloak generate it.

## v0.59.0 — 2026-11-13

### Added
- **One trace from the chat to the API** (ADR 0028): the MCP gateway forwards the caller's trace context to every MCP
  server (never baggage), so a LibreChat question is a single trace through the MCP server, its token exchange and the
  API. Open WebUI sends no trace context with its MCP calls; its hop is still matched through the audit lines.
- `make k3s-mcp-lite-verify` (with `OBS=1`) sends its own trace through the gateway and finds it with the MCP server's
  and the API's spans, and fails if any stored log line carries a token or an email address.

### Changed
- **The log collector masks emails and tokens** in every log line before storing it (`<email>`, `<token>`), including
  lines from third-party software such as the chat UIs, which log the signed-in user's email.

## v0.58.0 — 2026-11-13

### Added
- **Observability in the MCP lite profile** (ADR 0027): `make k3s-mcp-lite OBS=1` adds the observability bundle
  (OpenTelemetry Collector, VictoriaMetrics with alerts, Loki, Tempo, Grafana with the MCP dashboard) to the lite
  cluster, about 0.43 GB more. It always targets the lite cluster, never the framework's.
- `make k3s-mcp-lite-verify` then also checks that its calls reached the bundle: tool-call metrics with both outcomes
  (ok and denied), each kit server's audit lines and traces, and that no log line carries a token.
- LibreChat sends traces when the observability bundle is on (`make k3s-obs-up`, in both profiles): its chat requests
  and the AI gateway form one trace. The hop from the gateway into an MCP server is still matched through audit lines.

## v0.57.0 — 2026-11-13

### Added
- **The MCP lite profile** (ADR 0027): `make k3s-mcp-lite` runs the MCP Lite Stack alone on its own k3d cluster
  (`mcp-lite`, about 2.9 GB): Keycloak, the AI gateway (models and the MCP gateway) with Valkey, the CRM example kit
  server and its API at Level 3, and one chat UI (`CHAT_UI=open-webui`, the default, or `librechat`). It reuses the
  framework's own manifests, never touches the default kube context, and refuses to start while another k3d cluster
  holds port 80 (`make k3s-pause` first, `make k3s-resume` after `make k3s-mcp-lite-down`).
- `make k3s-mcp-lite-verify` checks it without a model: each demo user signs in through Keycloak's login pages, then
  every kit server's tools, a granted call and a refused one are checked against `mcp/catalog.yaml`.
- `make mcp-catalog` also writes the lite profile's chat UI connections, with the kit servers only.

### Fixed
- llm-gateway no longer fails every model call that carries a user id when Valkey is unreachable: the monthly budget
  check is skipped with a warning, as the policy check already was.

## v0.56.0 — 2026-11-13

### Added
- **LibreChat as a second, optional chat UI** (ADR 0026, MIT): `make k3s-librechat-up` serves it with its own MongoDB at
  `http://librechat.localhost` (compose profile `chat-ui-librechat`). Sign-in through Keycloak only (client `librechat`,
  same demo users as Open WebUI); every server of `mcp/catalog.yaml` is reached through the MCP gateway with the
  signed-in user's own token, so the doors and kit servers decide exactly as for Open WebUI. Models from llm-gateway,
  `gemini-flash` by default. Run one chat UI at a time on a 16 GB laptop.
- `make mcp-catalog` also writes LibreChat's MCP servers, and exempts only the gateway from LibreChat's block on private
  addresses. LibreChat and its database have rows on the Health page.

### Changed
- The catalog refuses `{{` in a title or description, as it refuses `$`. LibreChat shows a title without the characters
  it refuses ("Customers (CRM)" reads "Customers CRM").
- `scripts/chat_ui_identity.py` fills a key left empty in `.env` on its own line instead of adding a second one.

### Known limits
- LibreChat's OpenID client refuses an http identity provider; the lab's Keycloak is http, so a small load hook
  (`k8s/chat-ui-librechat/oidc-allow-http.mjs`) turns on the library's own opt-out. Delete it with an https provider.
- After a token refresh, LibreChat's first MCP call is refused once by the gateway (the session belongs to the old
  token); LibreChat reopens the connection and the model retries.

## v0.55.0 — 2026-11-12

### Added
- **Level 3 for regulated MCP servers** (ADR 0025), per kit server with `level: 3` in `mcp/catalog.yaml`; the
  `crm-mcp` + `crm-api` example runs at it. A server at level 3 refuses to start without every part:
  - the per-API token is bound to a key the MCP server holds only in memory (DPoP): a copied token is refused as a
    bearer token, with another key's proof, with a reused proof or with a proof for another request;
  - every user token is checked live at Keycloak on every request, and the API checks its own token the same way:
    signing a user out stops them on their next call, not when the token expires; an unreachable Keycloak is a refusal;
  - mutual TLS between the MCP server and its API, with one certificate authority per pair (`make k3s-mcp-tls`,
    files in `infra/mcp-tls/`, one Secret per workload holding only its own key);
  - the per-API token carries ids only; the API reads roles and row filters from Keycloak.
- Alerts for a revoked session's token used again and for unreachable token checks; dashboard panels for live checks.
- The catalog portal can set a server's security level.

### Changed
- The example `crm-api` serves its API on 8443 (mutual TLS) and only `/healthz` and `/metrics` on 8000, and has its own
  client secret (`CRM_API_CLIENT_SECRET`, generated into `.env` by `make k3s-mcp-examples-up`).

## v0.54.0 — 2026-11-12

### Added
- **One catalog for the MCP path** (ADR 0024): `mcp/catalog.yaml` lists the gateway, every MCP server (a scenario's door, or
  a server in front of an API built with `libs/mcp-kit`) and every group (a realm role and the tools it may run on each kit
  server). `make mcp-catalog` generates the MCP gateway's routes, the chat UI's connections, each kit server's
  `policy.yaml` and the Keycloak clients that let a server exchange a user's token for its own API only. Scenario doors are
  routed but never granted there: their entitlements and roles stay in `scenario.yaml`.
- **Drift and mistakes fail CI**: a generated file that no longer matches the catalog, a tool the server's code does not
  register, a grant on a scenario door, a group that is not a realm role, a forwarded credential header.
- `make k3s-mcp-catalog-apply` puts a catalog change on the running cluster (Keycloak, MCP gateway, kit servers, chat UI).
- **Optional local portal**, `make mcp-catalog-portal` (http://127.0.0.1:8040): tick the tools each group may run, add or
  remove a server, review every generated change as a diff, publish. It never deploys or commits.

### Changed
- The chat UI's and the gateway's server descriptions now come from the catalog, and a kit server's Keycloak secret is
  always `<CLIENT_ID>_CLIENT_SECRET` in `.env` (the example's `CRM_MCP_CLIENT_SECRET` is unchanged).

## v0.53.0 — 2026-11-12

### Added
- **Observability of the MCP path** (ADR 0023), on the existing ADR 0015 stack: every MCP server built with `libs/mcp-kit`
  reports tool calls, refusals, per-API token exchanges and its API's answers as metrics (labelled by server, tool, outcome
  and reason only), and writes trace-linked audit lines in the platform's log shape. One trace runs from the MCP server
  through its Keycloak exchange to the API.
- **"AI Circus · MCP servers" dashboard** (calls, refusals, per-API tokens, latency, the API behind each server, audit lines
  from Loki) and the `ai-circus.mcp` alerts: refused-request spikes, refused token exchanges, tool error rate.
- `make k3s-obs-up` also traces the optional chat UI. The hop from the chat UI and the MCP gateway into an MCP server is
  correlated through the audit lines, not traced, until their MCP clients send `traceparent`.

## v0.52.0 — 2026-11-12

### Added
- **MCP server kit, `libs/mcp-kit`** (ADR 0022): an MCP server in front of an existing API inherits its security, so its own
  code is its tools only. The kit validates the user's token (401 otherwise), applies a `policy.yaml` of tools per role and
  tenant (403 when nothing is granted), exchanges the token at Keycloak for one valid only at its API, hands tools a
  read-only identity and an API client that already holds that token, and writes one audit line per call with ids and
  outcomes only. `mcp_kit.testing` replays the failure cases.
- **Per-API tokens with Keycloak's standard token exchange**: each MCP server may exchange a user's token only for its own
  API, never impersonate, and never get the platform audience.
- **Example pair, optional**: `make k3s-mcp-examples-up` deploys `crm-api` (a customer API that accepts only tokens issued for
  it and refuses requests naming someone else) and `crm-mcp` (built with the kit). The MCP gateway serves it as `/mcp/crm`
  and the chat UI lists "Customers (CRM)": the analyst gets every shop and `top_customers`, the S012 store manager only S012.
  A user's token replayed straight at the API is refused.

## v0.51.0 — 2026-11-12

### Added
- **Optional chat UI with real Keycloak sign-in** (ADR 0021): `make k3s-chat-ui-up` serves Open WebUI at
  `http://chat.localhost` (compose profile `chat-ui`). Users sign in with Keycloak only; models and embeddings go through
  llm-gateway; each scenario's MCP door is one connection to the MCP gateway carrying the signed-in user's own token, so
  the door applies that user's tenant and role. It brings the live realm up to `realm-export.json` and creates a `lite-demo`
  tenant: in the chat, the analyst gets `run_sql` and every store, the S012 store manager neither. LibreChat (MIT) follows
  as a second, strictly open-source chat UI.

### Fixed
- **Real Keycloak sign-ins could not reach any service**: the imported realm declared its own client scopes, so Keycloak
  added none of its built-in ones and tokens had no `sub`, no realm roles and a list-shaped `organization` claim. The realm
  now declares them, adds the organization id and the row-filter claims (`stores`), and a test pins each claim.
- Keycloak hands in-cluster clients in-cluster token and key URLs, while the issuer and login page stay public.

## v0.50.0 — 2026-11-12

### Added
- **One MCP gateway in front of the doors** (ADR 0020): llm-gateway's LiteLLM also serves `/mcp/{slug},{slug}`, one URL for
  rag-agent's and data-api's MCP doors, with tools prefixed by scenario (`surf_retail-query_metric`). Every door is
  `true_passthrough`: the caller's own token reaches the door, which still decides tenant, role and tools, and the gateway
  holds no credential. Open-source LiteLLM features only. With several doors in one URL the client sends one
  `x-mcp-{slug}-authorization` header per door, because LiteLLM never replays one token to several upstreams; a door that
  refuses simply contributes no tools. Contract tests load the config into LiteLLM's own MCP manager, so an upgrade that
  changes this behaviour fails the build.
- `make k3s-mcp-portforward` also forwards the gateway to `localhost:8033`.

## v0.49.0 — 2026-11-11

### Added
- **MCP door on a data product** (ADR 0019, step 2): a `data_product` with `mcp: {expose: […]}` serves its governed data
  tools at data-api's `/mcp/{slug}`: `list_metrics`, `query_metric`, `search_records`, `get_record`, `data_health`,
  `run_sql`. The door settles the caller's access with the REST routes' own code (role, row filter, masks; a platform key
  may send the X-Data-View-As headers) and every tool is a REST route's core call, so MCP and REST cannot drift apart.
  `run_sql` is listed only for roles that may run SQL. First on `surf_retail`: a store manager gets only their store.
- **Connect via MCP on data products**: the tab carries the View-as role into the client snippets and the live check.
- `make k3s-mcp-portforward` also forwards data-api to `localhost:8032` when the Data Platform is up.

## v0.48.0 — 2026-11-11

### Added
- **MCP door on a document scenario** (ADR 0019): a `conversational_rag` scenario with `mcp: {expose: […]}` serves read-only
  tools at rag-agent's `/mcp/{slug}` (FastMCP, Streamable HTTP, stateless): `list_sources`, `search_documents`, `read_document`.
  The entitlement check runs before any MCP handling and the tenant comes from the bearer token; no model runs in the door.
  First on `ai_circus_reference`. A **Connect via MCP** tab shows the endpoint, client snippets and a live `tools/list`.
- **The chat answers through MCP on request**: `mcp: {consume: […]}` adds a **📚 Direct · 🔌 Via MCP** switch under the chat; via
  MCP, rag-agent loads its tools from those doors (only doors it serves, over loopback, with the caller's token, capped in steps)
  and the reply is labelled "🔌 MCP · <server>".
- **The chat lists and reads whole documents**: `list_sources` and `read_document` beside `retrieve_docs`, the same reads the door
  publishes.
- **`make k3s-mcp-portforward`** (optional): rag-agent on `localhost:8031` as a systemd user service, for MCP clients on this
  machine, since `*.localhost` does not resolve for every client.

### Fixed
- **A chat forgot the tools it had used**: the streamed and snapshot copies of a tool result carry different ids, so the client
  put the result after the reply and the next turn's history made the backend drop the call. The model then said it had not used
  the tool, and earlier replies lost their sources. Every chat with backend tools was affected.
- **A reply drawn only as a chart or table had no "Answer based on" row**; the row now goes under the end of every reply.

## v0.47.1 — 2026-11-11

### Security
- **`llm-gateway`: `oauthlib` 4.0.0 → 4.0.1** for GHSA-3jp2-3mrg-2vxc (a bearer token could be retrieved in the device flow by a client
  that knows a valid client id). It comes in through `litellm`'s proxy extra (`fastapi-sso`); `make check-all` failed on it in `uv audit`.

## v0.47.0 — 2026-11-05

### Added
- **`make k3s-vectorize`**: runs the `etl-vectorize` Job, which a fresh cluster needs for RAG, forms and the case desk's reading
  room. About 20 min on CPU, so run it detached.
- **`make dl-artifacts-export` / `dl-artifacts-import`**: move trained deep-learning models between stacks as one tar file, so one
  machine can train on a GPU and another can serve on CPU (`scripts/dl_artifacts.py`). `raw/` stays behind, and the manifest goes last.

### Changed
- **`symptom_triage` is served unquantized**: 600 MB fp32 ONNX, 96.2% on test with the CPU budget. `dl-inference`'s memory limit goes
  from 1536Mi to 3Gi in both deploy paths (measured peak 2.4 GiB).

### Fixed
- **int8 models could fail depending on the CPU**: on an AVX2 CPU without VNNI, the quantized `symptom_triage` scored 4% on test
  against 95% for the same model in torch and fp32 ONNX, consistent with onnxruntime's documented saturation on such CPUs. The export
  now uses `reduce_range` (91.5% on the same model).
- **The case desk's reading room was blank when no documents were indexed**: it now says so and names the command to run.

## v0.46.0 — 2026-11-05

### Added
- **Several models in one scenario** (`extra_models:`, ADR 0018): each model is a validated view of the scenario with its own
  dataset and bucket, addressed as `slug~id` (the bare slug is the main model, so every existing URL is unchanged). Entitlement is
  to the scenario. The assistant gets every model's tools; an extra model's end in `_<id>`.
- **Recommenders** (`model.task_type: recommendation`): association rules and trained item factors, served side by side by
  `GET /recommend/{slug}`, scored on items hidden from held-out baskets next to a popularity baseline. First user `surf_ml~recommender`,
  from a committed sample of anonymous baskets (`scripts/prepare_surf_ml_samples.py`), so it runs without the data tier.

### Changed
- **`energy_building` has its regression back** next to its 24-hour forecast, which replaced it in v0.40.0.
- **`surf_demand_forecast` and `surf_customer_propensity` are now one scenario, `surf_ml`**; `luznova_regional_forecast` is now a model of
  `luznova_regional_demand`. The seed moves a tenant's entitlements to the successor (`RETIRED_SCENARIOS`).
- **ML Predictions and ML Insights are one tab per model**, with a Predictions / Insights switch, in every tabular scenario.
- `make k3s-idle-check` also visits a model tab's Insights, fails a tab on a backend 5xx or 404, and saves screenshots with `SHOT_DIR`.

## v0.45.0 — 2026-11-04

### Added
- **"Model not trained" on scenario cards**: a tabular scenario whose model does not exist yet for the caller's tenant (every one
  on a fresh cluster) is grayed out with "run `make k3s-pipeline`" instead of failing on open. One org-scoped, entitlement-checked
  request, `GET /models/status` on `prediction`, reads only whether the artifacts exist.
- **`make k3s-idle-check`**: opens every tab of the scenarios in `IDLE_SCENARIOS` in headless Chromium and fails if one still draws
  more than 2 frames/s or uses more than 15% CPU while nobody touches it.
- **`make k3s-embed-onnx`** (optional, off by default): runs `local-embed` on ONNX Runtime instead of torch. Measured on the
  cluster after one request: llm-gateway 975 MB against 1,300 MB (-25%), 0.18 s against 0.67 s for five texts. The int8 export's
  vectors differ from torch's (cosine 0.984 to 0.993), so documents already in Qdrant should be re-vectorized after switching;
  `EMBED_VARIANT=off` goes back. The fp32 export is exact but needs 1.4 GB, more than the pod's limit. torch stays in the image
  as the fallback.

### Fixed
- **Data & BI held a CPU core while idle** (found by the new idle check): `DataView` passed fresh arrays to the filter panel on
  every render, which re-ran its effect and re-rendered the view without end (about 290,000 DOM changes in 8 s, 100% CPU, measured
  on churn; the view is shared by every tabular scenario). Idle is now 0%. The same pattern in ML Predictions' batch mode is fixed too.

### Known
- `local-embed` has always served a plain 1024-d Qwen3 backbone (causal attention, no linear head), because the gateway loads
  `voyageai/voyage-4-nano` without `trust_remote_code`; the model card's 2048-d bidirectional model is not what is deployed.
  The ONNX export reproduces what is deployed, so existing vectors stay comparable. Switching to the real model would mean
  re-vectorizing everything and retraining the text challengers; it was not done.

## v0.44.0 — 2026-11-04

### Changed
- **The region and watchlist maps are plain SVG** (`GeoBubbleMap`, `d3-geo`, no frame loop): opening them takes about 85% less
  browser CPU, the 8,196-bank map is 20 DOM nodes instead of 8,299, and they no longer fetch map data from `cdn.plot.ly`.
  Idle was already near zero. The regional map uses a one-hue colour scale, and pan and zoom have a reset button.
  The watchlist's Risk landscape keeps Plotly's WebGL scatter.
- **The data product dashboards no longer load Plotly** (`dashboardCharts.tsx`, `BarList`): opening the tab takes about half the
  browser CPU and a fifth of the download. A panel declared `chart: pie` is drawn as ranked bars with each share beside them.
- ADR 0014 records both sets of measurements. Plotly now serves only the chart builder, the chat's charts, large scatter
  plots and 3-D.

### Fixed
- A dashboard's day, week or month still in progress is left out, with a note, instead of drawn as a sudden collapse.
- The regional map no longer overflows the page on a phone. 39 banks the US map cannot place are now stated under it, and stay
  in the ranked list.

## v0.43.0 — 2026-11-04

### Added
- **vLLM as an optional GPU backend beside Ollama** (ADR 0017): `make k3s-vllm-up` or the `vllm` compose profile, reached
  only through llm-gateway as the `vllm-qwen` model, listed in Settings and the health page, off by default. Its first start
  needs about 33 GB of disk for the image and 3.1 GB for the weights, and it holds the whole GPU while it runs.

### Changed
- ADR 0014 is decided: Plotly stays for the chart builder, large scatter plots and 3-D, with measurements against Vega-Lite.
  New small charts use the SVG primitives and new maps use `d3-geo`.

## v0.42.0 — 2026-11-04

### Added
- **`luznova_regional_forecast`**: the first forecast with many series, 50 Spanish provinces 30 days ahead, from the same
  synthetic data as `luznova_regional_demand`, which stays a regression because its map scores any area under any
  conditions. The backtest selects exponential smoothing, so its temperature lever is off and says why.
- **`row_triage` tab on `steel_defects`**: plates routed to an inspector or automatically by a threshold you move, with the
  real workload and misses. It uses out-of-fold scores, so a tick calls no model.
- **`case_tracker` tab on `service_request` and `sede_electronica`**: a user's own filed cases, with receipt, values and
  filed PDF, through form-agent's new `GET /submissions/{slug}`, scoped by tenant and by user.
- **`source_library` tab on `ai_circus_reference`**: the documents the assistant really searches, through rag-agent's new
  `GET /documents/{slug}` and `/passages`, read from the tenant's own collection.

### Changed
- etl-vectorize stores each chunk's position. Re-run it (`etl-vectorize` Job) so Sources shows passages in order.
- `steel_defects` trains out-of-fold scores: retrain it (`./scripts/k3s_ml_train.sh steel_defects`).

### Fixed
- A forecast scenario can no longer take `region_map`, which the schema wrongly allowed: the map scores through `/predict`,
  which answers 409 for a forecast.
- A numeric static feature of a forecast could arrive as a numpy number, be treated as a category and fail the artifact.

## v0.41.0 — 2026-11-04

### Added
- **`fleet_health` tab**: a fleet of units replaying real dataset rows, scored live by the deployed model. Health is the
  model's score, or actual minus predicted when the target is not itself a health measure. DOM and CSS only, with no
  frame loop. On `turbofan_rul`, `electric_motor`, `luznova_gas_anomaly` and `luznova_ev_charging`.
- **Watchlists on `churn` and `surf_customer_propensity`**, showing customers by id only. `risk_watchlist` gains an
  optional `name_column`, `positive_is_adverse` (a blue ramp when the outcome is good news) and its own wording.

### Changed
- `turbofan_rul` shows `unit_number`, which stays out of the features: rerun its ETL
  (`./scripts/k3s_ml_train.sh turbofan_rul`).

### Fixed
- Pages no longer scroll sideways at phone width: the top bar wraps, the tab strip scrolls inside itself, and Data & BI,
  data tables, sub-tabs and the watchlist fit. Desktop is unchanged.
- Lint errors in the forecasting files v0.40.0 shipped with (`make check` failed for `prediction` and `training`). The
  pre-commit hook skips untracked files, so they went unchecked. No behaviour change.

## v0.40.0 — 2026-11-04

### Added
- **Time-series forecasting** as a third tabular task (`model.task_type: forecast` + `time_series:`, ADR 0016), with no
  new service. Training backtests seasonal naive, exponential smoothing and LightGBM on lags on past cut-offs and
  selects by MASE under the Green Code rule, with conformal prediction bands.
- `prediction` serves `GET /forecast/{slug}`, `POST /forecast/{slug}/what-if` and `GET /model/{slug}/backtest`.
- The assistant gets `get_forecast` and `get_forecast_backtest` on a forecast scenario.
- ui-react: forecast variants of ML Predictions and ML Insights, and the opt-in `forecast_board` what-if tab.
- The gallery's *Time Series* filter now finds real scenarios; it used to test for a scenario kind nothing produced.

### Changed
- **`energy_building` is now a forecast**: a home's appliance energy, hourly, 24 hours ahead. It is re-seeded from the
  10-minute sample under a new raw object key and needs retraining (`make k3s-pipeline`, or
  `./scripts/k3s_ml_train.sh energy_building`). Its `/predict`, dataset evaluation and explainability now answer 409.
- `training` depends on statsmodels. mlforecast and statsforecast were the first choice but pin `pandas<3`.

## v0.39.0 — 2026-11-03

### Added
- **Data drift in the app**: every tabular scenario's ML Insights tab opens with a *Data drift* card — Stable /
  Moderate / Significant shift, the output's PSI, the most shifted input and a bar per profiled input — over the
  tenant's own recent scoring traffic, or why there is none yet (a model trained before v0.38.0; too few scored
  rows). Served by `prediction`'s new `GET /model/{slug}/drift`.
- The admin Platform page lists the observability bundle's `grafana` (console link) and `otel-collector` as
  optional Observability rows.

### Security
- Drift windows are kept per tenant: tenants without a model of their own share the fallback one, and a shared
  window would have shown one tenant statistics of another's traffic. The `/metrics` gauges still sum every
  tenant of a model, with no tenant label.

### Fixed
- The Platform page no longer offers console links for tools that are stopped or not deployed.
- `prediction`'s decision-record test failed at random (its `f1` leak marker also occurs in the event's UUID).

## v0.38.0 — 2026-11-03

### Added
- The AI-specific observability pieces ([`observability/`](observability/README.md), ADR 0015): an OpenTelemetry
  collector config (OTLP in, pod JSON logs tailed, redaction again), Grafana dashboards — **LLM gateway** (calls,
  tokens, cost, latency, time to first token), **GPU**, **Model drift** — generated by
  `scripts/generate_observability_dashboards.py`, and Prometheus rule groups (slow/expensive/silent LLM calls, 5xx
  rate, service down, model drift, GPU heat and memory).
- An optional observability lab bundle for clusters with no platform stack: `make k3s-obs-up` / `k3s-obs-down`
  (collector, VictoriaMetrics + vmalert, Loki, Tempo, Grafana at http://grafana.localhost; off by default, ~700 MiB
  measured, 24 h retention). Services are restarted one at a time to keep a laptop's memory in check.
- Model drift: `training` writes a `drift_reference` (top 8 features by SHAP and the served score) into the model
  metadata; `prediction` exports the Population Stability Index of live traffic against it on /metrics. Models
  trained before this release report none until retrained (`make k3s-pipeline`).
- llm-gateway exports OpenTelemetry GenAI metrics (dimensions: operation, provider, model only).
- `data-platform-manager` publishes its NVML GPU reading under NVIDIA DCGM exporter names (`source="nvml"`) where
  DCGM cannot run (WSL2, consumer cards).
- FastAPI pods carry `prometheus.io/*` scrape annotations.

### Security
- llm-gateway's platform spans drop LiteLLM's request metadata (`metadata.*`, `hidden_params`: requester IP and
  headers, user e-mail, API-key hash) and carry the `gen_ai.*`/`aicf.*` attributes instead.

### Fixed
- llm-gateway tests share one event loop and reset LiteLLM's global callback registry, so tracing test modules no
  longer interfere with each other.

## v0.37.0 — 2026-11-03

### Added
- Observability as a platform contract ([ADR 0015](docs/adr/0015-observability-platform-contract.md)): the
  metrics/logs/traces stack (collector, Prometheus-compatible store, Loki, Tempo, Grafana) is a prerequisite the
  cluster provides, never shipped in `k8s/base`. Each service keeps `/metrics` and now:
  - exports **OTLP traces** when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (`ai_circus_shared.telemetry.configure_tracing`:
    FastAPI server and httpx client spans, `traceparent` carried to llm-gateway) — off by default, and the framework
    imports nothing OpenTelemetry while off;
  - writes **JSON logs** (`LOG_FORMAT=json`, set in k8s `shared-config`) with `trace_id`/`span_id`.
- llm-gateway joins the same traces through LiteLLM's `otel` callback, always without message content
  (`message_logging: false`); Langfuse keeps the prompts, linked by the same trace id.
- The layers diagram reads left to right, over a platform observability band.

### Security
- Exported spans are content-free: `RedactingSpanExporter` drops prompt, completion, message, SQL-text, body and
  `enduser.*` attributes and strips URL query strings before anything leaves a pod.
- FastAPI's own `OTEL_*` auto-export is switched off on every app — verified on k3s, it otherwise attached a second,
  unredacted exporter (each span went out twice, once with its query string) and a log exporter carrying exception
  messages and rejected input.

### Changed
- The OpenTelemetry SDK is a dependency of every service image (~10 MB). redis-py imports it whenever installed:
  ~4 MiB of RSS per Python process even with tracing off (measured on `prediction`).

## v0.36.0 — 2026-11-03

### Added
- Scenario gallery: a scenario whose backend is not running (scaled to 0 by `k3s-demo`/`k3s-all-lite`, or an
  overlay never deployed) is grayed out with "<service> is not started" and the command to start it, instead of
  failing with "Failed to fetch" once opened. The check is each service's `/healthz` through Traefik (one shared
  30 s poll, paused while the tab is hidden); admins get a Start button for `dl-inference`. A down assistant only
  adds a "Chat unavailable" chip.
- ADR 0014 (proposed): Vega-Lite as the app's one chart grammar (Plotly to be phased out), Superset kept for BI,
  Power BI reached through the gold marts and Deneb-ready specs.

### Changed
- Every animated canvas outside the network scenes (shipment globe, Dispatch Tower map and landscape, case-desk
  circuit) runs on one loop, `ui-react/src/sceneLoop.ts`: decorative motion rests 15 s after the last input (the
  globe's spin 30 s), nothing draws while the page or canvas is hidden, and expensive frames lower the frame rate.
  Idle animation frames drop from 60/s to 0 on these tabs; the case-desk circuit no longer redraws forever.

## v0.35.1 — 2026-11-03

### Fixed
- `RTVI_DEBUG` (the v0.35.0 opt-in for agui-voice's RTVI message logging) now reaches the pod: it is passed by
  `docker-compose.yml` and the k3s `agui-voice-secrets`, and documented in `.env.example`.

## v0.35.0 — 2026-11-03

### Security
- Governed SQL (`data-api`): a role's hidden columns are now absent from the tenant-filtered subquery itself, so
  no spelling (`EMAIL`, `to_jsonb(t)`, `t::text`, `SELECT *`) can reach them; the name check is case-insensitive.
- Auth: a token belonging to more than one Keycloak organization is rejected (the tenant is no longer "the first
  claim"); malformed organization claims and non-ASCII bearer values no longer cause a 500.
- Tenant ids and path segments are validated with `\Z` (a trailing newline no longer passes); Qdrant collection
  names are built only from a validated org id.
- Prompt injection: retrieved documents, catalog entries and knowledge-graph lines are wrapped by
  `wrap_untrusted`, which defangs any delimiter inside the text and keeps `source` inside its attribute.
- Optional model-artifact signing: with `MODEL_SIGNING_KEY` set (training + prediction only), `training` signs each
  model's checksums with HMAC-SHA256 and `prediction` refuses a model whose signature is missing or wrong. Off by
  default; set it before `make k3s-pipeline`.
- Public deployments (`DEPLOYMENT_TARGET=public`) also refuse the other shipped `change-me…` secrets; agui-voice's
  RTVI message logging is opt-in (`RTVI_DEBUG=true`); Keycloak brute-force protection in the realm export.
- Request bodies are bounded (conversation titles, dl-inference text/image/ids, registry model and voice names).
- `ObjectStore.exists()` only answers "absent" for a real not-found; a 403/5xx now raises instead of reading as
  missing, and concurrent bucket creation no longer fails the loser.

### Added
- `SECURITY.md` and `CONTRIBUTING.md`; Dependabot covers data-api, data-ingest, data-transform, GitHub Actions and
  base images; CI lints the UI and reports `npm audit` (informational).

### Fixed
- Bumped `pyasn1` and `pypdf` to clear `uv audit` CVEs.
- The entitlement TTL cache evicts the least recently written entry, not a freshly refreshed one.

## v0.34.3 — 2026-10-30

### Added
- README hero: a 2:54 narrated Tron-style pitch video (`docs/media/ai-open-framework-tron-pitch.mp4`, 720p, with an
  animated WebP preview) recorded on the real app — the Angel's Surf Co. data platform, the delivery globe, the
  money-laundering map, pneumonia and wildfire-smoke detection, an assisted tax form, the social-aid desk, and the
  platform's health, gateway and audit trail.

## v0.34.2 — 2026-10-30

### Fixed
- CI: every `service-check` job failed since the runner's `uv` started installing Python 3.15, for which
  `confluent-kafka` has no wheel. The workflow now pins `UV_PYTHON: "3.14"`, the version the services and their
  Dockerfiles target.

## v0.34.1 — 2026-10-30

### Changed
- README: a three-layer architecture overview (`docs/screenshots/architecture-layers.svg`, generated by
  `scripts/generate_layers_diagram.py`) — consumption, data platform, AI gateway & governance — a full Angel's Surf Co.
  section in the platform tour, and the Governance tab in the Platform tour.

## v0.34.0 — 2026-10-30

### Added
- **Platform → Governance** tab in the admin Platform page (`PlatformGovernance.tsx`) backed by
  `GET /governance/overview` on platform-registry: switches on/off, the AI systems with their declared EU AI Act risk
  and human oversight, the audit trail with a chain-verified badge, and an AI-BOM download. Read-only, content-free,
  and it answers while every governance feature is off.

### Changed
- New `k3s-data-demo` / `k3s-data-demo-off` targets: every scenario kind plus the core, analytics and bi data tiers,
  with governance, MLflow and voice off (about 8.8 of 11.7 GiB measured).
- The simulator ADR is renumbered 0013 (0011 stays the governance ADR).
- Docs: README governance section, tier table with demos and memory, `docs/scenarios/surf_retail.md`, a regenerated
  data-products diagram.

## v0.33.1 — 2026-10-30

### Fixed
- `prediction`: the decision-record metadata is typed as a mapping of any, so `make check` passes.
- `libs/shared`: three lint errors in `data_product` cleared so `make check-all` passes.

## v0.33.0 — 2026-10-30

### Added
- **AI-BOM** — the AI inventory exported as CycloneDX 1.6 at `GET /governance/inventory`
  (`GOVERNANCE_INVENTORY_ENABLED`, off by default).
- Governance documentation: [docs/governance](docs/governance/README.md) with the standards
  crosswalk, plus the roadmap status in the admin Platform → Capabilities tab.

## v0.32.0 — 2026-10-29

### Added — governance-ready by standards ([ADR 0011](docs/adr/0011-governance-ready-by-standards.md))
Everything is off by default and changes nothing when off.
- Declared AI-system metadata per scenario with a stable `ai_system_id` (never auto-classified).
- Content-free **CloudEvents** (log or HMAC-signed webhook with an outbox) and a **hash-chained
  admin audit trail** (`/governance/audit`, `/audit/verify`).
- A pluggable **policy decision point** enforced at `llm-gateway` (noop / static / OPA / HTTP, with
  an explicit `POLICY_FAIL_MODE` and an HMAC invalidation webhook).
- `gen_ai.*` + `aicf.*` trace attributes (semconv v1.37.0) and content-free **decision records**
  with enforced retention for non-LLM predictions.

## v0.31.0 — 2026-10-29

### Added — Data Platform and the `data_product` scenario kind ([docs/data-platform](docs/data-platform/README.md))
- A fifth scenario kind, `data_product`, and the generic services `data-ingest`, `data-transform`
  and `data-api` on four switchable k8s tiers (core, analytics, bi, governance; never part of
  `make k3s-all`).
- The `assistant` answers data products through governed data, catalogue and ML tools.
- **Angel's Surf Co.** (`surf_retail`, fictional): a web shop, the Surf HQ control room, a guided
  tour, and two ML scenarios trained on its gold marts (`surf_demand_forecast`,
  `surf_customer_propensity`). See [docs/scenarios/surf_retail.md](docs/scenarios/surf_retail.md).

### Fixed
- `etl-tabular` and `training` jobs honour `SCENARIOS`.
- Dependency advisories cleared (langgraph, filelock, dnspython).
