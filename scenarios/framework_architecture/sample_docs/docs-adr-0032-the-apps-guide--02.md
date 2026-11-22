# 0032 — The app's guide: the architecture tutorial's agent, with the app's own actions

Source: docs/adr/0032-the-apps-guide.md (this repository)

**Consequences.**
- One assistant answers "how is it built" and "show me", with sources and with visible actions (a chip under the reply
  says what was opened, and the reply's provenance says "An action in the app").
- The guide costs one context of about 2,000 tokens per question (the scenario list and the catalog ids), sent with
  every turn; it grows with the gallery, so a much larger catalogue would need a search tool instead of a list.
- The guide cannot ask a scenario's own assistant, run a prediction or change data: actions only navigate. A tenant not
  entitled to the tutorial has no guide; another `conversational_rag` scenario can back it at build time
  (`VITE_GUIDE_SCENARIO`).
- New documents (this ADR included) reach the guide's answers after `make architecture` and an `etl-vectorize` run.
