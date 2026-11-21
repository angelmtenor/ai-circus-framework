# 0027 — The MCP lite profile: the MCP Lite Stack alone, inside the framework

Source: docs/adr/0027-mcp-lite-profile.md (this repository)

- **A gateway that survives its store.** Running without Valkey first showed that llm-gateway's budget hook failed
  every model call carrying the OpenAI `user` field (LibreChat sends it, as the framework's agents do) when Valkey was
  unreachable: a 500 instead of a skipped budget check. It now logs a warning and lets the call through, as the policy
  hook already did with its counters, with short socket timeouts so a hanging Valkey cannot stall the proxy. The
  profile includes Valkey, since the gateway's budgets and policies live there.

**Verified** on a fresh k3d cluster with the framework cluster paused. `make k3s-mcp-lite` built it from nothing
(Keycloak imported the realm from its image; the identity step added the demo tenant) and `make k3s-mcp-lite-verify`
passed with both chat UIs' clients: the analyst lists and runs the CRM server's three tools, the store manager two,
`top_customers` is refused by crm-mcp's policy (its audit line says `denied`), and nothing is listed without a token.
In a headless browser, both users signed in through Keycloak to Open WebUI and to LibreChat, each listing only the
CRM server, and their questions ran `top_customers` (analyst) and `search_customers` (manager) under their own
subjects. Re-running the target on the same cluster restarts what it rebuilt; switching `CHAT_UI` removes the other
chat UI. Memory with LibreChat: about 2.9 GB of pods (llm-gateway 1.2 GB with its embedding model, Keycloak 0.7 GB,
LibreChat and its MongoDB 0.66 GB), 4.9 GB for the whole node; Open WebUI instead of LibreChat is about 0.5 GB more
on a first start. The default kubeconfig's context stayed on the framework cluster throughout.

With `OBS=1` (fresh cluster, LibreChat): the verify step found the calls' metrics (`ok` and `denied` outcomes), 12
crm-mcp audit lines, 20 crm-mcp traces and no token in any log line (Loki held logs of crm-api, crm-mcp, LibreChat,
llm-gateway, MongoDB and Postgres; Keycloak and Valkey log nothing while idle). Grafana's MCP dashboard showed the
calls, the refusal outside the grant, the per-API tokens issued, the exchange round trip and the live token checks.
LibreChat's model requests and llm-gateway's spans form one trace; the MCP hop does not yet (the gateway's `tools/call`
and crm-mcp each start their own trace, ADR 0023). The bundle added 0.43 GB: about 3.1 GB of pods in all.

**Consequences.** One more way to run the framework, never part of `k3s-all`, that proves the MCP Lite Stack stands
alone and fits a laptop next to nothing else. Adding an API to it is one catalog entry plus one kit server, as in the
full framework. The blueprint's "ship as a standalone repository" is deferred, not dropped.
