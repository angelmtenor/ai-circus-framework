# MCP Lite Stack - Architecture

Source: docs/mcp/README.md (this repository)

```mermaid
%%{init: {"theme": "base", "themeVariables": {"background": "#05070f", "primaryColor": "#0b1224", "primaryTextColor": "#dcecf7", "primaryBorderColor": "#33c7ff", "lineColor": "#33c7ff", "secondaryColor": "#101b36", "tertiaryColor": "#0b1224", "clusterBkg": "#05070f", "clusterBorder": "#2c5a8a", "edgeLabelBackground": "#0b1224", "fontFamily": "Segoe UI, sans-serif"}}}%%
flowchart LR
  U([Person in a browser]) --> CUI[Chat UI<br/>Open WebUI or LibreChat]
  CUI -. 1 sign in .-> KC[(Keycloak<br/>roles · tenant)]
  CUI -- 2 same token --> GW[AI gateway · LiteLLM<br/>/v1 models · /mcp tools]
  GW --> LLM[Model providers]
  subgraph SERVERS[MCP servers]
    DOOR[Scenario doors<br/>documents · governed data]
    KIT[Kit server · crm-mcp<br/>role → tools · audit line]
  end
  GW -- 3 --> DOOR
  GW -- 3 --> KIT
  KIT -. 4 token for this API only .-> KC
  KIT -- 5 mutual TLS --> API[crm-api<br/>sees a pseudonym]
  SERVERS -. metrics · audit logs · traces .-> OBS[Observability<br/>Grafana MCP dashboard]
  classDef id fill:#2a2410,stroke:#ffcf4d,color:#f3fbff
  classDef api fill:#1d1236,stroke:#aa3bff,color:#f3fbff
  class KC id
  class API api
```

1. **Sign in.** Keycloak checks the password and issues a token carrying the person's realm roles and tenant
   (Keycloak Organization). Neither chat UI has local accounts.
2. **Ask.** The chat UI sends that same token to the gateway, one connection per MCP server, so one
   `Authorization` header only ever reaches one server.
3. **Filter.** The server checks the token and lists only what the role is granted. A scenario door asks the
   platform-registry for the tenant's entitlement and applies the scenario's own role rules; a kit server applies the
   catalog's grants. Every call writes an audit line.
4. **Exchange.** A kit server trades the user's token at Keycloak for one valid only at its API (RFC 8693). At
   Level 3 that token is bound to a key the server holds in memory (DPoP) and both tokens are checked live.
5. **Answer.** The API applies its own rules (here: only the shops that person may see) and sees the user under a
   pseudonym of its own, never the Keycloak id.

### Services and ports

What `make k3s-mcp-lite OBS=1` runs (the same services run in the full framework), with each Kubernetes service's
port: one chat UI at a time, Open WebUI or LibreChat with its MongoDB.

<p align="center"><img src="../screenshots/architecture-mcp-lite.svg" alt="Services of the MCP lite profile with observability: Traefik; namespace ai-circus with the chat UI, LibreChat MongoDB, Keycloak and Postgres, llm-gateway and Valkey, crm-mcp and crm-api; namespace observability with the OpenTelemetry Collector, VictoriaMetrics, vmalert, Loki, Tempo and Grafana" width="100%"></p>
