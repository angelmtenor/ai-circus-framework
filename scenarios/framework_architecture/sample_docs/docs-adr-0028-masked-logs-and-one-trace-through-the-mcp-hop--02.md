# 0028 — Masked logs and one trace through the MCP hop

Source: docs/adr/0028-masked-logs-and-one-trace-through-the-mcp-hop.md (this repository)

**Consequences.** The blueprint's observability test ("a search for tokens or personal data in all signals finds
nothing") holds for every log line, not only the framework's. Open WebUI's MCP client sends no trace context, so its
MCP hop stays correlated through audit lines; LibreChat's is one trace. A platform that merges this collector
snippet into its own collector gets the masking too.
