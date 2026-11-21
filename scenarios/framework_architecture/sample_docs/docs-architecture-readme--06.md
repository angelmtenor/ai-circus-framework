# Architecture guide - Security and tenancy on every hop

Source: docs/architecture/README.md (this repository)

- **Every service checks for itself.** The token and the entitlement are checked by the service that answers, never
  only by the UI.
- **Every read is scoped by tenant**: object-store keys, Qdrant collections and database rows carry the tenant.
- **Every model call goes through `llm-gateway`**, embeddings included (a test enforces it), so limits, budgets,
  policy and traces apply to all of them.
- **MCP**: an external chat reaches a tool only through the MCP gateway with the user's own token; a kit server
  exchanges it for a token valid only for its API, and the API sees a pseudonym ([ADR 0022](../adr/0022-mcp-server-kit-and-per-api-tokens.md), [0029](../adr/0029-pseudonymous-users-at-the-apis.md)).
- **Logs and traces carry no content**: no prompts, no personal data; the collector masks tokens and emails.
