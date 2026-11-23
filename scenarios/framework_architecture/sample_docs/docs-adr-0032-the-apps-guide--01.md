# 0032 — The app's guide: the architecture tutorial's agent, with the app's own actions

Source: docs/adr/0032-the-apps-guide.md (this repository)

Builds on ADR 0019 (MCP doors) and the architecture tutorial scenario (`framework_architecture`).

**Context.** A newcomer opens the gallery and faces three dozen scenarios, an Architecture page and, inside each
scenario, its own tabs, audience switch and guided demo. Every chat in the app belongs to one scenario. What was
missing is one assistant for the whole framework that answers how it is built and that can also act: find the right
scenario, open it on the right tab, start its guided demo, show a request flow on the map. The plan left two options
open: a new `conversational_rag` scenario that consumes the tutorial's MCP door, or a small agent of its own in
rag-agent.
