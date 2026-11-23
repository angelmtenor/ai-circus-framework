# 0032 — The app's guide: the architecture tutorial's agent, with the app's own actions

Source: docs/adr/0032-the-apps-guide.md (this repository)

**Decision.**
- **Neither: the guide is the architecture tutorial's own agent.** The web app talks to rag-agent's
  `/agui/framework_architecture` (`config.guideScenarioSlug`), so the guide answers from the same documents (the
  generated snapshot of this repository's docs, ADRs and scenario guides, plus which components each scenario uses),
  with the same citations, persistence and budget. No new scenario, service, index or schema field: a second scenario
  would need a second vectorize run (about 20 minutes on CPU) or a schema change for a scenario without documents, and
  a gallery card that is not a scenario.
- **The actions are frontend tools**, declared by the guide's own `<CopilotKit>` provider with `useCopilotAction`, the
  same path as the chat's charts: `search_scenarios(query)` ranks the gallery by words and meaning,
  `open_scenario(slug, tab?)` opens a scenario or switches tab, `start_guided_demo(slug)` opens it on its Overview and
  starts the demo, `open_architecture(component?, flow?)` opens the Architecture page on a component or a request flow,
  and `ask_scenario_assistant(slug, question)` opens a scenario and puts a question to its own assistant in its chat,
  as the scenario's Ask buttons do (every kind but deep learning, whose workspace has no chat).
  CopilotKit's middleware ends the run at such a call; ChatPanel hands it to the guide, which hands it to App.
- **What the model sees is the app's context, as data.** Three readables: the scenarios this user can open (slug,
  title, kind, domain, headline, whether it has a guided demo), where the user is (with the open workspace's tabs), and
  the Architecture page's component and flow ids. The open workspace publishes its tabs through a small store
  (`guideBridge.ts`) that every workspace already reaches through `useStoryDemo` (TabularView calls it directly).
- **rag-agent adds only instructions.** `forwardedProps.guide: true` appends how to use app actions to the system
  prompt: only listed slugs, tabs and ids; one action per reply, announced in one sentence; short answers. The
  document tools and the identity chain do not change.
- **Bounded by the gallery, checked three times.** The tools' `enum`s carry only this user's slugs; App opens a slug
  only if it is in the user's entitled list and a workspace opens a tab only if it published it; each workspace's own
  backend checks the entitlement again when it loads, and the scenario's own agent when it answers a question the
  guide put to it (with the user's token, as for a typed question). A scenario the gallery shows but will not open (its service is
  off, or its model is not trained for the tenant: the same shared checks as the gallery) is marked unavailable in the
  context and refused, with the reason. The guide shows only to a tenant entitled to the tutorial.
- **Its conversations are the tutorial's,** titled "🧭 Framework guide" (a set title is never replaced by the first
  message): the guide resumes the newest one, and its history also appears in the tutorial's own sidebar.
- **A side panel, not a modal.** Opened from the top bar on every page, fetched on first open (CopilotKit stays out
  of the entry chunk), then kept mounted while hidden so the conversation survives; on a wide screen the page makes
  room for it (the top bar wraps) and, from 1,400 px, a scenario's own chat opens beside it rather than over it.
  Nothing animates.
