# 0027 — The MCP lite profile: the MCP Lite Stack alone, inside the framework

Builds on ADR 0020 (the MCP gateway), 0021 and 0026 (the chat UIs), 0022 (the kit), 0024 (the catalog) and 0025
(Level 3). Revises the MCP Lite Stack blueprint's last step.

**Context.** The blueprint's placement was "prove it in the framework, ship it as a standalone repository": generate
a separate repository (catalog, generator, kit, servers, deploy files) from the framework's sources. With every step
of the build plan done (gateway, identity, kit, observability, catalog and portal, Level 3, both chat UIs), the open
question was whether that repository is worth its cost now. It needs an export script, a drift test between the two
trees and a second set of deploy files, and nobody is receiving it yet. What it would prove, that the MCP path works
with nothing else of the framework running, can be proved here. The only coupling found is about 330 lines of
telemetry helpers the example pair takes from `libs/shared`.

**Decision.**
- **A profile, not a repository.** `make k3s-mcp-lite` deploys the MCP Lite Stack alone on its own k3d cluster
  (`MCP_LITE_CLUSTER`, default `mcp-lite`): Postgres and Keycloak, llm-gateway (the AI gateway: models at `/v1`, the
  MCP gateway at `/mcp`) with Valkey (its budget and policy counters), the example kit server and its API (`crm-mcp` + `crm-api`, Level 3) and one chat UI
  (`CHAT_UI=open-webui`, the default, or `librechat`). No registry, scenarios, data platform or Langfuse.
- **The framework's own manifests, never copies.** `k8s/mcp-lite/` lists files of `k8s/base` and the
  `k8s/mcp-examples` and chat UI folders, so it is built with `kubectl kustomize --load-restrictor
  LoadRestrictionsNone`. Its only patch switches Langfuse tracing off in llm-gateway (no keys, no exporter).
- **Kit servers only.** A scenario door needs the framework's registry and data stack, so the profile's chat UIs list
  the catalog's kit servers only: the generator writes `k8s/mcp-lite/open-webui/connections.yaml` (a patch over
  `k8s/chat-ui`) and `k8s/mcp-lite/librechat/librechat-config.yaml` (LibreChat's file with the kit servers' block),
  and CI fails on drift as for every other generated file. The gateway keeps its door routes (one image); they
  answer nothing in the profile.
- **Its own cluster and kubeconfig.** `scripts/k3s_mcp_lite.sh` creates the cluster without touching the default
  kubeconfig or its current context (`k3d kubeconfig write`), so a framework command never lands on the wrong
  cluster. The cluster holds host port 80 as the framework's does: the script refuses to start while another k3d
  cluster runs and names the `make k3s-pause` that frees the port. `make k3s-mcp-lite-down` deletes the cluster.
- **Identity as in the framework.** `scripts/chat_ui_identity.py --keys-only` writes the missing `.env` keys before
  any Secret exists; after Keycloak starts, `--no-entitlements` syncs the realm and the demo tenant and skips the
  registry, which the profile does not have.
- **A check without a model.** `make k3s-mcp-lite-verify` (`scripts/mcp_lite_check.py`) signs each demo user in
  through Keycloak's real login pages with the chat UI's own client (code + PKCE), then, through the gateway with
  that token, checks every kit server against the catalog: the tools listed are exactly the role's grants, a granted
  tool runs (through the exchange, DPoP and mutual TLS of Level 3), a tool the role lacks is refused, and without a
  token nothing is listed. Keycloak marks its session cookies Secure and browsers send them to `http://*.localhost`;
  the check carries them the same way. Chat answers depend on a model and a provider key, so they are checked in a
  browser, not here.
- **Observability is one switch away** (added after v0.57.0). `make k3s-mcp-lite OBS=1` runs the framework's own
  bundle target (`make k3s-obs-up`: OpenTelemetry Collector, VictoriaMetrics + vmalert, Loki, Tempo, Grafana with the
  MCP dashboard and alerts of ADR 0023) against the lite cluster's kubeconfig. That target acts on the current
  context, so it must never run bare while the lite cluster is up. LibreChat now traces too (its own OpenTelemetry
  switch: HTTP spans without headers, query strings redacted, and it passes the trace context on to the gateway);
  `k3s-obs-up` wires it in the full framework as well. With the bundle deployed, `make k3s-mcp-lite-verify` also
  checks that the calls it made reached all three stores (the kit's tool-call metrics with both outcomes, ok and
  denied; each kit server's audit lines; its traces) and that no log line of the cluster carries a token, the
  blueprint's test for observability ("a search for tokens or personal data in all signals finds nothing", here
  for tokens).
- **A standalone repository stays possible** and becomes mechanical: the profile's file list is its content, plus
  the telemetry helpers. It is built when someone is to receive it.

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
