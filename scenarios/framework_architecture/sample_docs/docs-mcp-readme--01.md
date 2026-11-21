# MCP Lite Stack

Source: docs/mcp/README.md (this repository)

A chat UI where each person signs in with Keycloak and uses MCP tools as themselves: every MCP server sees the
signed-in user's own token, lists only the tools that user's role is granted, and calls its API with a token valid for
that API alone. One file, [`mcp/catalog.yaml`](../../mcp/catalog.yaml), decides which servers exist and who may run
what. Everything here is optional and off by default; the AI gateway is open-source LiteLLM only.

It runs inside the full framework, or alone as the **MCP lite profile** (`make k3s-mcp-lite`, about 3 GB), which
proves it needs nothing else of the framework. Each decision below is an ADR under [`docs/adr/`](../adr/), 0019 to 0029.
