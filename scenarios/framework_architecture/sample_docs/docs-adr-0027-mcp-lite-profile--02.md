# 0027 — The MCP lite profile: the MCP Lite Stack alone, inside the framework

Source: docs/adr/0027-mcp-lite-profile.md (this repository)

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
- **A standalone repository stays possible** and becomes mechanical: the profile's file
