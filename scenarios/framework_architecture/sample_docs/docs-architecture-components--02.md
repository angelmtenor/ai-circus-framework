# Components and request flows - Gateways and governance

Source: docs/architecture/components.md (this repository)

Every door into the platform and every check on the way: the ingress, sign-in, entitlements, the gateway every model and agent call passes, and the admin control plane.

| Component | What it does | Technology | How to switch it on | ADR |
|---|---|---|---|---|
| **Traefik** | The only way in from a browser: *.localhost hostnames to each service. The registry, gateway, Qdrant and Valkey are never exposed. | Traefik ingress | always on | — |
| **Keycloak** | Sign-in and tenants: a tenant is a Keycloak Organization, a scenario is a realm role; per-API token exchange for MCP servers. | Keycloak, OIDC | always on | [0021](../adr/0021-chat-ui-with-real-keycloak-sign-in.md), [0022](../adr/0022-mcp-server-kit-and-per-api-tokens.md) |
| **platform-registry** | Tenants, scenarios and entitlements, checked by every service before it answers; the audit trail and the AI-BOM. | FastAPI · SQLAlchemy | always on | [0008](../adr/0008-tenancy-and-access-policy.md), [0011](../adr/0011-governance-ready-by-standards.md) |
| **llm-gateway** | Every model call and every MCP tool call: routing, rate limits, per-tenant budgets, the policy decision point, the local embedding model and the MCP gateway. | LiteLLM proxy · sentence-transformers | always on | [0017](../adr/0017-vllm-alongside-ollama.md), [0020](../adr/0020-mcp-gateway-without-enterprise.md), [0024](../adr/0024-one-mcp-catalog.md) |
| **MCP server kit (crm-mcp)** | An MCP server in front of an existing API: validates the token, applies the tool policy, exchanges the token for the API's own, writes an audit line per call. | libs/mcp-kit · FastMCP | make k3s-mcp-examples-up | [0022](../adr/0022-mcp-server-kit-and-per-api-tokens.md), [0023](../adr/0023-observability-of-the-mcp-path.md), [0025](../adr/0025-level-3-regulated-mcp-servers.md), [0029](../adr/0029-pseudonymous-users-at-the-apis.md) |
| **Open Policy Agent** | An optional policy decision point the gateway can ask before a model call, with the policy as code. | OPA, Rego | make k3s-governance-opa-up | [0011](../adr/0011-governance-ready-by-standards.md) |
| **data-platform-manager** | The admin control plane behind the Platform page: health, resources, start and stop of optional workloads, the capability roadmap and governance switches. | FastAPI · Kubernetes API | always on | — |
